"""Aperturas y cierres de un bot de senales sobre velas cerradas (v4)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.live import signals as mod
from app.live.config import validate

WARM = 500


def bot(**kw):
    base = dict(name="b", symbols=["BTCUSDT"], timeframe="4h", indicators=["wt_cross"],
                kind="bot", direction="long", exit_indicators=["rf"], exit_name="pct",
                pct_exit=(1.0, None, None))
    base.update(kw)
    return validate(**base)


def fake(monkeypatch, n, entry_long=(), entry_short=(), exit_long=(), exit_short=()):
    frame = pd.DataFrame({"open_time": np.arange(n, dtype="int64") * 14_400_000,
                          "open": 1.0, "high": 1.0, "low": 1.0, "close": np.arange(n) + 1.0})
    monkeypatch.setattr(mod, "compute_votes", lambda df, tf, btc: df)

    def arr(idx):
        a = np.zeros(n, dtype=bool)
        a[list(idx)] = True
        return a

    def conf(feats, inds, window, min_agree):
        if tuple(inds) == ("wt_cross",):
            return arr(entry_long), arr(entry_short)
        return arr(exit_long), arr(exit_short)
    monkeypatch.setattr(mod, "confluence_signals", conf)
    return frame


def test_a_long_bot_opens_on_entry_and_closes_on_the_opposite_exit(monkeypatch):
    n = WARM + 20
    frame = fake(monkeypatch, n, entry_long=[WARM - 1, WARM + 2, WARM + 5, WARM + 9],
                 exit_long=[WARM + 4], exit_short=[WARM + 7, WARM + 9, WARM + 12])
    got = [(s.action, s.open_time // 14_400_000) for s in mod.bot_signals(bot(), frame)]
    # La entrada de la vela de calentamiento no cuenta; WARM+4 es salida del mismo lado (no
    # cierra); WARM+5 no entra (ya hay posicion); WARM+7 cierra; WARM+9 entrada con salida
    # contraria en la misma vela: se omite; WARM+12 sin posicion no hace nada.
    assert got == [("OPEN", WARM + 2), ("CLOSE", WARM + 7), ("SKIP", WARM + 9)]


def test_a_short_bot_mirrors_and_without_exit_rules_never_closes(monkeypatch):
    n = WARM + 10
    frame = fake(monkeypatch, n, entry_short=[WARM + 1, WARM + 3], exit_long=[WARM + 2])
    got = [(s.action, s.side, s.close) for s in mod.bot_signals(bot(direction="short"), frame)]
    assert got == [("OPEN", "SHORT", WARM + 2.0), ("CLOSE", "SHORT", WARM + 3.0),
                   ("OPEN", "SHORT", WARM + 4.0)]
    frame = fake(monkeypatch, n, entry_short=[WARM + 1, WARM + 3], exit_long=[WARM + 2])
    only = mod.bot_signals(bot(direction="short", exit_indicators=[]), frame)
    assert [s.action for s in only] == ["OPEN"]


def test_bot_signals_rejects_tests():
    with pytest.raises(ValueError):
        mod.bot_signals(validate(name="t", symbols=["BTCUSDT"], timeframe="4h",
                                 indicators=["rf"]), pd.DataFrame())
