"""Niveles Camarilla del dia UTC anterior (`app/indicators/levels.py`)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.indicators.levels import DAY_MS, camarilla_from_hlc, camarilla_levels

HOUR_MS = 3_600_000
DAY0 = 20_000 * DAY_MS  # medianoche UTC


def _hourly_days(day_stats, start=DAY0):
    """Velas de 1h; cada dia tiene su maximo, minimo y cierre exactos."""
    rows = []
    for d, (high, low, close) in enumerate(day_stats):
        for h in range(24):
            mid = (high + low) / 2
            rows.append({
                "open_time": start + d * DAY_MS + h * HOUR_MS,
                "high": high if h == 5 else mid + 0.1,
                "low": low if h == 17 else mid - 0.1,
                "close": close if h == 23 else mid,
            })
    return pd.DataFrame(rows)


def test_camarilla_formula_matches_the_standard_multipliers():
    levels = camarilla_from_hlc(110.0, 90.0, 100.0)
    assert levels["r4"] == pytest.approx(111.0)
    assert levels["r3"] == pytest.approx(105.5)
    assert levels["r2"] == pytest.approx(100 + 22 / 6)
    assert levels["r1"] == pytest.approx(100 + 22 / 12)
    assert levels["s1"] == pytest.approx(100 - 22 / 12)
    assert levels["s2"] == pytest.approx(100 - 22 / 6)
    assert levels["s3"] == pytest.approx(94.5)
    assert levels["s4"] == pytest.approx(89.0)


def test_every_bar_of_a_day_uses_the_previous_utc_day():
    df = _hourly_days([(110.0, 90.0, 100.0), (130.0, 70.0, 120.0)])
    out = camarilla_levels(df)
    assert out.iloc[:24].isna().all().all()  # el primer dia no tiene dia anterior
    day1 = out.iloc[24:48]
    assert (day1["cam_prev_high"] == 110.0).all()
    assert (day1["cam_prev_low"] == 90.0).all()
    assert (day1["cam_prev_close"] == 100.0).all()
    assert day1["cam_r3"].iloc[0] == pytest.approx(105.5)
    assert day1["cam_s4"].iloc[-1] == pytest.approx(89.0)


def test_the_utc_day_boundary_is_midnight():
    df = _hourly_days([(110.0, 90.0, 100.0), (130.0, 70.0, 120.0), (1.0, 0.5, 0.7)])
    out = camarilla_levels(df)
    assert out["cam_prev_high"].iloc[47] == 110.0  # 23:00 del dia 1: aun el dia 0
    assert out["cam_prev_high"].iloc[48] == 130.0  # 00:00 del dia 2: ya el dia 1


def test_an_incomplete_previous_day_gives_no_levels_unless_allowed():
    df = _hourly_days([(110.0, 90.0, 100.0), (130.0, 70.0, 120.0)])
    df = df.drop(index=10).reset_index(drop=True)  # falta una vela del dia 0
    out = camarilla_levels(df, interval_ms=HOUR_MS)
    assert out.iloc[23:].isna().all().all()
    lenient = camarilla_levels(df, interval_ms=HOUR_MS, require_complete=False)
    assert lenient["cam_prev_high"].iloc[-1] == 110.0


def test_a_nan_close_makes_the_day_incomplete():
    df = _hourly_days([(110.0, 90.0, 100.0), (130.0, 70.0, 120.0)])
    df.loc[3, "close"] = np.nan
    out = camarilla_levels(df, interval_ms=HOUR_MS)
    assert out["cam_r1"].iloc[24:].isna().all()


def test_a_missing_previous_day_gives_no_levels():
    df = _hourly_days([(110.0, 90.0, 100.0), (130.0, 70.0, 120.0), (1.0, 0.5, 0.7)])
    df = df[(df["open_time"] < DAY0 + DAY_MS) | (df["open_time"] >= DAY0 + 2 * DAY_MS)]
    out = camarilla_levels(df.reset_index(drop=True), interval_ms=HOUR_MS)
    assert out.isna().all().all()


def test_daily_bars_use_the_previous_bar():
    df = pd.DataFrame({"open_time": [DAY0, DAY0 + DAY_MS], "high": [110.0, 1.0],
                       "low": [90.0, 0.5], "close": [100.0, 0.7]})
    out = camarilla_levels(df)
    assert out["cam_r4"].iloc[1] == pytest.approx(111.0)


def test_interval_that_does_not_divide_a_day_is_rejected():
    df = pd.DataFrame({"open_time": [DAY0, DAY0 + 7 * HOUR_MS], "high": [1.0, 1.0],
                       "low": [0.5, 0.5], "close": [0.7, 0.7]})
    with pytest.raises(ValueError, match="dividir"):
        camarilla_levels(df)


def test_unsorted_open_time_is_rejected():
    df = pd.DataFrame({"open_time": [DAY0 + HOUR_MS, DAY0], "high": [1.0, 1.0],
                       "low": [0.5, 0.5], "close": [0.7, 0.7]})
    with pytest.raises(ValueError, match="creciente"):
        camarilla_levels(df)


def test_missing_open_time_is_rejected():
    with pytest.raises(ValueError, match="open_time"):
        camarilla_levels(pd.DataFrame({"high": [1.0], "low": [0.5], "close": [0.7]}))


def test_empty_frame_returns_empty_levels():
    df = pd.DataFrame({"open_time": [], "high": [], "low": [], "close": []})
    assert camarilla_levels(df).empty


# --- Fibonacci de Frost -----------------------------------------------------------------------


def test_frost_fibonacci_zigzag_and_levels_by_hand():
    from app.indicators.levels import FIB_LEVEL_COLUMNS, frost_fibonacci

    df = pd.DataFrame({
        #       0     1     2     3     4    5     6     7
        "high": [10.0, 11.0, 12.0, 11.0, 10.0, 12.0, 14.0, 13.0],
        "low": [9.0, 10.0, 11.0, 10.0, 8.0, 11.0, 12.0, 9.5],
    })
    out = frost_fibonacci(df, period=3)
    assert FIB_LEVEL_COLUMNS == ("fib_000", "fib_382", "fib_500", "fib_618", "fib_812",
                                 "fib_1000", "fib_1250", "fib_1500")
    # 2: maximo de 3 velas (12) -> pivote; 3: minimo empatado (10) -> pivote bajista; 4: minimo
    # mas bajo (8) corre ese pivote; 5: maximo (12) -> tercer pivote: v1 = 8 (vela 4), v2 = 12.
    assert out["fib_v1"].iloc[:5].isna().all() and (out["fib_leg"].iloc[:5] == 0).all()
    row = out.iloc[5]
    assert (row["fib_v1"], row["fib_v2"], row["fib_v1_pos"], row["fib_v2_pos"]) == (8, 12, 4, 2)
    assert row["fib_leg"] == -1
    assert row["fib_000"] == 8 and row["fib_500"] == 10 and row["fib_1000"] == 12
    assert row["fib_1500"] == 14
    # 6: maximo mas alto (14) corre el pivote en curso: v1 y v2 no cambian.
    assert (out["fib_v1"].iat[6], out["fib_v2"].iat[6]) == (8, 12)
    # 7: minimo de 3 velas -> pivote nuevo: v1 = 14 (vela 6), v2 = 8; pierna de subida.
    row = out.iloc[7]
    assert (row["fib_v1"], row["fib_v2"], row["fib_v1_pos"], row["fib_leg"]) == (14, 8, 6, 1)
    assert row["fib_500"] == 11 and row["fib_618"] == pytest.approx(14 - 6 * 0.618)
    with pytest.raises(ValueError):
        frost_fibonacci(df, period=0)
    with pytest.raises(ValueError):
        frost_fibonacci(df.drop(columns="low"))


def _fib_frame(leg: int, v1: float, v2: float, n: int) -> pd.DataFrame:
    from app.indicators.levels import FIB_LEVEL_COLUMNS, FROST_FIB_RATIOS

    out = pd.DataFrame({"fib_v1": [v1] * n, "fib_v2": [v2] * n, "fib_v1_pos": [0] * n,
                        "fib_v2_pos": [0] * n, "fib_leg": [leg] * n})
    for r, col in zip(FROST_FIB_RATIOS, FIB_LEVEL_COLUMNS, strict=True):
        out[col] = v1 + (v2 - v1) * r
    return out


def test_fib_bounce_long_needs_touch_close_above_and_no_break_of_100():
    from app.indicators.levels import fib_bounce_signals

    fib = _fib_frame(1, 14.0, 8.0, 6)  # pierna de subida 8 -> 14; el 50 % es 11
    df = pd.DataFrame({
        #        0     1     2     3     4     5
        "high": [14.0, 13.0, 12.0, 11.5, 11.5, 11.6],
        "low": [13.0, 11.5, 10.8, 10.5, 7.5, 10.9],
        "close": [13.5, 12.0, 11.5, 10.9, 11.2, 11.3],
    })
    long, short = fib_bounce_signals(df, fib, 0.5)
    # 1: no toca; 2: toca 11 y cierra sobre -> largo; 3: cierra bajo; 4: perfora el 100 % (8);
    # 5: ya se perforo desde el pivote -> nada.
    assert long.tolist() == [False, False, True, False, False, False]
    assert not short.any()
    # nivel 0 (el maximo v1 = 14): largo si la vela lo pasa y cierra sobre el
    df0 = pd.DataFrame({"high": [14.0, 13.0, 14.5], "low": [13.0, 12.0, 12.5],
                        "close": [13.5, 12.5, 14.2]})
    long0, _ = fib_bounce_signals(df0, _fib_frame(1, 14.0, 8.0, 3), 0.0)
    assert long0.tolist() == [False, False, True]
    with pytest.raises(ValueError):
        fib_bounce_signals(df, fib, 0.7)


def test_fib_bounce_short_is_the_mirror():
    from app.indicators.levels import fib_bounce_signals

    fib = _fib_frame(-1, 8.0, 14.0, 5)  # pierna de bajada 14 -> 8; el 61,8 % es 11.708
    df = pd.DataFrame({
        "high": [9.0, 11.0, 11.8, 14.5, 11.8],
        "low": [8.0, 10.0, 11.0, 11.0, 11.0],
        "close": [8.5, 10.5, 11.2, 11.3, 11.2],
    })
    long, short = fib_bounce_signals(df, fib, 0.618)
    # 1: no llega; 2: toca y cierra bajo -> corto; 3: perfora el 100 % (14); 4: ya roto.
    assert short.tolist() == [False, False, True, False, False]
    assert not long.any()


def test_fib_break_crosses_by_hand_in_both_directions():
    from app.indicators.levels import fib_break_signals

    fib = _fib_frame(1, 14.0, 8.0, 7)  # el 50 % es 11
    fib.loc[0, "fib_500"] = float("nan")
    df = pd.DataFrame({"close": [10.0, 10.5, 11.0, 11.5, 11.2, 10.0, 12.0]})
    long, short = fib_break_signals(df, fib, 0.5)
    # 1: nivel anterior NaN -> nada; 2: llega justo a 11 (no lo cruza); 3: desde 11 cierra 11.5 ->
    # largo; 4: sigue arriba; 5: cierra 10 -> corto; 6: vuelve a cerrar arriba -> largo.
    assert long.tolist() == [False, False, False, True, False, False, True]
    assert short.tolist() == [False, False, False, False, False, True, False]
    # sin condicion de pierna: con una pierna de bajada el cruce cuenta igual, tambien en el 150 %
    fib_down = _fib_frame(-1, 8.0, 14.0, 3)  # el 150 % es 17
    l2, s2 = fib_break_signals(pd.DataFrame({"close": [16.0, 17.5, 16.5]}), fib_down, 1.5)
    assert l2.tolist() == [False, True, False] and s2.tolist() == [False, False, True]
    with pytest.raises(ValueError):
        fib_break_signals(df, fib, 0.3)
