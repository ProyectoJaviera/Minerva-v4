"""El corredor de las pruebas en vivo: escucha el WebSocket publico y opera en paper.

Un solo proceso atiende todas las pruebas activas de `history/live_v3.db`:

1. Se suscribe al canal `trade` de las monedas de las pruebas (mas BTC, que usan dos filtros).
2. Con cada tick: lo graba, actualiza la vela en curso de cada marco, revisa las posiciones
   abiertas (SL, TP, trailing, liquidacion, funding) y llena las entradas pendientes.
3. Decide las entradas con la confluencia de `app.explorer.confluence` (los indicadores y
   filtros que eligio el dueno): al cierre de la vela (modo `close`) o cada `eval_seconds` con la
   vela abierta (modo `intrabar`). La entrada se llena en el tick SIGUIENTE a la decision.
4. Cada pocos segundos relee las pruebas (para ver las nuevas y las detenidas), guarda las
   posiciones, anota su latido y, tras cada cierre de vela, reconcilia con las velas de REST.

Protecciones: si una moneda lleva mas de `STALE_SECONDS` sin ticks no se abren entradas en ella;
una prueba detenida cierra sus posiciones con el ultimo precio (motivo `MANUAL`); al reiniciar,
las posiciones guardadas siguen y el primer tick decide si un SL salto durante la caida.

Solo usa endpoints publicos de datos. Nunca envia ordenes: todo es paper.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol

from app.config import Settings
from app.core.logging import get_logger
from app.explorer import confluence
from app.explorer.confluence import ConfluenceStrategy
from app.live.book import MAX_CLOSED, CandleBook
from app.live.config import TestConfig
from app.live.paper import OpenRefusal, PaperPosition, close_position, on_tick, open_position
from app.live.store import LiveStore, StoredTest, TickStore
from app.persistence.models import Side
from app.research import combos
from app.strategies.base import Signal

logger = get_logger(__name__)

BTC = "BTCUSDT"
BTC_INDICATORS = {"btc", "rs"}
STALE_SECONDS = 120
RELOAD_SECONDS = 10
SAVE_SECONDS = 60
FUNDING_SECONDS = 3600
RECONCILE_DELAY_SECONDS = 8
WARMUP_PAGES = 4
PAGE = 200


class RestSource(Protocol):
    """Lo que el corredor necesita del cliente REST (`BitunixRestClient`)."""

    def get_kline(self, symbol: str, interval: str, start_time: int | None = None,
                  end_time: int | None = None, limit: int = 100,
                  price_type: str = "LAST_PRICE") -> Awaitable[list[dict]]: ...

    def get_funding_rate_batch(self) -> Awaitable[list[dict]]: ...

    def get_funding_rate_history(self, symbol: str, start_time: int | None = None,
                                 end_time: int | None = None,
                                 limit: int = 100) -> Awaitable[list[dict]]: ...


@dataclass
class _Pending:
    side: Side
    signal_time_ms: int
    decision_price: float
    strategy: ConfluenceStrategy
    frame: object  # el DataFrame con el que se decidio (para el SL y el TP)


def parse_trade_message(msg: dict) -> list[tuple[str, int, float, float, str]]:
    """Convierte un mensaje del canal `trade` en ticks.

    Formato verificado en vivo el 2026-11-11: `{"ch": "trade", "symbol": "BTCUSDT", "ts": <ms>,
    "data": [{"t": "<ISO, segundos>", "p": "<precio>", "v": "<cantidad>", "s": "buy"|"sell"}]}`.
    Se usa `ts` (milisegundos) como hora de todas las operaciones del mensaje.

    Args:
        msg: mensaje ya decodificado del WebSocket.

    Returns:
        Tuplas `(symbol, ts_ms, price, qty, side)`; vacio si el mensaje no es de `trade` o viene
        mal formado.
    """
    if msg.get("ch") != "trade":
        return []
    symbol = msg.get("symbol")
    data = msg.get("data")
    if not isinstance(symbol, str) or not isinstance(data, list):
        return []
    try:
        ts = int(msg["ts"])
    except (KeyError, TypeError, ValueError):
        return []
    out = []
    for d in data:
        try:
            price, qty = float(d["p"]), float(d["v"])
        except (KeyError, TypeError, ValueError):
            continue
        if price > 0 and qty >= 0:
            out.append((symbol, ts, price, qty, str(d.get("s", ""))))
    return out


class LiveRunner:
    """Opera todas las pruebas activas con los ticks que recibe.

    El WebSocket se conecta afuera (`scripts/live_tests.py run`) y entrega cada mensaje a
    `on_message`; `housekeeping` se llama una vez por segundo. Asi las pruebas pueden manejar el
    corredor a mano, sin red.

    Args:
        settings: configuracion (margen, apalancamiento, comisiones, deslizamiento, topes).
        store: archivo de pruebas y resultados.
        ticks: archivo de ticks, o `None` para no grabar.
        rest: cliente REST publico.
        clock: reloj en segundos (para las pruebas).
        on_symbols: se llama con la lista de monedas cuando cambia (para suscribir el WebSocket).
    """

    def __init__(self, settings: Settings, store: LiveStore, ticks: TickStore | None,
                 rest: RestSource, clock: Callable[[], float] = time.time,
                 on_symbols: Callable[[list[str]], Awaitable[None]] | None = None) -> None:
        self.settings = settings
        self.store = store
        self.ticks = ticks
        self.rest = rest
        self.clock = clock
        self.on_symbols = on_symbols
        self.tests: dict[int, StoredTest] = {}
        self.books: dict[tuple[str, str], CandleBook] = {}
        self.positions: dict[tuple[int, str], PaperPosition] = self.store.positions()
        self.pending: dict[tuple[int, str], _Pending] = {}
        self.last_price: dict[str, float] = {}
        self.last_tick_ms: dict[str, int] = {}
        self.funding: dict[str, float] = {}
        self._tick_buffer: list[tuple[str, int, float, float, str]] = []
        self._last_eval: dict[tuple[int, str], float] = {}
        self._entry_bucket: dict[tuple[int, str], int] = {}
        self._reconcile_at: dict[tuple[str, str], float] = {}
        self._stale_logged: set[str] = set()
        self._next_reload = 0.0
        self._next_save = 0.0
        self._next_funding = 0.0
        self.ticks_seen = 0

    # --- utilidades ---

    def _now_ms(self) -> int:
        return int(self.clock() * 1000)

    def symbols(self) -> list[str]:
        """Las monedas que hay que escuchar: las de las pruebas activas, las que tienen una
        posicion abierta y BTC si algun filtro lo necesita.

        Returns:
            La lista ordenada.
        """
        out = {s for t in self.tests.values() for s in t.config.symbols}
        out |= {symbol for _, symbol in self.positions}
        if any(BTC_INDICATORS & set(t.config.indicators) for t in self.tests.values()):
            out.add(BTC)
        return sorted(out)

    def _needed_books(self) -> set[tuple[str, str]]:
        need = set()
        for t in self.tests.values():
            cfg = t.config
            for s in cfg.symbols:
                need.add((s, cfg.timeframe))
            if BTC_INDICATORS & set(cfg.indicators):
                need.add((BTC, cfg.timeframe))
        return need

    async def _load_book(self, book: CandleBook) -> None:
        now = self._now_ms()
        raw: list[dict] = []
        cursor: int | None = None
        for _ in range(WARMUP_PAGES):
            page = await self.rest.get_kline(symbol=book.symbol, interval=book.timeframe,
                                             end_time=cursor, limit=PAGE)
            if not page:
                break
            raw += page
            cursor = min(int(r["time"]) for r in page)
        book.load(raw, now)
        try:
            rows = await self.rest.get_funding_rate_history(symbol=book.symbol, limit=PAGE)
            book.funding_events = sorted(
                (int(r["fundingTime"]), float(r["fundingRate"])) for r in rows)
        except Exception as exc:  # noqa: BLE001 - el funding es opcional: sin el, vota aproximado
            logger.warning("sin historial de funding para %s: %s", book.symbol, exc)

    # --- pruebas ---

    async def reload_tests(self) -> None:
        """Relee las pruebas activas, carga las velas que falten y cierra lo de las detenidas.

        Efectos:
            Lee `live_tests`. Red: GET publicos de velas y funding para las monedas nuevas.
            Escribe `live_trades`, `live_positions` y `live_events` si cierra posiciones.
        """
        active = {t.id: t for t in self.store.tests(only_active=True)}
        for test_id in set(self.tests) - set(active):
            self._close_test(test_id)
        added = set(active) - set(self.tests)
        self.tests = active
        # Una posicion guardada de una prueba que ya no esta activa (se detuvo con el corredor
        # apagado) se cierra con el primer precio que se conozca.
        for (test_id, _symbol) in list(self.positions):
            if test_id not in active:
                self._close_test(test_id)
        for key in self._needed_books() - set(self.books):
            book = CandleBook(*key)
            try:
                await self._load_book(book)
            except Exception as exc:  # noqa: BLE001 - se reintenta en la proxima recarga
                self.store.log("ERROR", f"no se pudieron cargar las velas {key}: {exc}",
                               symbol=key[0], now_ms=self._now_ms())
                continue
            self.books[key] = book
        for test_id in sorted(added):
            cfg = active[test_id].config
            self.store.log("START", f"prueba '{cfg.name}' activa: {cfg.to_json()}",
                           test_id=test_id, now_ms=self._now_ms())
        if self.on_symbols is not None:
            await self.on_symbols(self.symbols())

    def _close_test(self, test_id: int) -> None:
        for key in [k for k in self.pending if k[0] == test_id]:
            del self.pending[key]
        for (tid, symbol), pos in list(self.positions.items()):
            if tid != test_id:
                continue
            price = self.last_price.get(symbol)
            if price is None:
                continue  # sin precio todavia: se cierra cuando llegue el primer tick
            trade = close_position(pos, price, self._now_ms(), "MANUAL", self.settings)
            self.store.record_trade(test_id, symbol, trade)
            del self.positions[(tid, symbol)]
            self.store.log("CLOSE", f"MANUAL a {price} neto {trade.pnl_net_usdt:+.4f}",
                           test_id=test_id, symbol=symbol, now_ms=self._now_ms())

    # --- ticks ---

    async def on_message(self, msg: dict) -> None:
        """Procesa un mensaje del WebSocket (solo los del canal `trade` hacen algo).

        Args:
            msg: mensaje ya decodificado.

        Efectos:
            Puede escribir `live_trades`, `live_positions` y `live_events`.
        """
        for tick in parse_trade_message(msg):
            self.on_tick(*tick)

    def on_tick(self, symbol: str, ts_ms: int, price: float, qty: float, side: str) -> None:
        """Aplica un tick: lo guarda, mueve las velas, revisa posiciones y llena pendientes.

        Args:
            symbol: moneda.
            ts_ms: hora del tick, ms Unix.
            price: precio.
            qty: cantidad en la moneda base.
            side: `buy` o `sell`.

        Efectos:
            Puede escribir `live_trades`, `live_positions` y `live_events`.
        """
        self.ticks_seen += 1
        self.last_price[symbol] = price
        self.last_tick_ms[symbol] = ts_ms
        self._stale_logged.discard(symbol)
        if self.ticks is not None:
            self._tick_buffer.append((symbol, ts_ms, price, qty, side))

        # 1. Posiciones abiertas de esta moneda.
        for (test_id, sym), pos in list(self.positions.items()):
            if sym != symbol:
                continue
            if test_id not in self.tests:
                self._close_test(test_id)
                continue
            trade = on_tick(pos, price, ts_ms, self.settings,
                            funding_rate=self.funding.get(symbol))
            if trade is not None:
                self.store.record_trade(test_id, symbol, trade)
                del self.positions[(test_id, symbol)]
                self.store.log("CLOSE", f"{trade.close_reason} a {trade.exit_price} neto "
                               f"{trade.pnl_net_usdt:+.4f}", test_id=test_id, symbol=symbol,
                               now_ms=ts_ms)

        # 2. Entradas pendientes: se llenan con este tick (posterior a la decision).
        for (test_id, sym), p in list(self.pending.items()):
            if sym != symbol or ts_ms <= p.signal_time_ms:
                continue
            del self.pending[(test_id, sym)]
            self._fill(test_id, symbol, p, price, ts_ms)

        # 3. Velas: si este tick cerro una, decide el modo `close`.
        for (sym, tf), book in self.books.items():
            if sym != symbol:
                continue
            if book.on_tick(price, qty, ts_ms):
                self._bar_closed(sym, tf, ts_ms)

    def _fill(self, test_id: int, symbol: str, p: _Pending, price: float, ts_ms: int) -> None:
        sig = Signal(p.side.value)
        sl = p.strategy.stop_price(p.frame, sig, price)
        tp = p.strategy.take_profit_price(p.frame, sig, price)
        trailing = p.strategy.trailing_distance(p.frame)
        result = open_position(p.side, price, ts_ms, sl=sl, tp=tp, trailing=trailing,
                               settings=self.settings, signal_time_ms=p.signal_time_ms,
                               decision_price=p.decision_price)
        if isinstance(result, OpenRefusal):
            self.store.log("REFUSED", f"{p.side.value} {result.reason}: {result.detail}",
                           test_id=test_id, symbol=symbol, now_ms=ts_ms)
            return
        self.positions[(test_id, symbol)] = result
        self.store.save_position(test_id, symbol, result)
        self.store.log("OPEN", f"{p.side.value} a {price} (decidido a {p.decision_price}), "
                       f"SL {result.stop}, TP {result.tp}, trailing {result.trailing}",
                       test_id=test_id, symbol=symbol, now_ms=ts_ms)

    # --- decisiones ---

    def _is_stale(self, symbol: str, now_ms: int) -> bool:
        last = self.last_tick_ms.get(symbol)
        stale = last is None or now_ms - last > STALE_SECONDS * 1000
        if stale and symbol not in self._stale_logged and last is not None:
            self._stale_logged.add(symbol)
            self.store.log("STALE", f"sin ticks hace mas de {STALE_SECONDS} s: no se abren "
                           "entradas", symbol=symbol, now_ms=now_ms)
        return stale

    def evaluate(self, cfg: TestConfig, symbol: str,
                 include_forming: bool) -> tuple[Signal, ConfluenceStrategy, object] | None:
        """Calcula la confluencia de una prueba en una moneda con las velas actuales.

        Args:
            cfg: la prueba.
            symbol: la moneda.
            include_forming: usa tambien la vela en curso (modo `intrabar`).

        Returns:
            `(senal, estrategia, velas)`, o `None` si todavia no hay velas suficientes.
        """
        book = self.books.get((symbol, cfg.timeframe))
        if book is None:
            return None
        frame = book.frame(include_forming)
        if len(frame) <= confluence.WARMUP_BY_TIMEFRAME[cfg.timeframe]:
            return None
        btc = None
        if BTC_INDICATORS & set(cfg.indicators):
            btc_book = self.books.get((BTC, cfg.timeframe))
            if btc_book is None:
                return None
            btc = btc_book.frame(include_forming)
        strat = ConfluenceStrategy(cfg.indicators, cfg.timeframe, window=cfg.window,
                                   exit_name=cfg.exit_name, btc=btc, min_agree=cfg.min_agree,
                                   pct_exit=cfg.pct_exit)
        return strat.evaluate(frame), strat, frame

    def _decide(self, test: StoredTest, symbol: str, include_forming: bool, now_ms: int) -> None:
        key = (test.id, symbol)
        if key in self.positions or key in self.pending or self._is_stale(symbol, now_ms):
            return
        book = self.books.get((symbol, test.config.timeframe))
        if book is None:
            return
        bucket = book.current_bucket(now_ms)
        if self._entry_bucket.get(key) == bucket:
            return  # como mucho una entrada por vela y por moneda
        try:
            result = self.evaluate(test.config, symbol, include_forming)
        except Exception as exc:  # noqa: BLE001 - una prueba rota no detiene a las demas
            self.store.log("ERROR", f"fallo al evaluar: {type(exc).__name__}: {exc}",
                           test_id=test.id, symbol=symbol, now_ms=now_ms)
            return
        if result is None or result[0] == Signal.HOLD:
            return
        signal, strat, frame = result
        price = self.last_price[symbol]
        self._entry_bucket[key] = bucket
        self.pending[key] = _Pending(Side(signal.value), now_ms, price, strat, frame)
        self.store.log("SIGNAL", f"{signal.value} con precio {price} "
                       f"({'vela abierta' if include_forming else 'cierre de vela'})",
                       test_id=test.id, symbol=symbol, now_ms=now_ms)

    def _bar_closed(self, symbol: str, timeframe: str, now_ms: int) -> None:
        self._reconcile_at[(symbol, timeframe)] = self.clock() + RECONCILE_DELAY_SECONDS
        for test in self.tests.values():
            cfg = test.config
            if cfg.mode == "close" and cfg.timeframe == timeframe and symbol in cfg.symbols:
                self._decide(test, symbol, include_forming=False, now_ms=now_ms)

    # --- tareas periodicas ---

    async def housekeeping(self) -> None:
        """Lo que se hace una vez por segundo: velas por reloj, modo `intrabar`, guardado y red.

        Efectos:
            Escribe los archivos de ticks y de pruebas. Red: GET publicos de velas y funding.
        """
        now = self.clock()
        now_ms = int(now * 1000)
        if self.ticks is not None and self._tick_buffer:
            buffer, self._tick_buffer = self._tick_buffer, []
            self.ticks.append(buffer)

        if now >= self._next_reload:
            self._next_reload = now + RELOAD_SECONDS
            await self.reload_tests()
            self.store.beat(f"{len(self.tests)} pruebas, {len(self.positions)} posiciones, "
                            f"{self.ticks_seen} ticks", now_ms=now_ms)

        for (symbol, tf), book in self.books.items():
            if book.roll(now_ms):
                self._bar_closed(symbol, tf, now_ms)

        for test in self.tests.values():
            cfg = test.config
            if cfg.mode != "intrabar":
                continue
            for symbol in cfg.symbols:
                key = (test.id, symbol)
                if now - self._last_eval.get(key, 0.0) < cfg.eval_seconds:
                    continue
                self._last_eval[key] = now
                self._decide(test, symbol, include_forming=True, now_ms=now_ms)

        for key, at in list(self._reconcile_at.items()):
            if now >= at and key in self.books:
                del self._reconcile_at[key]
                book = self.books[key]
                try:
                    raw = await self.rest.get_kline(symbol=key[0], interval=key[1], limit=PAGE)
                    merged = {b["open_time"]: b for b in book.closed}
                    tmp = CandleBook(*key)
                    tmp.load(raw, now_ms)
                    merged.update({b["open_time"]: b for b in tmp.closed})
                    book.closed = [merged[t] for t in sorted(merged)][-MAX_CLOSED:]
                except Exception as exc:  # noqa: BLE001 - se sigue con las velas armadas con ticks
                    logger.warning("no se pudo reconciliar %s: %s", key, exc)

        if now >= self._next_funding:
            self._next_funding = now + FUNDING_SECONDS
            try:
                rows = await self.rest.get_funding_rate_batch()
                self.funding = {r["symbol"]: float(r["fundingRate"]) for r in rows
                                if isinstance(r, dict) and r.get("fundingRate") is not None}
            except Exception as exc:  # noqa: BLE001 - sin funding la operacion queda aproximada
                logger.warning("no se pudo leer el funding vigente: %s", exc)

        if now >= self._next_save:
            self._next_save = now + SAVE_SECONDS
            for (test_id, symbol), pos in self.positions.items():
                self.store.save_position(test_id, symbol, pos)
            # Las caches de indicadores crecen con cada vela nueva: se vacian para no acumular.
            combos._FEATURE_CACHE.clear()
            confluence._FEATURE_CACHE.clear()

    def shutdown(self) -> None:
        """Guarda lo pendiente antes de salir (ticks en memoria y posiciones abiertas).

        Efectos:
            Escribe los archivos de ticks y de pruebas.
        """
        if self.ticks is not None and self._tick_buffer:
            self.ticks.append(self._tick_buffer)
            self._tick_buffer = []
        for (test_id, symbol), pos in self.positions.items():
            self.store.save_position(test_id, symbol, pos)
        self.store.log("STOP", "corredor detenido", now_ms=self._now_ms())
