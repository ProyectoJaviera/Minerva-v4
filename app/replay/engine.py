"""Motor de backtest en tiempo real: recorre la historia minuto a minuto.

A diferencia de `app/backtesting/engine.py` (que salta de cierre en cierre y llena en la apertura
de la vela siguiente), aqui la estrategia vive dentro de la vela:

- En cada **cierre de vela** de su marco ve las velas cerradas (con sus indicadores, calculados
  una sola vez con `precompute`) y puede dejar una orden: a mercado, de stop (entrar si el precio
  cruza un nivel) o limite. La orden vale hasta el cierre siguiente salvo que diga otra cosa.
- En cada **minuto** (solo si declara `uses_minutes = True`) ve ademas la vela en curso, armada
  con los minutos que van, y el precio actual. Si decide entrar a mercado, se llena en la apertura
  del minuto siguiente: nunca con un precio que todavia no existia.

Reglas de simulacion dentro del minuto (la resolucion del archivo es 1 minuto; en vivo el precio
llega cada medio segundo):

- **Stop de entrada:** se dispara si el minuto toca el nivel; se llena en el nivel, o en la
  apertura del minuto si ya abrio mas alla (hueco), que siempre es el peor precio.
- **Limite de entrada:** se llena solo si el minuto ATRAVIESA el nivel (estricto), al nivel, con
  comision maker y sin deslizamiento.
- **Salidas:** primero el hueco en la apertura (salida a la apertura), despues el lado adverso
  (SL o liquidacion, el que este mas cerca) y por ultimo el TP. **Si en un mismo minuto se tocan
  el SL y el TP se supone que vino primero el SL** (el peor caso), porque el minuto no dice el
  orden.
- **Minuto de entrada:** si la entrada fue a la apertura del minuto, el minuto entero es posterior
  y se revisa completo. Si fue a mitad de minuto (stop o limite) solo se revisa el lado adverso:
  no se sabe si el TP se toco antes o despues del llenado.
- **Trailing:** se mueve con el extremo del minuto y solo puede disparar desde el minuto
  siguiente.
- **Funding:** prorrateado por minuto con la tasa vigente (misma convencion que el motor de
  velas: antes del primer dato real se usa la mediana y la operacion queda marcada como
  aproximada).
- **Liquidacion:** con el precio de la ultima operacion (el archivo de minutos no trae el precio de
  marca). Con el tope de riesgo por operacion el SL queda siempre mucho antes que la liquidacion,
  asi que la aproximacion casi nunca decide nada; queda anotada.

El motor es puro (sin base ni red): recibe los minutos y el funding ya cargados. Devuelve
`RawTrade`/`RawSkip`, los mismos tipos del motor de velas, para reusar todas las metricas.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum

import pandas as pd

from app.backtesting.engine import RawSkip, RawTrade
from app.backtesting.funding import DEFAULT_FUNDING_INTERVAL_HOURS
from app.backtesting.liquidation import compute_liquidation_price
from app.config import Settings
from app.execution.pnl import compute_close_result, compute_open_fill
from app.persistence.models import Side
from app.replay.candles import Candle, aggregate, bucket_start, timeframe_ms
from app.replay.minute_store import MINUTE_MS, MinuteBar
from app.strategies.base import Signal
from app.trading.sl_calc import fallback_sl_tp_prices, margin_loss_pct
from app.trading.slippage import entry_slippage_usdt, exit_slippage_usdt

MINUTE_HOURS = 1.0 / 60.0


class EntryKind(StrEnum):
    """Tipo de orden de entrada: a mercado, de stop (al cruzar un nivel) o limite."""
    MARKET = "MARKET"
    STOP = "STOP"
    LIMIT = "LIMIT"


@dataclass(frozen=True)
class EntryOrder:
    """Lo que la estrategia pide para entrar.

    Attributes:
        side: direccion.
        kind: tipo de orden.
        price: nivel de la orden (obligatorio para `STOP` y `LIMIT`).
        valid_until_ms: vence ANTES de este instante (ms Unix). `None` = hasta el cierre de la
            vela siguiente del marco de la estrategia.
    """
    side: Side
    kind: EntryKind = EntryKind.MARKET
    price: float | None = None
    valid_until_ms: int | None = None

    def __post_init__(self) -> None:
        if self.kind != EntryKind.MARKET and (self.price is None or self.price <= 0):
            raise ValueError(f"una orden {self.kind} necesita un precio positivo")


@dataclass
class MarketView:
    """Lo que la estrategia sabe en un instante.

    Attributes:
        closed: velas CERRADAS de su marco (con las columnas de `precompute`), las ultimas
            `lookback`; la fila final es la ultima vela cerrada.
        forming: la vela en curso con los minutos ya terminados, o `None` justo al cierre.
        price: el ultimo precio conocido (cierre del ultimo minuto terminado).
        now_ms: el instante de la decision (fin del ultimo minuto terminado).
    """
    closed: pd.DataFrame
    forming: Candle | None
    price: float
    now_ms: int

    def with_forming(self) -> pd.DataFrame:
        """Las velas cerradas mas la vela en curso como ultima fila (solo OHLCV y `open_time`).

        Sirve para recalcular un indicador con la vela abierta, igual que en vivo. Las columnas
        de `precompute` de la fila en curso quedan vacias (NaN): hay que recalcularlas.

        Returns:
            Un DataFrame nuevo con una fila por vela.
        """
        base = self.closed[["open", "high", "low", "close", "volume", "open_time"]]
        if self.forming is None:
            return base.reset_index(drop=True)
        f = self.forming
        row = pd.DataFrame([{"open": f.open, "high": f.high, "low": f.low, "close": f.close,
                             "volume": f.volume, "open_time": f.open_time}])
        return pd.concat([base, row], ignore_index=True)


class ReplayStrategy:
    """Base de una estrategia para la repeticion minuto a minuto (todo es opcional).

    Attributes:
        timeframe: marco de sus velas (`1m` ... `1d`, `1w`).
        lookback: cuantas velas cerradas recibe en `MarketView.closed`.
        uses_minutes: si es `True`, se llama `on_minute` en cada minuto sin posicion (mas lento).
        max_hold_ms: si se define, cierra la posicion al cierre del minuto en que se cumple
            ese tiempo (motivo `TIME`), sin TP de respaldo.
    """
    timeframe: str = "4h"
    lookback: int = 300
    uses_minutes: bool = False
    max_hold_ms: int | None = None

    def precompute(self, df: pd.DataFrame) -> pd.DataFrame:
        """Agrega columnas de indicadores a TODAS las velas cerradas (una sola vez).

        Args:
            df: velas cerradas con `open`, `high`, `low`, `close`, `volume` y `open_time`.

        Returns:
            El DataFrame con las columnas agregadas.
        """
        return df

    def on_bar_close(self, view: MarketView) -> EntryOrder | None:
        """Decide al cierre de una vela (sin posicion abierta).

        Args:
            view: lo que se sabe al cierre (sin vela en curso).

        Returns:
            Una orden, o `None` para no hacer nada.
        """
        return None

    def on_minute(self, view: MarketView) -> EntryOrder | None:
        """Decide al final de cada minuto (sin posicion abierta y si `uses_minutes`).

        Args:
            view: lo que se sabe al terminar el minuto, con la vela en curso.

        Returns:
            Una orden, o `None` para no hacer nada.
        """
        return None

    def stop_price(self, view: MarketView, side: Side, entry_price: float) -> float | None:
        """SL de la operacion.

        Args:
            view: lo que se sabia al decidir la entrada.
            side: direccion.
            entry_price: precio de llenado.

        Returns:
            El precio del SL, o `None` para usar el SL de respaldo de la configuracion.
        """
        return None

    def take_profit_price(self, view: MarketView, side: Side, entry_price: float) -> float | None:
        """TP de la operacion.

        Args:
            view: lo que se sabia al decidir la entrada.
            side: direccion.
            entry_price: precio de llenado.

        Returns:
            El precio del TP, o `None`.
        """
        return None

    def trailing_distance(self, view: MarketView) -> float | None:
        """Distancia del trailing.

        Args:
            view: lo que se sabia al decidir la entrada.

        Returns:
            La distancia en precio, o `None` sin trailing.
        """
        return None


@dataclass
class ReplayStats:
    """Contadores de una corrida (para el informe y para detectar datos malos)."""
    minutes: int = 0
    candles_closed: int = 0
    candles_with_missing_minutes: int = 0
    orders_placed: int = 0
    orders_expired: int = 0
    entries_skipped_crossed_sl: int = 0
    same_minute_sl_and_tp: int = 0


@dataclass
class ReplayResult:
    """Resultado de `run_replay`."""
    trades: list[RawTrade] = field(default_factory=list)
    skipped: list[RawSkip] = field(default_factory=list)
    stats: ReplayStats = field(default_factory=ReplayStats)


@dataclass
class _Position:
    side: Side
    entry_time_ms: int
    entry_price: float
    qty: float
    margin_usdt: float
    notional: float
    leverage: int
    fee_entry_usdt: float
    original_sl: float
    tp: float | None
    trailing: float | None
    stop: float
    best: float
    liq: float
    slippage_entry_usdt: float
    sl_margin_loss_pct: float
    funding_paid_usdt: float = 0.0
    funding_approx: bool = False


class _Funding:
    """Tasa de funding vigente minuto a minuto, recorriendo los eventos una sola vez."""

    def __init__(self, events: list[tuple[int, float]]) -> None:
        self.events = sorted(events)
        self.median = statistics.median(r for _, r in self.events) if self.events else 0.0
        self.first = self.events[0][0] if self.events else None
        self.idx = 0
        self.current = self.median

    def rate_at(self, t: int) -> tuple[float, bool]:
        while self.idx < len(self.events) and self.events[self.idx][0] <= t:
            self.current = self.events[self.idx][1]
            self.idx += 1
        if self.first is None or t < self.first:
            return self.median, True
        return self.current, False


def _ms_to_dt(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, tz=UTC)


def candles_frame(candles: list[Candle]) -> pd.DataFrame:
    """Las velas como DataFrame.

    Args:
        candles: velas en orden.

    Returns:
        Un DataFrame con `open`, `high`, `low`, `close`, `volume` y `open_time`.
    """
    return pd.DataFrame({
        "open": [c.open for c in candles], "high": [c.high for c in candles],
        "low": [c.low for c in candles], "close": [c.close for c in candles],
        "volume": [c.volume for c in candles], "open_time": [c.open_time for c in candles],
    })


def run_replay(
    minutes: list[MinuteBar],
    strategy: ReplayStrategy,
    strategy_name: str,
    symbol: str,
    settings: Settings,
    funding_events: list[tuple[int, float]] | None = None,
    margin_tiers_json: str | None = None,
    funding_interval_hours: float = DEFAULT_FUNDING_INTERVAL_HOURS,
    data_end_ms: int | None = None,
) -> ReplayResult:
    """Simula una estrategia sobre los minutos de un simbolo, una posicion a la vez.

    Args:
        minutes: minutos ordenados por `open_time` (puede haber huecos).
        strategy: la estrategia.
        strategy_name: nombre con el que se etiquetan las operaciones.
        symbol: simbolo.
        settings: configuracion (margen, apalancamiento, comisiones, deslizamiento, topes).
        funding_events: eventos reales `(ms, tasa)`; sin eventos el funding vale 0 (aproximado).
        margin_tiers_json: escalones de margen del contrato (para la liquidacion).
        funding_interval_hours: intervalo de funding del contrato.
        data_end_ms: hasta donde llegan los datos; una vela cuyo cubo termina despues no se
            considera cerrada. Por defecto, el fin del ultimo minuto.

    Returns:
        Operaciones, entradas omitidas por el tope de riesgo y contadores.

    Raises:
        ValueError: si el marco es desconocido o los minutos no estan ordenados.
    """
    result = ReplayResult()
    stats = result.stats
    if not minutes:
        return result
    if any(b.open_time <= a.open_time for a, b in zip(minutes, minutes[1:], strict=False)):
        raise ValueError("los minutos tienen que estar ordenados y sin repetidos")

    tf = strategy.timeframe
    step = timeframe_ms(tf)
    end_ms = data_end_ms if data_end_ms is not None else minutes[-1].open_time + MINUTE_MS

    # Las velas cerradas dependen solo de sus minutos: se arman y se precalculan una vez.
    all_candles = [c for c in aggregate(minutes, tf) if c.open_time + step <= end_ms]
    closed_df = strategy.precompute(candles_frame(all_candles)) if all_candles else None
    index_of = {c.open_time: i for i, c in enumerate(all_candles)}
    stats.candles_with_missing_minutes = sum(1 for c in all_candles if c.missing)

    margin = settings.default_margin_usdt
    leverage = settings.leverage
    taker = settings.taker_fee_pct
    funding = _Funding(funding_events or [])

    position: _Position | None = None
    pending_market: EntryOrder | None = None
    active: EntryOrder | None = None
    active_until = 0
    forming: Candle | None = None
    forming_closed = True
    last_closed_index = -1
    last_view: MarketView | None = None

    def view_at(price: float, now_ms: int, with_forming: Candle | None) -> MarketView:
        assert closed_df is not None
        k = last_closed_index + 1
        closed = closed_df.iloc[max(0, k - strategy.lookback):k]
        return MarketView(closed=closed, forming=with_forming, price=price, now_ms=now_ms)

    def finalize(exit_price: float, exit_ms: int, reason: str) -> None:
        nonlocal position
        assert position is not None
        res = compute_close_result(
            position.side, position.qty, position.entry_price, exit_price,
            position.fee_entry_usdt, taker,
            extra_costs_usdt=position.slippage_entry_usdt + position.funding_paid_usdt,
        )
        slip_exit = exit_slippage_usdt(position.qty, exit_price, settings.backtest_slippage_bps)
        result.trades.append(RawTrade(
            strategy=strategy_name, symbol=symbol, timeframe=tf, side=position.side,
            entry_time=_ms_to_dt(position.entry_time_ms), exit_time=_ms_to_dt(exit_ms),
            entry_price=position.entry_price, exit_price=exit_price, qty=position.qty,
            margin_usdt=position.margin_usdt, leverage=position.leverage,
            fee_entry_usdt=position.fee_entry_usdt, fee_exit_usdt=res.fee_exit_usdt,
            slippage_cost_usdt=position.slippage_entry_usdt + slip_exit,
            funding_paid_usdt=position.funding_paid_usdt,
            funding_is_approximated=position.funding_approx,
            pnl_gross_usdt=res.pnl_gross_usdt, pnl_net_usdt=res.pnl_net_usdt - slip_exit,
            close_reason=reason, sl_margin_loss_pct=position.sl_margin_loss_pct,
        ))
        position = None

    def open_position(order: EntryOrder, fill_price: float, t_ms: int, maker: bool) -> bool:
        """Abre la posicion; `False` si se omitio (tope de riesgo o SL ya cruzado)."""
        nonlocal position
        view = last_view if last_view is not None else view_at(fill_price, t_ms, forming)
        side = order.side
        sl = strategy.stop_price(view, side, fill_price)
        fb_sl, fb_tp = fallback_sl_tp_prices(Signal(side.value), fill_price, settings)
        if sl is None:
            sl = fb_sl
        elif (side == Side.LONG and sl >= fill_price) or (side == Side.SHORT and sl <= fill_price):
            stats.entries_skipped_crossed_sl += 1
            return False
        trailing = strategy.trailing_distance(view)
        tp = strategy.take_profit_price(view, side, fill_price)
        if tp is None and trailing is None and not strategy.max_hold_ms:
            tp = fb_tp
        risk = margin_loss_pct(fill_price, sl, leverage)
        if risk > settings.max_sl_margin_loss_pct:
            result.skipped.append(RawSkip(
                strategy=strategy_name, symbol=symbol, timeframe=tf, side=side,
                ts=_ms_to_dt(t_ms), intended_sl_margin_loss_pct=risk,
            ))
            return False
        fill = compute_open_fill(margin, leverage, fill_price,
                                 settings.maker_fee_pct if maker else taker)
        liq = compute_liquidation_price(side, fill_price, leverage, fill.notional_usdt,
                                        margin_tiers_json)
        position = _Position(
            side=side, entry_time_ms=t_ms, entry_price=fill_price, qty=fill.qty,
            margin_usdt=margin, notional=fill.notional_usdt, leverage=leverage,
            fee_entry_usdt=fill.fee_entry_usdt, original_sl=sl, tp=tp, trailing=trailing,
            stop=sl, best=fill_price, liq=liq,
            slippage_entry_usdt=0.0 if maker else entry_slippage_usdt(
                fill.notional_usdt, settings.backtest_slippage_bps),
            sl_margin_loss_pct=risk,
        )
        return True

    def check_exit(bar: MinuteBar, adverse_only: bool) -> None:
        assert position is not None
        long = position.side == Side.LONG
        stop, liq, tp = position.stop, position.liq, position.tp
        # El umbral adverso mas cercano a la entrada es el que se toca primero.
        adverse = max(stop, liq) if long else min(stop, liq)
        reason = ("LIQUIDATION" if adverse == liq and adverse != stop
                  else "SL" if stop == position.original_sl else "TRAILING")
        t = bar.open_time
        if not adverse_only:
            if (bar.open <= adverse) if long else (bar.open >= adverse):
                finalize(bar.open, t, reason)
                return
            if tp is not None and ((bar.open >= tp) if long else (bar.open <= tp)):
                finalize(bar.open, t, "TP")
                return
        hit_adverse = (bar.low <= adverse) if long else (bar.high >= adverse)
        hit_tp = tp is not None and ((bar.high >= tp) if long else (bar.low <= tp))
        if hit_adverse:
            if hit_tp and not adverse_only:
                stats.same_minute_sl_and_tp += 1
            finalize(adverse, t, reason)
            return
        if hit_tp and not adverse_only:
            finalize(tp, t, "TP")

    def try_fill_active(bar: MinuteBar) -> tuple[float, bool] | None:
        """Precio de llenado de la orden activa en este minuto y si es maker, o `None`."""
        assert active is not None
        p = active.price
        assert p is not None
        long = active.side == Side.LONG
        if active.kind == EntryKind.STOP:
            if (bar.open >= p) if long else (bar.open <= p):
                return bar.open, False
            if (bar.high >= p) if long else (bar.low <= p):
                return p, False
            return None
        if (bar.low < p) if long else (bar.high > p):
            return p, True
        return None

    def place(order: EntryOrder | None, now_ms: int) -> None:
        nonlocal pending_market, active, active_until
        if order is None:
            return
        stats.orders_placed += 1
        if order.kind == EntryKind.MARKET:
            pending_market = order
            active = None
            return
        active = order
        active_until = (order.valid_until_ms if order.valid_until_ms is not None
                        else bucket_start(now_ms, tf) + step)

    def close_bar(bucket: int, now_ms: int, price: float) -> None:
        nonlocal last_closed_index, forming_closed, last_view, active
        forming_closed = True
        if bucket not in index_of:
            return
        last_closed_index = index_of[bucket]
        stats.candles_closed += 1
        if active is not None and active.valid_until_ms is None:
            active = None  # vence al cierre: la estrategia decide de nuevo
            stats.orders_expired += 1
        last_view = view_at(price, now_ms, None)
        if position is None and pending_market is None:
            place(strategy.on_bar_close(last_view), now_ms)

    for bar in minutes:
        stats.minutes += 1
        t = bar.open_time
        b = bucket_start(t, tf)
        # Una vela que quedo sin cerrar porque faltaron sus ultimos minutos se cierra aqui,
        # antes de usar el minuto nuevo.
        if forming is not None and b != forming.open_time and not forming_closed:
            close_bar(forming.open_time, forming.open_time + step, forming.close)

        just_opened_mid = False
        if position is None and pending_market is not None:
            order = pending_market
            pending_market = None
            open_position(order, bar.open, t, maker=False)
        if position is None and active is not None:
            if t >= active_until:
                active = None
                stats.orders_expired += 1
            else:
                filled = try_fill_active(bar)
                if filled is not None:
                    order, active = active, None
                    if open_position(order, filled[0], t, maker=filled[1]):
                        just_opened_mid = True

        if position is not None:
            rate, approx = funding.rate_at(t)
            ih = funding_interval_hours if funding_interval_hours > 0 else 8.0
            sign = 1.0 if position.side == Side.LONG else -1.0
            position.funding_paid_usdt += position.notional * rate * (MINUTE_HOURS / ih) * sign
            position.funding_approx = position.funding_approx or approx
            check_exit(bar, adverse_only=just_opened_mid)
        if position is not None and position.trailing is not None and not just_opened_mid:
            if position.side == Side.LONG:
                position.best = max(position.best, bar.high)
                position.stop = max(position.stop, position.best - position.trailing)
            else:
                position.best = min(position.best, bar.low)
                position.stop = min(position.stop, position.best + position.trailing)

        if forming is None or b != forming.open_time:
            forming = Candle.start(b, bar)
            forming_closed = False
        else:
            forming.add(bar)
        now = t + MINUTE_MS

        if (position is not None and strategy.max_hold_ms
                and now - position.entry_time_ms >= strategy.max_hold_ms):
            finalize(bar.close, t, "TIME")

        if now >= b + step:
            close_bar(b, now, bar.close)
        elif (position is None and pending_market is None and strategy.uses_minutes
              and closed_df is not None and last_closed_index >= 0):
            last_view = view_at(bar.close, now, forming)
            place(strategy.on_minute(last_view), now)

    if position is not None:
        last = minutes[-1]
        finalize(last.close, last.open_time, "END_OF_DATA")
    return result
