"""Resumen de los resultados de las pruebas en vivo (compartido por el comando y la API)."""

from __future__ import annotations

import time
from pathlib import Path

from app.live.paper import ClosedTrade
from app.live.store import LiveStore

RUNNER_ALIVE_SECONDS = 60


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
        for t in store.tests():
            s = summarize([tr for _, tr in store.trades(t.id)])
            s["pf"] = _json_pf(s["pf"])
            rows.append({
                "id": t.id, "status": t.status, "created_at_ms": t.created_at_ms,
                "stopped_at_ms": t.stopped_at_ms, "config": _config_dict(t.config),
                "summary": s,
                "open_positions": sum(1 for (tid, _s) in positions if tid == t.id),
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
        test = next((t for t in store.tests() if t.id == test_id), None)
        if test is None:
            return None
        trades = store.trades(test_id)
        s = summarize([tr for _, tr in trades])
        s["pf"] = _json_pf(s["pf"])
        positions = [{"symbol": sym, **pos.to_dict()}
                     for (tid, sym), pos in sorted(store.positions().items()) if tid == test_id]
        events = [{"ts_ms": e[0], "symbol": e[2], "kind": e[3], "detail": e[4]}
                  for e in store.events(test_id, limit=events_limit)]
    return {
        "id": test.id, "status": test.status, "created_at_ms": test.created_at_ms,
        "stopped_at_ms": test.stopped_at_ms, "config": _config_dict(test.config), "summary": s,
        "positions": positions,
        "trades": [{"symbol": sym, **tr.__dict__} for sym, tr in trades[-trades_limit:]][::-1],
        "events": events,
    }


def _config_dict(cfg) -> dict:
    return {"name": cfg.name, "symbols": list(cfg.symbols), "timeframe": cfg.timeframe,
            "indicators": list(cfg.indicators), "mode": cfg.mode, "exit_name": cfg.exit_name,
            "pct_exit": None if cfg.pct_exit is None else list(cfg.pct_exit),
            "window": cfg.window, "min_agree": cfg.min_agree, "eval_seconds": cfg.eval_seconds,
            "margin_usdt": cfg.margin_usdt, "leverage": cfg.leverage, "direction": cfg.direction,
            "take_profits": [list(tp) for tp in cfg.take_profits], "breakeven": cfg.breakeven,
            "max_open": cfg.max_open}
