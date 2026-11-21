"""Aperturas y cierres que daria un bot de senales sobre velas ya cerradas (v4).

Sirve para comparar un bot con TradingView antes de dejarlo corriendo: recorre las velas con la
misma confluencia que el corredor (`app.explorer.confluence`) y las mismas reglas del bot:

- sin posicion, abre en la vela en que la entrada da la direccion del bot, salvo que la salida de
  la senal contraria en esa misma vela (entonces la omite, como el corredor);
- con posicion, la cierra en la vela en que la salida da la senal contraria.

No simula SL, TP, trailing ni liquidacion, ni el llenado en el tick siguiente: lista solo las
senales (la vela de la decision y su cierre). Una operacion real puede terminar antes por su SL o
su TP; desde ese momento la lista puede desfasarse del bot.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from app.explorer.confluence import WARMUP_BY_TIMEFRAME, compute_votes, confluence_signals
from app.live.config import TestConfig


@dataclass(frozen=True)
class BotSignal:
    """Una senal del bot.

    Attributes:
        action: `OPEN`, `CLOSE` o `SKIP` (entrada omitida por la regla de salida).
        side: direccion de la posicion (`LONG` o `SHORT`).
        open_time: apertura de la vela de la decision, ms Unix (la decision es a su cierre).
        close: cierre de esa vela.
    """
    action: str
    side: str
    open_time: int
    close: float


def bot_signals(cfg: TestConfig, frame: pd.DataFrame,
                btc: pd.DataFrame | None = None) -> list[BotSignal]:
    """Las aperturas y cierres de un bot sobre velas cerradas.

    Args:
        cfg: el bot (`kind = "bot"`, direccion `long` o `short`).
        frame: velas cerradas de la moneda (`open_time`, OHLC), de la mas vieja a la mas nueva.
        btc: velas de BTC del mismo marco, si algun indicador las usa.

    Returns:
        Las senales en orden. Solo desde la vela `WARMUP_BY_TIMEFRAME[cfg.timeframe]` (como el
        corredor, que no decide sin ese calentamiento).

    Raises:
        ValueError: si la configuracion no es un bot de una direccion.
    """
    if not cfg.is_bot or cfg.direction not in ("long", "short"):
        raise ValueError("solo para bots de senales de una direccion")
    feats = compute_votes(frame.reset_index(drop=True), cfg.timeframe, btc)
    long_in, short_in = confluence_signals(feats, cfg.indicators, cfg.window, cfg.min_agree)
    if cfg.exit_indicators:
        long_out, short_out = confluence_signals(feats, cfg.exit_indicators, cfg.window,
                                                 cfg.exit_min_agree)
    else:
        long_out = short_out = [False] * len(feats)
    is_long = cfg.direction == "long"
    side = "LONG" if is_long else "SHORT"
    entry = long_in if is_long else short_in
    against = short_out if is_long else long_out
    times = feats["open_time"].to_numpy(dtype="int64")
    closes = feats["close"].to_numpy(dtype=float)
    out: list[BotSignal] = []
    holding = False
    for i in range(WARMUP_BY_TIMEFRAME[cfg.timeframe], len(feats)):
        if holding:
            if against[i]:
                out.append(BotSignal("CLOSE", side, int(times[i]), float(closes[i])))
                holding = False
            continue
        if entry[i]:
            if against[i]:
                out.append(BotSignal("SKIP", side, int(times[i]), float(closes[i])))
            else:
                out.append(BotSignal("OPEN", side, int(times[i]), float(closes[i])))
                holding = True
    return out
