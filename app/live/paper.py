"""La posicion paper de una prueba en vivo, evaluada en cada operacion real del mercado (tick).

A diferencia del backtest por minutos aqui no hay que suponer nada dentro del minuto: cada tick
llega en orden.

- **Entrada a mercado:** se llena con el precio del primer tick posterior a la decision (comision
  taker y deslizamiento de la configuracion).
- **SL, trailing y liquidacion:** disparan cuando un tick llega al nivel o lo pasa y se llenan al
  **precio de ese tick** (nunca mejor que el nivel: si el mercado salto, se sale donde se transo).
- **TP:** dispara cuando un tick llega al nivel y se llena **en el nivel** (una orden limite de
  salida que descansa en el libro no mejora su precio).
- **Trailing:** se mueve con cada tick favorable y puede disparar desde el tick siguiente.
- **Funding:** se acumula por el tiempo transcurrido entre ticks con la tasa vigente.

Todo es puro (sin base ni red) y serializable, para que una posicion sobreviva a un reinicio.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from app.backtesting.liquidation import compute_liquidation_price
from app.config import Settings
from app.execution.pnl import compute_close_result, compute_open_fill
from app.persistence.models import Side
from app.strategies.base import Signal
from app.trading.sl_calc import fallback_sl_tp_prices, margin_loss_pct
from app.trading.slippage import entry_slippage_usdt, exit_slippage_usdt

MS_PER_HOUR = 3_600_000.0


@dataclass
class PaperPosition:
    """Una posicion paper abierta (todos los campos son numeros o texto: se guarda como JSON)."""
    side: str
    entry_time_ms: int
    entry_price: float
    qty: float
    margin_usdt: float
    notional: float
    leverage: int
    fee_entry_usdt: float
    slippage_entry_usdt: float
    original_sl: float
    stop: float
    tp: float | None
    trailing: float | None
    best: float
    liq: float
    sl_margin_loss_pct: float
    signal_time_ms: int
    decision_price: float
    last_tick_ms: int
    funding_paid_usdt: float = 0.0
    funding_approx: bool = False

    def to_dict(self) -> dict:
        """La posicion como diccionario.

        Returns:
            Un diccionario apto para JSON.
        """
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict) -> PaperPosition:
        """Reconstruye una posicion guardada.

        Args:
            raw: diccionario de `to_dict`.

        Returns:
            La posicion.
        """
        return cls(**raw)


@dataclass(frozen=True)
class ClosedTrade:
    """Una operacion paper cerrada."""
    side: str
    entry_time_ms: int
    exit_time_ms: int
    entry_price: float
    exit_price: float
    qty: float
    margin_usdt: float
    leverage: int
    fee_entry_usdt: float
    fee_exit_usdt: float
    slippage_cost_usdt: float
    funding_paid_usdt: float
    funding_approx: bool
    pnl_gross_usdt: float
    pnl_net_usdt: float
    close_reason: str
    sl_margin_loss_pct: float
    signal_time_ms: int
    decision_price: float


@dataclass(frozen=True)
class OpenRefusal:
    """Por que no se abrio una entrada (`RISK_CAP`, `SL_CROSSED` o `NO_SL`)."""
    reason: str
    detail: str


def open_position(side: Side, price: float, ts_ms: int, *, sl: float | None, tp: float | None,
                  trailing: float | None, settings: Settings, signal_time_ms: int,
                  decision_price: float,
                  margin_tiers_json: str | None = None) -> PaperPosition | OpenRefusal:
    """Abre una posicion a mercado con el precio de un tick.

    Args:
        side: direccion.
        price: precio del tick de llenado.
        ts_ms: hora del tick, ms Unix.
        sl: SL pedido por la estrategia, o `None` para el SL de respaldo.
        tp: TP pedido, o `None` (si tampoco hay trailing se usa el TP de respaldo).
        trailing: distancia del trailing en precio, o `None`.
        settings: configuracion (margen, apalancamiento, comisiones, deslizamiento, topes).
        signal_time_ms: cuando se decidio la entrada.
        decision_price: el precio que se conocia al decidir.
        margin_tiers_json: escalones de margen del contrato (para la liquidacion).

    Returns:
        La posicion, o el motivo del rechazo (tope de riesgo o SL ya cruzado).
    """
    fb_sl, fb_tp = fallback_sl_tp_prices(Signal(side.value), price, settings)
    if sl is None:
        sl = fb_sl
    elif (side == Side.LONG and sl >= price) or (side == Side.SHORT and sl <= price):
        return OpenRefusal("SL_CROSSED", f"SL {sl} del lado equivocado de la entrada {price}")
    if tp is None and trailing is None:
        tp = fb_tp
    risk = margin_loss_pct(price, sl, settings.leverage)
    if risk > settings.max_sl_margin_loss_pct:
        return OpenRefusal("RISK_CAP", f"el SL perderia {risk:.1f} % del margen")
    fill = compute_open_fill(settings.default_margin_usdt, settings.leverage, price,
                             settings.taker_fee_pct)
    liq = compute_liquidation_price(side, price, settings.leverage, fill.notional_usdt,
                                    margin_tiers_json)
    return PaperPosition(
        side=side.value, entry_time_ms=ts_ms, entry_price=price, qty=fill.qty,
        margin_usdt=settings.default_margin_usdt, notional=fill.notional_usdt,
        leverage=settings.leverage, fee_entry_usdt=fill.fee_entry_usdt,
        slippage_entry_usdt=entry_slippage_usdt(fill.notional_usdt,
                                                settings.backtest_slippage_bps),
        original_sl=sl, stop=sl, tp=tp, trailing=trailing, best=price, liq=liq,
        sl_margin_loss_pct=risk, signal_time_ms=signal_time_ms, decision_price=decision_price,
        last_tick_ms=ts_ms,
    )


def close_position(pos: PaperPosition, exit_price: float, ts_ms: int, reason: str,
                   settings: Settings) -> ClosedTrade:
    """Cierra una posicion a un precio y calcula su resultado.

    Args:
        pos: la posicion.
        exit_price: precio de salida.
        ts_ms: hora de la salida, ms Unix.
        reason: `SL`, `TRAILING`, `LIQUIDATION`, `TP` o `MANUAL`.
        settings: configuracion (comision y deslizamiento).

    Returns:
        La operacion cerrada.
    """
    side = Side(pos.side)
    res = compute_close_result(
        side, pos.qty, pos.entry_price, exit_price, pos.fee_entry_usdt, settings.taker_fee_pct,
        extra_costs_usdt=pos.slippage_entry_usdt + pos.funding_paid_usdt,
    )
    slip_exit = exit_slippage_usdt(pos.qty, exit_price, settings.backtest_slippage_bps)
    return ClosedTrade(
        side=pos.side, entry_time_ms=pos.entry_time_ms, exit_time_ms=ts_ms,
        entry_price=pos.entry_price, exit_price=exit_price, qty=pos.qty,
        margin_usdt=pos.margin_usdt, leverage=pos.leverage, fee_entry_usdt=pos.fee_entry_usdt,
        fee_exit_usdt=res.fee_exit_usdt, slippage_cost_usdt=pos.slippage_entry_usdt + slip_exit,
        funding_paid_usdt=pos.funding_paid_usdt, funding_approx=pos.funding_approx,
        pnl_gross_usdt=res.pnl_gross_usdt, pnl_net_usdt=res.pnl_net_usdt - slip_exit,
        close_reason=reason, sl_margin_loss_pct=pos.sl_margin_loss_pct,
        signal_time_ms=pos.signal_time_ms, decision_price=pos.decision_price,
    )


def on_tick(pos: PaperPosition, price: float, ts_ms: int, settings: Settings, *,
            funding_rate: float | None = None,
            funding_interval_hours: float = 8.0) -> ClosedTrade | None:
    """Aplica un tick a una posicion abierta: funding, salidas y trailing.

    Args:
        pos: la posicion (se modifica: funding, mejor precio y stop).
        price: precio del tick.
        ts_ms: hora del tick, ms Unix.
        settings: configuracion.
        funding_rate: tasa de funding vigente, o `None` si no se conoce (se acumula 0 y la
            operacion queda marcada como aproximada).
        funding_interval_hours: intervalo de funding del contrato.

    Returns:
        La operacion cerrada si el tick disparo una salida; si no, `None`.
    """
    long = pos.side == Side.LONG.value
    elapsed_h = max(0, ts_ms - pos.last_tick_ms) / MS_PER_HOUR
    pos.last_tick_ms = max(pos.last_tick_ms, ts_ms)
    if funding_rate is None:
        pos.funding_approx = True
    else:
        interval = funding_interval_hours if funding_interval_hours > 0 else 8.0
        sign = 1.0 if long else -1.0
        pos.funding_paid_usdt += pos.notional * funding_rate * (elapsed_h / interval) * sign

    # El umbral adverso mas cercano a la entrada es el que se toca primero.
    adverse = max(pos.stop, pos.liq) if long else min(pos.stop, pos.liq)
    if (price <= adverse) if long else (price >= adverse):
        if adverse == pos.liq and adverse != pos.stop:
            reason = "LIQUIDATION"
        else:
            reason = "SL" if pos.stop == pos.original_sl else "TRAILING"
        return close_position(pos, price, ts_ms, reason, settings)
    if pos.tp is not None and ((price >= pos.tp) if long else (price <= pos.tp)):
        return close_position(pos, pos.tp, ts_ms, "TP", settings)
    if pos.trailing is not None:
        if long:
            pos.best = max(pos.best, price)
            pos.stop = max(pos.stop, pos.best - pos.trailing)
        else:
            pos.best = min(pos.best, price)
            pos.stop = min(pos.stop, pos.best + pos.trailing)
    return None
