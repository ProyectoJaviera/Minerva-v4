"""Resumen de los resultados de las pruebas en vivo (compartido por el comando y la API).

v4: cada prueba trae a su gemelo de control al azar (`control`), la frecuencia de senales que usa
el gemelo (`rate`) y la comparacion con el (`vs_control`, ver `app.live.control.compare`).
"""

from __future__ import annotations

import time
from pathlib import Path

from app.live.control import compare, signal_rate
from app.live.paper import ClosedTrade, PaperPosition
from app.live.store import LiveStore, StoredTest

RUNNER_ALIVE_SECONDS = 60
# Un precio sin ticks nuevos en mas de esto se marca como viejo en el dashboard.
PRICE_STALE_SECONDS = 30


def position_view(pos: PaperPosition, price: float | None, taker_fee_pct: float,
                  slippage_bps: float) -> dict:
    """Los valores de una posicion abierta a un precio, como los muestra un exchange (v4).

    Args:
        pos: la posicion.
        price: ultimo precio de la moneda, o `None` si no se conoce.
        taker_fee_pct: comision taker (fraccion) para estimar el cierre.
        slippage_bps: deslizamiento de salida en puntos basicos.

    Returns:
        `size_pct` (lo que sigue abierto, %), `notional` al precio actual, `upnl` (PnL no
        realizado bruto de la parte abierta, como el de un exchange), `roe_pct` (sobre el margen de
        la parte abierta), `realized` (neto de los TP parciales ya hechos), `net_if_closed` (lo que
        dejaria toda la operacion si se cerrara ahora: con comisiones, deslizamiento y funding),
        `next_tp` y las distancias en % del precio actual al SL, al proximo TP y a la liquidacion
        (positivas si falta recorrido). Sin precio, los valores en vivo son `None`.
    """
    long = pos.side == "LONG"
    sign = 1.0 if long else -1.0
    open_frac = pos.qty / pos.qty_initial if pos.qty_initial else 1.0
    pending = [lv for lv in pos.tp_levels if not lv[2]]
    next_tp = pending[0][0] if pending else pos.tp
    realized = (pos.realized_gross_usdt - pos.realized_fee_exit_usdt
                - pos.realized_slippage_exit_usdt)
    out = {"size_pct": 100 * open_frac, "next_tp": next_tp, "realized": realized,
           "funding": pos.funding_paid_usdt, "notional": None, "upnl": None, "roe_pct": None,
           "net_if_closed": None, "to_sl_pct": None, "to_tp_pct": None, "to_liq_pct": None}
    if price is None or price <= 0:
        return out
    upnl = (price - pos.entry_price) * pos.qty * sign
    margin_open = pos.margin_usdt * open_frac
    exit_costs = price * pos.qty * taker_fee_pct + price * pos.qty * slippage_bps / 10_000
    out.update({
        "notional": price * pos.qty, "upnl": upnl,
        "roe_pct": upnl / margin_open * 100 if margin_open else None,
        "net_if_closed": (upnl + realized - pos.fee_entry_usdt - pos.slippage_entry_usdt
                          - pos.funding_paid_usdt - exit_costs),
        "to_sl_pct": sign * (price - pos.stop) / price * 100,
        "to_tp_pct": None if next_tp is None else sign * (next_tp - price) / price * 100,
        "to_liq_pct": sign * (price - pos.liq) / price * 100,
    })
    return out


def live_positions(path: str | Path, taker_fee_pct: float, slippage_bps: float,
                   now_s: float | None = None) -> dict:
    """Todas las posiciones abiertas con sus valores al ultimo precio, para el dashboard (v4).

    Args:
        path: archivo de pruebas.
        taker_fee_pct: comision taker (fraccion).
        slippage_bps: deslizamiento en puntos basicos.
        now_s: instante en segundos (para las pruebas); por defecto, el reloj.

    Returns:
        `now_ms` y `positions`: una fila por posicion con `test_id`, `is_control`, `symbol`, los
        campos de la posicion, `price`, `price_ts_ms`, `price_stale` y los de `position_view`.

    Efectos:
        Lee el archivo de pruebas en solo lectura.
    """
    now = time.time() if now_s is None else now_s
    now_ms = int(now * 1000)
    if not Path(path).is_file():
        return {"now_ms": now_ms, "positions": []}
    with LiveStore(path, read_only=True) as store:
        controls = {t.id for t in store.tests() if t.control_of is not None}
        prices = store.prices()
        rows = []
        for (test_id, symbol), pos in sorted(store.positions().items()):
            price, ts = prices.get(symbol, (None, None))
            stale = ts is None or now_ms - ts > PRICE_STALE_SECONDS * 1000
            rows.append({
                "test_id": test_id, "is_control": test_id in controls, "symbol": symbol,
                "side": pos.side, "entry_time_ms": pos.entry_time_ms,
                "entry_price": pos.entry_price, "qty": pos.qty, "qty_initial": pos.qty_initial,
                "margin_usdt": pos.margin_usdt, "leverage": pos.leverage, "stop": pos.stop,
                "liq": pos.liq, "tp_levels": pos.tp_levels, "tp": pos.tp,
                "trailing": pos.trailing, "price": price, "price_ts_ms": ts,
                "price_stale": stale,
                **position_view(pos, price, taker_fee_pct, slippage_bps),
            })
    return {"now_ms": now_ms, "positions": rows}


def summarize(trades: list[ClosedTrade]) -> dict:
    """Resumen de las operaciones cerradas de una prueba.

    Args:
        trades: operaciones cerradas.

    Returns:
        Un diccionario con `trades`, `wins`, `winrate`, `pf` (`None` sin ganancias ni perdidas,
        `inf` sin perdidas), `net`, `fees` y `avg_entry_delay_ms` (demora media entre la
        decision y el llenado).
    """
    gains = sum(t.pnl_net_usdt for t in trades if t.pnl_net_usdt > 0)
    losses = -sum(t.pnl_net_usdt for t in trades if t.pnl_net_usdt < 0)
    wins = sum(1 for t in trades if t.pnl_net_usdt > 0)
    pf = None if not trades or (gains == 0 and losses == 0) else (
        float("inf") if losses == 0 else gains / losses)
    delays = [t.entry_time_ms - t.signal_time_ms for t in trades]
    return {"trades": len(trades), "wins": wins,
            "winrate": wins / len(trades) if trades else None, "pf": pf,
            "net": sum(t.pnl_net_usdt for t in trades),
            "fees": sum(t.fee_entry_usdt + t.fee_exit_usdt for t in trades),
            "avg_entry_delay_ms": sum(delays) / len(delays) if delays else None}


def _json_pf(pf: float | None) -> float | str | None:
    return "inf" if pf is not None and pf == float("inf") else pf


def _summary(trades: list[ClosedTrade]) -> dict:
    s = summarize(trades)
    s["pf"] = _json_pf(s["pf"])
    return s


def _control_block(store: LiveStore, test: StoredTest, tests: list[StoredTest],
                   own: list[ClosedTrade], positions: dict) -> dict:
    """El gemelo al azar de una prueba, su frecuencia y la comparacion (o `None`s si no tiene)."""
    twin = next((t for t in tests if t.control_of == test.id), None)
    eligible, signals = store.rates().get(test.id, (0, 0))
    rate = {"eligible_bars": eligible, "signals": signals,
            "rate": signal_rate(eligible, signals)}
    if twin is None:
        return {"control": None, "rate": rate, "vs_control": None}
    twin_trades = [tr for _, tr in store.trades(twin.id)]
    return {
        "control": {"id": twin.id, "name": twin.config.name, "status": twin.status,
                    "created_at_ms": twin.created_at_ms, "summary": _summary(twin_trades),
                    "open_positions": sum(1 for (tid, _s) in positions if tid == twin.id)},
        "rate": rate,
        "vs_control": compare([t.pnl_net_usdt for t in own],
                              [t.pnl_net_usdt for t in twin_trades]),
    }


def overview(path: str | Path, now_s: float | None = None) -> dict:
    """Estado del corredor y resumen de todas las pruebas, para el dashboard.

    Args:
        path: archivo de pruebas.
        now_s: instante en segundos (para las pruebas); por defecto, el reloj.

    Returns:
        `runner` (`alive`, `last_beat_ms`, `detail`, `bot_on`, `switch_changed_ms`) y `tests`
        (una fila por prueba con su
        configuracion, estado, resumen y posiciones abiertas). Sin archivo, listas vacias.

    Efectos:
        Lee el archivo de pruebas en solo lectura.
    """
    now = time.time() if now_s is None else now_s
    if not Path(path).is_file():
        return {"runner": {"alive": False, "last_beat_ms": None, "detail": None, "bot_on": True,
                           "switch_changed_ms": None}, "tests": []}
    with LiveStore(path, read_only=True) as store:
        beat = store.heartbeat()
        bot_on = store.bot_on()
        switch_changed = store.bot_switch_changed_at()
        positions = store.positions()
        rows = []
        tests = store.tests()
        for t in tests:
            if t.control_of is not None:
                continue  # el gemelo va dentro de su prueba
            own = [tr for _, tr in store.trades(t.id)]
            rows.append({
                "id": t.id, "status": t.status, "created_at_ms": t.created_at_ms,
                "stopped_at_ms": t.stopped_at_ms, "config": _config_dict(t.config),
                "summary": _summary(own),
                "open_positions": sum(1 for (tid, _s) in positions if tid == t.id),
                **_control_block(store, t, tests, own, positions),
            })
    return {"runner": {"alive": beat is not None and now - beat[0] / 1000 < RUNNER_ALIVE_SECONDS,
                       "last_beat_ms": None if beat is None else beat[0],
                       "detail": None if beat is None else beat[1], "bot_on": bot_on,
                       "switch_changed_ms": switch_changed},
            "tests": rows}


def detail(path: str | Path, test_id: int, trades_limit: int = 100,
           events_limit: int = 40) -> dict | None:
    """Detalle de una prueba: configuracion, resumen, posiciones, operaciones y eventos.

    Args:
        path: archivo de pruebas.
        test_id: la prueba.
        trades_limit: cuantas operaciones (las mas recientes).
        events_limit: cuantos eventos (los mas recientes).

    Returns:
        El detalle, o `None` si la prueba no existe.

    Efectos:
        Lee el archivo de pruebas en solo lectura.
    """
    if not Path(path).is_file():
        return None
    with LiveStore(path, read_only=True) as store:
        tests = store.tests()
        test = next((t for t in tests if t.id == test_id), None)
        if test is None:
            return None
        trades = store.trades(test_id)
        own = [tr for _, tr in trades]
        s = _summary(own)
        all_positions = store.positions()
        positions = [{"symbol": sym, **pos.to_dict()}
                     for (tid, sym), pos in sorted(all_positions.items()) if tid == test_id]
        block = ({"control": None, "rate": None, "vs_control": None}
                 if test.control_of is not None
                 else _control_block(store, test, tests, own, all_positions))
        events = [{"ts_ms": e[0], "symbol": e[2], "kind": e[3], "detail": e[4]}
                  for e in store.events(test_id, limit=events_limit)]
    return {
        "id": test.id, "status": test.status, "created_at_ms": test.created_at_ms,
        "stopped_at_ms": test.stopped_at_ms, "config": _config_dict(test.config), "summary": s,
        "positions": positions,
        "trades": [{"symbol": sym, **tr.__dict__} for sym, tr in trades[-trades_limit:]][::-1],
        "events": events, "control_of": test.control_of, **block,
    }


def _config_dict(cfg) -> dict:
    return {"name": cfg.name, "symbols": list(cfg.symbols), "timeframe": cfg.timeframe,
            "indicators": list(cfg.indicators), "mode": cfg.mode, "exit_name": cfg.exit_name,
            "pct_exit": None if cfg.pct_exit is None else list(cfg.pct_exit),
            "window": cfg.window, "min_agree": cfg.min_agree, "eval_seconds": cfg.eval_seconds,
            "margin_usdt": cfg.margin_usdt, "leverage": cfg.leverage, "direction": cfg.direction,
            "take_profits": [list(tp) for tp in cfg.take_profits], "breakeven": cfg.breakeven,
            "max_open": cfg.max_open}
