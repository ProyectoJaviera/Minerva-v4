"""La posicion paper de una prueba en vivo, evaluada en cada operacion real del mercado (tick).

A diferencia del backtest por minutos aqui no hay que suponer nada dentro del minuto: cada tick
llega en orden.

- **Entrada a mercado:** se llena con el precio del primer tick posterior a la decision, con el
  margen y el apalancamiento de la prueba (comision taker y deslizamiento de la configuracion).
- **SL, trailing y liquidacion:** disparan cuando un tick llega al nivel o lo pasa y se llenan al
  **precio de ese tick** (nunca mejor que el nivel: si el mercado salto, se sale donde se transo).
- **TP:** dispara cuando un tick llega al nivel y se llena **en el nivel** (una orden limite de
  salida que descansa en el libro no mejora su precio).
- **TP parciales:** hasta 3 niveles, cada uno cierra un % de la cantidad INICIAL (el ultimo nivel
  que se toca cierra lo que quede si los porcentajes suman 100). Con `breakeven`, al tocarse el
  primero el SL pasa al precio de entrada. Lo que queda abierto sigue con el SL, el trailing y la
  liquidacion. Las comisiones de salida y el deslizamiento se cobran sobre cada cierre parcial.
- **Trailing:** se mueve con cada tick favorable y puede disparar desde el tick siguiente.
- **Funding:** se acumula por el tiempo transcurrido entre ticks con la tasa vigente, sobre la
  parte que sigue abierta.

Todo es puro (sin base ni red) y serializable, para que una posicion sobreviva a un reinicio.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from app.backtesting.liquidation import compute_liquidation_price
from app.config import Settings
from app.execution.pnl import compute_open_fill
from app.persistence.models import Side
from app.strategies.base import Signal
from app.trading.sl_calc import fallback_sl_tp_prices, margin_loss_pct
from app.trading.slippage import entry_slippage_usdt, exit_slippage_usdt

MS_PER_HOUR = 3_600_000.0
QTY_EPS = 1e-12


@dataclass
class PaperPosition:
    """Una posicion paper abierta (numeros, texto o listas: se guarda como JSON).

    `qty` es lo que sigue abierto; `qty_initial`, lo que se abrio. `tp_levels` son
    `[precio, fraccion de la cantidad inicial, hecho]` y `partials` los cierres parciales ya hechos
    (`[precio, cantidad, hora, motivo]`). Las posiciones guardadas antes de los TP parciales se leen
    con los valores por defecto.
    """
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
    qty_initial: float = 0.0
    tp_levels: list = field(default_factory=list)
    breakeven: bool = False
    partials: list = field(default_factory=list)
    realized_gross_usdt: float = 0.0
    realized_fee_exit_usdt: float = 0.0
    realized_slippage_exit_usdt: float = 0.0

    def __post_init__(self) -> None:
        if not self.qty_initial:
            self.qty_initial = self.qty

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
    """Una operacion paper cerrada (con sus cierres parciales, si los hubo).

    `exit_price` es el precio medio de salida ponderado por cantidad y `qty` la cantidad inicial.
    """
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
    partials: list = field(default_factory=list)


@dataclass(frozen=True)
class OpenRefusal:
    """Por que no se abrio una entrada (`RISK_CAP`, `SL_CROSSED` o `NO_SL`)."""
    reason: str
    detail: str


def open_position(side: Side, price: float, ts_ms: int, *, sl: float | None, tp: float | None,
                  trailing: float | None, settings: Settings, signal_time_ms: int,
                  decision_price: float, margin_tiers_json: str | None = None,
                  margin_usdt: float | None = None, leverage: int | None = None,
                  take_profits: list[tuple[float, float]] | None = None,
                  breakeven: bool = False) -> PaperPosition | OpenRefusal:
    """Abre una posicion a mercado con el precio de un tick.

    Args:
        side: direccion.
        price: precio del tick de llenado.
        ts_ms: hora del tick, ms Unix.
        sl: SL pedido por la estrategia, o `None` para el SL de respaldo.
        tp: TP unico pedido, o `None` (si tampoco hay trailing ni TP parciales se usa el de
            respaldo). Se ignora si hay `take_profits`.
        trailing: distancia del trailing en precio, o `None`.
        settings: configuracion (comisiones, deslizamiento, topes y valores por defecto).
        signal_time_ms: cuando se decidio la entrada.
        decision_price: el precio que se conocia al decidir.
        margin_tiers_json: escalones de margen del contrato (para la liquidacion).
        margin_usdt: margen de la operacion; por defecto, `DEFAULT_MARGIN_USDT`.
        leverage: apalancamiento; por defecto, `LEVERAGE`.
        take_profits: niveles parciales `(precio, fraccion de la cantidad inicial)`, en orden de
            cercania a la entrada.
        breakeven: al tocarse el primer TP parcial, el SL pasa al precio de entrada.

    Returns:
        La posicion, o el motivo del rechazo (tope de riesgo o SL ya cruzado).
    """
    margin = settings.default_margin_usdt if margin_usdt is None else float(margin_usdt)
    lev = settings.leverage if leverage is None else int(leverage)
    fb_sl, fb_tp = fallback_sl_tp_prices(Signal(side.value), price, settings)
    if sl is None:
        sl = fb_sl
    elif (side == Side.LONG and sl >= price) or (side == Side.SHORT and sl <= price):
        return OpenRefusal("SL_CROSSED", f"SL {sl} del lado equivocado de la entrada {price}")
    levels = []
    if take_profits:
        tp = None
        for level_price, frac in take_profits:
            wrong = level_price <= price if side == Side.LONG else level_price >= price
            if wrong or not 0 < frac <= 1:
                return OpenRefusal("TP_INVALID", f"TP parcial {level_price} invalido")
            levels.append([float(level_price), float(frac), False])
    elif tp is None and trailing is None:
        tp = fb_tp
    risk = margin_loss_pct(price, sl, lev)
    if risk > settings.max_sl_margin_loss_pct:
        return OpenRefusal("RISK_CAP", f"el SL perderia {risk:.1f} % del margen")
    fill = compute_open_fill(margin, lev, price, settings.taker_fee_pct)
    liq = compute_liquidation_price(side, price, lev, fill.notional_usdt, margin_tiers_json)
    return PaperPosition(
        side=side.value, entry_time_ms=ts_ms, entry_price=price, qty=fill.qty,
        margin_usdt=margin, notional=fill.notional_usdt, leverage=lev,
        fee_entry_usdt=fill.fee_entry_usdt,
        slippage_entry_usdt=entry_slippage_usdt(fill.notional_usdt,
                                                settings.backtest_slippage_bps),
        original_sl=sl, stop=sl, tp=tp, trailing=trailing, best=price, liq=liq,
        sl_margin_loss_pct=risk, signal_time_ms=signal_time_ms, decision_price=decision_price,
        last_tick_ms=ts_ms, qty_initial=fill.qty, tp_levels=levels, breakeven=breakeven,
    )


def _gross(pos: PaperPosition, exit_price: float, qty: float) -> float:
    move = exit_price - pos.entry_price
    return (move if pos.side == Side.LONG.value else -move) * qty


def _partial_close(pos: PaperPosition, price: float, qty: float, ts_ms: int, reason: str,
                   settings: Settings) -> None:
    qty = min(qty, pos.qty)
    pos.realized_gross_usdt += _gross(pos, price, qty)
    pos.realized_fee_exit_usdt += price * qty * settings.taker_fee_pct
    pos.realized_slippage_exit_usdt += exit_slippage_usdt(qty, price,
                                                          settings.backtest_slippage_bps)
    pos.qty -= qty
    pos.partials.append([price, qty, ts_ms, reason])


def close_position(pos: PaperPosition, exit_price: float, ts_ms: int, reason: str,
                   settings: Settings) -> ClosedTrade:
    """Cierra lo que queda de una posicion a un precio y calcula el resultado de toda la operacion.

    Args:
        pos: la posicion.
        exit_price: precio de salida de lo que queda abierto.
        ts_ms: hora de la salida, ms Unix.
        reason: `SL`, `TRAILING`, `BREAKEVEN`, `LIQUIDATION`, `TP`, `TPn` o `MANUAL`.
        settings: configuracion (comision y deslizamiento).

    Returns:
        La operacion cerrada, con los cierres parciales sumados.
    """
    rest = max(0.0, pos.qty)
    gross = pos.realized_gross_usdt + _gross(pos, exit_price, rest)
    fee_exit = pos.realized_fee_exit_usdt + exit_price * rest * settings.taker_fee_pct
    slip_exit = pos.realized_slippage_exit_usdt + (
        exit_slippage_usdt(rest, exit_price, settings.backtest_slippage_bps) if rest > 0 else 0.0)
    partials = list(pos.partials)
    if rest > QTY_EPS:
        partials.append([exit_price, rest, ts_ms, reason])
    total_qty = sum(p[1] for p in partials) or pos.qty_initial
    avg_exit = sum(p[0] * p[1] for p in partials) / total_qty if partials else exit_price
    net = gross - pos.fee_entry_usdt - fee_exit - pos.slippage_entry_usdt - slip_exit \
        - pos.funding_paid_usdt
    return ClosedTrade(
        side=pos.side, entry_time_ms=pos.entry_time_ms, exit_time_ms=ts_ms,
        entry_price=pos.entry_price, exit_price=avg_exit, qty=pos.qty_initial,
        margin_usdt=pos.margin_usdt, leverage=pos.leverage, fee_entry_usdt=pos.fee_entry_usdt,
        fee_exit_usdt=fee_exit, slippage_cost_usdt=pos.slippage_entry_usdt + slip_exit,
        funding_paid_usdt=pos.funding_paid_usdt, funding_approx=pos.funding_approx,
        pnl_gross_usdt=gross, pnl_net_usdt=net, close_reason=reason,
        sl_margin_loss_pct=pos.sl_margin_loss_pct, signal_time_ms=pos.signal_time_ms,
        decision_price=pos.decision_price, partials=partials if len(partials) > 1 else [],
    )


def on_tick(pos: PaperPosition, price: float, ts_ms: int, settings: Settings, *,
            funding_rate: float | None = None,
            funding_interval_hours: float = 8.0) -> ClosedTrade | None:
    """Aplica un tick a una posicion abierta: funding, salidas, TP parciales y trailing.

    Args:
        pos: la posicion (se modifica: funding, cierres parciales, mejor precio y stop).
        price: precio del tick.
        ts_ms: hora del tick, ms Unix.
        settings: configuracion.
        funding_rate: tasa de funding vigente, o `None` si no se conoce (se acumula 0 y la
            operacion queda marcada como aproximada).
        funding_interval_hours: intervalo de funding del contrato.

    Returns:
        La operacion cerrada si el tick cerro toda la posicion; si no, `None`.
    """
    long = pos.side == Side.LONG.value
    elapsed_h = max(0, ts_ms - pos.last_tick_ms) / MS_PER_HOUR
    pos.last_tick_ms = max(pos.last_tick_ms, ts_ms)
    if funding_rate is None:
        pos.funding_approx = True
    else:
        interval = funding_interval_hours if funding_interval_hours > 0 else 8.0
        sign = 1.0 if long else -1.0
        open_frac = pos.qty / pos.qty_initial if pos.qty_initial else 1.0
        pos.funding_paid_usdt += (pos.notional * open_frac * funding_rate
                                  * (elapsed_h / interval) * sign)

    # El umbral adverso mas cercano a la entrada es el que se toca primero.
    adverse = max(pos.stop, pos.liq) if long else min(pos.stop, pos.liq)
    if (price <= adverse) if long else (price >= adverse):
        if adverse == pos.liq and adverse != pos.stop:
            reason = "LIQUIDATION"
        elif pos.stop == pos.original_sl:
            reason = "SL"
        elif pos.stop == pos.entry_price and pos.trailing is None:
            reason = "BREAKEVEN"
        else:
            reason = "TRAILING"
        return close_position(pos, price, ts_ms, reason, settings)

    # TP parciales, del mas cercano al mas lejano.
    for n, level in enumerate(pos.tp_levels, start=1):
        level_price, frac, done = level
        if done or not ((price >= level_price) if long else (price <= level_price)):
            continue
        level[2] = True
        qty = frac * pos.qty_initial
        # Si este nivel cierra todo lo que queda, la operacion termina aqui.
        if qty >= pos.qty - QTY_EPS * max(1.0, pos.qty_initial):
            return close_position(pos, level_price, ts_ms, f"TP{n}", settings)
        _partial_close(pos, level_price, qty, ts_ms, f"TP{n}", settings)
        if pos.breakeven and n == 1:
            pos.stop = max(pos.stop, pos.entry_price) if long else min(pos.stop, pos.entry_price)

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
