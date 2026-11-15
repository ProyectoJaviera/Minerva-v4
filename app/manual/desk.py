"""El escritorio de la sala de trading manual: ordenes, posiciones y cuenta, tick a tick.

Reglas (las mismas del motor paper de las pruebas, `app.live.paper`):

- **Orden a mercado:** se llena con el primer tick posterior a la orden, con la comision taker y el
  deslizamiento de la configuracion.
- **Orden limite:** un largo se llena cuando un tick llega a su precio o lo baja (un corto, cuando
  lo sube), **al precio limite**. Si al crearla el precio ya estaba del otro lado (limite
  «comerciable») se llena con el primer tick, a ese precio, como en un exchange. Se cobra la
  comision taker igual (Minerva no modela la de maker: es la estimacion conservadora) y sin
  deslizamiento de entrada.
- **TP parciales** (hasta 3 niveles: distancia en % y % de la cantidad inicial que cierra cada uno;
  con «SL a la entrada tras el TP1» el stop pasa a la entrada al tocarse el primero) y **trailing
  stop** en % del precio de entrada (sigue al mejor precio desde la entrada), con las mismas reglas
  del motor paper de las pruebas.
- **TP y SL** en % del precio de entrada, opcionales. Sin SL la posicion solo sale por TP, a mano o
  por liquidacion (aislado: se pierde a lo sumo el margen). Un SL mas lejos que la liquidacion se
  rechaza.
- **Cierre manual** (todo o el 50 %) con el ultimo precio conocido, con comision y deslizamiento de
  salida. «SL a la entrada» mueve el stop al precio de entrada si la posicion va ganando.
- **Liquidacion:** en margen aislado se pierde a lo sumo el margen (mas la comision de entrada),
  aunque el precio salte mas alla del nivel de liquidacion.
- **Cuenta:** el saldo cambia solo al cerrar (resultado neto con comisiones, deslizamiento y
  funding). Disponible = saldo - margen de posiciones abiertas - margen reservado por ordenes.

El escritorio vive dentro del corredor (`LiveRunner`), que le pasa cada tick. Todo cambio se guarda
al momento en `ManualStore`.
"""

from __future__ import annotations

import re
import time
from collections import deque
from collections.abc import Callable
from dataclasses import replace

from app.config import Settings
from app.live.paper import (
    ClosedTrade,
    OpenRefusal,
    PaperPosition,
    _partial_close,
    close_position,
    on_tick,
    open_position,
)
from app.live.report import position_view
from app.manual.store import DEFAULT_BALANCE_USDT, ManualStore
from app.persistence.models import Side

SYMBOL_RE = re.compile(r"^[A-Z0-9]{2,20}USDT$")
MAX_LEVERAGE = 100
MAX_TP_PCT = 500.0
MAX_TP_LEVELS = 3            # TP parciales, como en las pruebas «En vivo»
MAX_TRAIL_PCT = 50.0
# Un SL que perderia este % del margen o mas queda a la altura de la liquidacion o mas lejos.
MAX_SL_MARGIN_LOSS_PCT = 90.0
MAX_OPEN_ITEMS = 50          # posiciones + ordenes abiertas
MAX_WATCHED = 12             # monedas que la sala puede mirar a la vez (lista + elegida)
WATCH_SECONDS = 90           # una moneda mirada deja de escucharse si nadie la pide en este tiempo
PRICE_FRESH_SECONDS = 120    # sin un tick en este tiempo no se cierra a mano (precio viejo)
RECENT_TRADES = 60


class ManualError(ValueError):
    """Un pedido de la sala que no se puede cumplir; el mensaje es para el dueno."""


class ManualDesk:
    """La cuenta manual, sus ordenes y posiciones.

    Args:
        settings: configuracion (comision, deslizamiento, valores por defecto).
        store: archivo de la sala.
        clock: reloj en segundos (para las pruebas).
    """

    def __init__(self, settings: Settings, store: ManualStore,
                 clock: Callable[[], float] = time.time) -> None:
        self.settings = settings
        # El tope de riesgo de las pruebas no aplica a la sala: el limite es la liquidacion.
        self.open_settings = settings.model_copy(update={"max_sl_margin_loss_pct": 101.0})
        self.store = store
        self.clock = clock
        self.balance, self.reset_at_ms = store.balance()
        self.positions: dict[int, list] = {pid: [sym, pos, meta]
                                           for pid, (sym, pos, meta) in store.positions().items()}
        self.orders: dict[int, dict] = store.orders()
        self.watched: dict[str, float] = {}
        self.last_price: dict[str, float] = {}
        self.last_tick_ms: dict[str, int] = {}
        self.recent: dict[str, deque] = {}
        self.events: deque = deque(maxlen=30)

    # --- utilidades ---

    def _now_ms(self) -> int:
        return int(self.clock() * 1000)

    def _note(self, text: str, kind: str = "info") -> None:
        self.events.appendleft({"ts_ms": self._now_ms(), "kind": kind, "text": text})

    @staticmethod
    def _open_margin(pos: PaperPosition) -> float:
        frac = pos.qty / pos.qty_initial if pos.qty_initial else 1.0
        return pos.margin_usdt * frac

    def used_margin(self) -> float:
        """Margen ocupado: posiciones abiertas mas ordenes pendientes.

        Returns:
            USDT.
        """
        return (sum(self._open_margin(p) for _, p, _ in self.positions.values())
                + sum(o["margin_usdt"] for o in self.orders.values()))

    def available(self) -> float:
        """Saldo disponible para nuevas ordenes.

        Returns:
            USDT.
        """
        return self.balance - self.used_margin()

    def symbols(self) -> set[str]:
        """Monedas que la sala necesita escuchar: con posicion u orden, o miradas hace poco.

        Returns:
            El conjunto de monedas.
        """
        now = self.clock()
        self.watched = {s: t for s, t in self.watched.items() if now - t <= WATCH_SECONDS}
        out = {sym for sym, _, _ in self.positions.values()}
        out |= {o["symbol"] for o in self.orders.values()}
        return out | set(self.watched)

    def watch(self, symbol: str) -> None:
        """Marca una moneda como mirada desde la sala (para recibir su precio en vivo).

        Args:
            symbol: moneda.

        Raises:
            ManualError: si la moneda no es valida o ya se miran demasiadas.
        """
        symbol = self._check_symbol(symbol)
        if symbol not in self.watched and len(self.symbols()) >= MAX_WATCHED + MAX_OPEN_ITEMS:
            raise ManualError("Demasiadas monedas a la vez.")
        if symbol not in self.watched and len(self.watched) >= MAX_WATCHED:
            oldest = min(self.watched, key=self.watched.get)
            del self.watched[oldest]
        self.watched[symbol] = self.clock()

    @staticmethod
    def _check_symbol(symbol: str) -> str:
        symbol = (symbol or "").strip().upper()
        if not SYMBOL_RE.match(symbol):
            raise ManualError(f"Moneda invalida: {symbol or '(vacia)'}.")
        return symbol

    # --- ordenes ---

    def place(self, symbol: str, side: str, kind: str, margin_usdt: float, leverage: int,
              price: float | None = None, tp_pct: float | None = None,
              sl_pct: float | None = None,
              take_profits: list[tuple[float, float]] | None = None,
              breakeven: bool = False, trail_pct: float | None = None) -> dict:
        """Crea una orden a mercado o limite.

        Args:
            symbol: moneda (p. ej. `BTCUSDT`).
            side: `LONG` o `SHORT`.
            kind: `market` o `limit`.
            margin_usdt: margen de la operacion.
            leverage: apalancamiento (1 a 100).
            price: precio limite (solo `limit`).
            tp_pct: TP en % del precio de entrada, o `None`.
            sl_pct: SL en % del precio de entrada, o `None` (sin SL).
            take_profits: TP parciales `(distancia %, % de la cantidad inicial que cierra)`, hasta
                3; si se dan, `tp_pct` se ignora. Si suman menos de 100 %, el resto sigue con el SL
                o el trailing.
            breakeven: con TP parciales, el SL pasa a la entrada al tocarse el primero.
            trail_pct: trailing stop en % del precio de entrada, o `None`.

        Returns:
            La orden creada.

        Raises:
            ManualError: si algun dato no es valido o no alcanza el disponible.
        """
        symbol = self._check_symbol(symbol)
        side = (side or "").upper()
        if side not in ("LONG", "SHORT"):
            raise ManualError("Elige largo o corto.")
        if kind not in ("market", "limit"):
            raise ManualError("Tipo de orden invalido.")
        if not (margin_usdt and margin_usdt > 0):
            raise ManualError("Escribe el margen en USDT.")
        if not 1 <= int(leverage) <= MAX_LEVERAGE:
            raise ManualError(f"El apalancamiento va de 1 a {MAX_LEVERAGE}x.")
        leverage = int(leverage)
        if tp_pct is not None and not 0 < tp_pct <= MAX_TP_PCT:
            raise ManualError(f"El TP va de 0 a {MAX_TP_PCT:g} %.")
        if sl_pct is not None:
            if not sl_pct > 0:
                raise ManualError("El SL debe ser mayor que 0 %.")
            if sl_pct * leverage >= MAX_SL_MARGIN_LOSS_PCT:
                top = MAX_SL_MARGIN_LOSS_PCT / leverage
                raise ManualError(f"Con {leverage}x el SL debe estar a menos de {top:.2f} %: "
                                  "mas lejos, liquida antes de llegar al SL.")
        levels: list[list[float]] = []
        if take_profits:
            if len(take_profits) > MAX_TP_LEVELS:
                raise ManualError(f"Como maximo {MAX_TP_LEVELS} TP parciales.")
            for dist, close in take_profits:
                if not (0 < dist <= MAX_TP_PCT and 0 < close <= 100):
                    raise ManualError("Cada TP parcial necesita una distancia (0 a 500 %) y un % "
                                      "que cierra (0 a 100 %).")
                levels.append([float(dist), float(close)])
            levels.sort(key=lambda lv: lv[0])
            if len({lv[0] for lv in levels}) != len(levels):
                raise ManualError("Dos TP parciales no pueden estar a la misma distancia.")
            if sum(lv[1] for lv in levels) > 100 + 1e-9:
                raise ManualError("Los TP parciales cierran mas del 100 % de la posicion.")
            tp_pct = None
        if trail_pct is not None and not 0 < trail_pct <= MAX_TRAIL_PCT:
            raise ManualError(f"El trailing va de 0 a {MAX_TRAIL_PCT:g} %.")
        if trail_pct is not None and trail_pct * leverage >= MAX_SL_MARGIN_LOSS_PCT:
            top = MAX_SL_MARGIN_LOSS_PCT / leverage
            raise ManualError(f"Con {leverage}x el trailing debe ser menor que {top:.2f} %: mas "
                              "lejos, liquida antes.")
        if kind == "limit" and not (price and price > 0):
            raise ManualError("Escribe el precio limite.")
        if len(self.positions) + len(self.orders) >= MAX_OPEN_ITEMS:
            raise ManualError(f"Como maximo {MAX_OPEN_ITEMS} posiciones y ordenes abiertas.")
        fee_est = margin_usdt * leverage * self.settings.taker_fee_pct
        if margin_usdt + fee_est > self.available() + 1e-9:
            raise ManualError(f"No alcanza el disponible ({max(0.0, self.available()):.2f} USDT; "
                              f"la comision de entrada es {fee_est:.2f} USDT).")
        last = self.last_price.get(symbol)
        marketable = kind == "limit" and last is not None and (
            (side == "LONG" and price >= last) or (side == "SHORT" and price <= last))
        order = {"id": self.store.next_id(), "symbol": symbol, "side": side, "kind": kind,
                 "price": float(price) if kind == "limit" else None,
                 "margin_usdt": float(margin_usdt), "leverage": leverage,
                 "tp_pct": tp_pct, "sl_pct": sl_pct, "take_profits": levels,
                 "breakeven": bool(breakeven and levels), "trail_pct": trail_pct,
                 "created_ms": self._now_ms(), "marketable": marketable}
        self.orders[order["id"]] = order
        self.store.save_order(order["id"], symbol, order)
        self.watch(symbol)
        what = "a mercado" if kind == "market" else f"limite en {price:g}"
        self._note(f"Orden {what}: {'largo' if side == 'LONG' else 'corto'} {symbol} "
                   f"{leverage}x, margen {margin_usdt:g} USDT")
        return order

    def cancel(self, order_id: int) -> None:
        """Cancela una orden pendiente.

        Args:
            order_id: id de la orden.

        Raises:
            ManualError: si no existe (ya se lleno o se cancelo).
        """
        order = self.orders.pop(order_id, None)
        if order is None:
            raise ManualError("La orden ya no esta pendiente.")
        self.store.delete_order(order_id)
        self._note(f"Orden {order_id} cancelada ({order['symbol']})")

    def _fill(self, order: dict, price: float, ts_ms: int, at_limit: bool) -> None:
        side = Side(order["side"])
        sign = 1.0 if side == Side.LONG else -1.0
        sl = price * (1 - sign * order["sl_pct"] / 100) if order["sl_pct"] else None
        tp = price * (1 + sign * order["tp_pct"] / 100) if order["tp_pct"] else None
        if sl is None:
            # Sin SL: un stop en el 100 % del margen; la liquidacion (antes) es la que actua.
            sl = price * (1 - sign / order["leverage"])
        levels = [(price * (1 + sign * dist / 100), close / 100)
                  for dist, close in order.get("take_profits") or []]
        trail = price * order["trail_pct"] / 100 if order.get("trail_pct") else None
        result = open_position(side, price, ts_ms, sl=sl, tp=tp, trailing=trail,
                               settings=self.open_settings, signal_time_ms=order["created_ms"],
                               decision_price=order["price"] or price,
                               margin_usdt=order["margin_usdt"], leverage=order["leverage"],
                               take_profits=levels or None,
                               breakeven=bool(order.get("breakeven")))
        del self.orders[order["id"]]
        self.store.delete_order(order["id"])
        if isinstance(result, OpenRefusal):
            self._note(f"Orden {order['id']} rechazada al llenarse: {result.detail}", "error")
            return
        if tp is None:
            result.tp = None  # sin TP pedido: no se usa el TP de respaldo de las pruebas
        if at_limit:
            result.slippage_entry_usdt = 0.0
        meta = {"no_sl": not order["sl_pct"] and not trail, "kind": order["kind"],
                "order_id": order["id"], "trail_pct": order.get("trail_pct")}
        self.positions[order["id"]] = [order["symbol"], result, meta]
        self.store.save_position(order["id"], order["symbol"], result, meta)
        self._note(f"{'Largo' if side == Side.LONG else 'Corto'} {order['symbol']} abierto a "
                   f"{price:g} ({order['leverage']}x)", "open")

    # --- posiciones ---

    def _finish(self, pos_id: int, trade: ClosedTrade) -> None:
        symbol, pos, _meta = self.positions[pos_id]
        if trade.close_reason == "LIQUIDATION":
            # Margen aislado: si el precio salto mas alla de la liquidacion, se pierde a lo sumo
            # el margen (mas la comision de entrada ya pagada), no lo que diria el tick.
            floor = -(pos.margin_usdt + pos.fee_entry_usdt)
            if trade.pnl_net_usdt < floor:
                trade = replace(trade, pnl_net_usdt=floor)
        del self.positions[pos_id]
        self.store.delete_position(pos_id)
        self.store.record_trade(pos_id, symbol, trade)
        self.balance += trade.pnl_net_usdt
        self.store.set_balance(self.balance)
        self._note(f"{symbol} cerrado ({trade.close_reason}) a {trade.exit_price:g}: "
                   f"{trade.pnl_net_usdt:+.3f} USDT", "close")

    def _fresh_price(self, symbol: str) -> float:
        price, ts = self.last_price.get(symbol), self.last_tick_ms.get(symbol)
        if price is None or ts is None or self._now_ms() - ts > PRICE_FRESH_SECONDS * 1000:
            raise ManualError(f"Sin precio reciente de {symbol}: espera unos segundos.")
        return price

    def close(self, pos_id: int, fraction: float = 1.0) -> None:
        """Cierra una posicion (toda o una parte) con el ultimo precio.

        Args:
            pos_id: id de la posicion.
            fraction: parte de lo que sigue abierto (0 a 1; 1 = todo).

        Raises:
            ManualError: si la posicion no existe o no hay precio reciente.
        """
        if pos_id not in self.positions:
            raise ManualError("La posicion ya esta cerrada.")
        if not 0 < fraction <= 1:
            raise ManualError("Fraccion invalida.")
        symbol, pos, meta = self.positions[pos_id]
        price, now = self._fresh_price(symbol), self._now_ms()
        if fraction >= 0.999 or pos.qty * (1 - fraction) <= 1e-12:
            self._finish(pos_id, close_position(pos, price, now, "MANUAL", self.settings))
            return
        _partial_close(pos, price, pos.qty * fraction, now, f"MANUAL{round(fraction * 100)}",
                       self.settings)
        self.store.save_position(pos_id, symbol, pos, meta)
        self._note(f"{symbol}: cerrado el {fraction:.0%} a {price:g}")

    def breakeven(self, pos_id: int) -> None:
        """Mueve el SL al precio de entrada (solo si la posicion va ganando).

        Args:
            pos_id: id de la posicion.

        Raises:
            ManualError: si no existe, no hay precio o va perdiendo.
        """
        if pos_id not in self.positions:
            raise ManualError("La posicion ya esta cerrada.")
        symbol, pos, meta = self.positions[pos_id]
        price = self._fresh_price(symbol)
        long = pos.side == "LONG"
        if (long and price <= pos.entry_price) or (not long and price >= pos.entry_price):
            raise ManualError("Solo se puede mover el SL a la entrada si la posicion va ganando.")
        pos.stop = pos.entry_price
        meta["no_sl"] = False
        self.store.save_position(pos_id, symbol, pos, meta)
        self._note(f"{symbol}: SL movido a la entrada ({pos.entry_price:g})")

    def close_all(self) -> int:
        """Cierra todas las posiciones que tengan precio reciente y cancela las ordenes.

        Returns:
            Cuantas posiciones se cerraron.
        """
        for oid in list(self.orders):
            self.cancel(oid)
        closed = 0
        for pid in list(self.positions):
            try:
                self.close(pid)
                closed += 1
            except ManualError as exc:
                self._note(str(exc), "error")
        return closed

    def reset(self) -> None:
        """Reinicia la cuenta al saldo inicial (sin posiciones ni ordenes)."""
        now = self._now_ms()
        self.store.reset(DEFAULT_BALANCE_USDT, now_ms=now)
        self.balance, self.reset_at_ms = DEFAULT_BALANCE_USDT, now
        self.positions.clear()
        self.orders.clear()
        self._note(f"Cuenta reiniciada a {DEFAULT_BALANCE_USDT:g} USDT")

    # --- ticks ---

    def on_tick(self, symbol: str, ts_ms: int, price: float, qty: float, side: str,
                funding_rate: float | None = None) -> None:
        """Aplica un tick: precio, operaciones recientes, posiciones y ordenes de la moneda.

        Args:
            symbol: moneda.
            ts_ms: hora del tick, ms Unix.
            price: precio.
            qty: cantidad.
            side: `buy` o `sell`.
            funding_rate: funding vigente de la moneda, o `None`.

        Efectos:
            Escribe las tablas `manual_*` si algo se abre o se cierra.
        """
        self.last_price[symbol] = price
        self.last_tick_ms[symbol] = ts_ms
        if symbol in self.watched or any(o["symbol"] == symbol for o in self.orders.values()) \
                or any(p[0] == symbol for p in self.positions.values()):
            self.recent.setdefault(symbol, deque(maxlen=RECENT_TRADES)).appendleft(
                (ts_ms, price, qty, side))
        for pid, (sym, pos, _meta) in list(self.positions.items()):
            if sym != symbol:
                continue
            trade = on_tick(pos, price, ts_ms, self.settings, funding_rate=funding_rate)
            if trade is not None:
                self._finish(pid, trade)
        for order in list(self.orders.values()):
            if order["symbol"] != symbol or ts_ms <= order["created_ms"]:
                continue
            if order["kind"] == "market" or order.get("marketable"):
                self._fill(order, price, ts_ms, at_limit=False)
            elif (order["side"] == "LONG" and price <= order["price"]) or \
                    (order["side"] == "SHORT" and price >= order["price"]):
                self._fill(order, order["price"], ts_ms, at_limit=True)

    # --- lo que muestra el dashboard ---

    def snapshot(self, symbol: str | None = None) -> dict:
        """El estado de la sala para el dashboard.

        Args:
            symbol: moneda que se esta mirando (para sus operaciones recientes), o `None`.

        Returns:
            `account`, `positions`, `orders`, `trades` (desde el ultimo reinicio), `recent`
            (operaciones del mercado de `symbol`), `prices` y `events`.
        """
        now = self._now_ms()
        fee, slip = self.settings.taker_fee_pct, self.settings.backtest_slippage_bps
        positions, upnl = [], 0.0
        for pid, (sym, pos, meta) in self.positions.items():
            price = self.last_price.get(sym)
            view = position_view(pos, price, fee, slip)
            upnl += view["upnl"] or 0.0
            positions.append({"id": pid, "symbol": sym, **pos.to_dict(), **view,
                              "price": price, "price_ts_ms": self.last_tick_ms.get(sym),
                              "no_sl": bool(meta.get("no_sl")), "kind": meta.get("kind"),
                              "trail_pct": meta.get("trail_pct")})
        trades = self.store.trades(limit=100, since_ms=self.reset_at_ms)
        realized_today = self.store.realized_since(max(self.reset_at_ms, now - now % 86_400_000))
        return {
            "now_ms": now,
            "account": {"balance": self.balance, "equity": self.balance + upnl,
                        "available": max(0.0, self.available()), "used_margin": self.used_margin(),
                        "upnl": upnl, "realized_today": realized_today,
                        "start_balance": DEFAULT_BALANCE_USDT, "reset_at_ms": self.reset_at_ms},
            "positions": positions,
            "orders": [dict(o, last_price=self.last_price.get(o["symbol"]))
                       for o in self.orders.values()],
            "trades": trades,
            "recent": [{"ts_ms": t, "price": p, "qty": q, "side": s}
                       for t, p, q, s in self.recent.get(symbol or "", [])],
            "prices": {s: {"price": p, "ts_ms": self.last_tick_ms.get(s)}
                       for s, p in self.last_price.items() if s in self.symbols()},
            "events": list(self.events),
            "fees": {"taker_pct": fee, "slippage_bps": slip},
        }
