"""Seccion «Indicadores» (exploratoria): confluencia, validacion, archivo de solo lectura, corte y
corrida de punta a punta con velas sinteticas en un archivo temporal."""

from __future__ import annotations

import os
import random
from datetime import UTC, datetime

import numpy as np
import pandas as pd
import pytest
from fastapi import HTTPException

from app.api.routes import explorer as route
from app.config import Settings
from app.explorer import confluence, service
from app.market.ohlcv_history import interval_to_ms
from app.persistence.database import Database
from app.persistence.models import OHLCVBar
from app.persistence.repositories import ohlcv_repo
from app.strategies.base import Signal

START_MS = int(datetime(2026, 7, 20, tzinfo=UTC).timestamp() * 1000)
END_MS = int(datetime(2026, 10, 20, tzinfo=UTC).timestamp() * 1000)  # pasa el limite reservado


async def _build_history(path: str, symbols=("BTCUSDT", "ETHUSDT")) -> None:
    db = Database(path)
    await db.connect()
    rng = random.Random(7)
    try:
        for symbol in symbols:
            for interval in ("15m", "1h", "4h", "1d", "1w"):
                step = interval_to_ms(interval)
                price = 100.0
                last, mark = [], []
                for i in range((END_MS - START_MS) // step):
                    t = START_MS + i * step
                    o = price
                    c = price * (1 + rng.uniform(-0.015, 0.015))
                    h, low = max(o, c) * 1.004, min(o, c) * 0.996
                    for rows, pt in ((last, "LAST_PRICE"), (mark, "MARK_PRICE")):
                        rows.append(OHLCVBar(symbol=symbol, interval=interval, price_type=pt,
                                             open_time=t, open=o, high=h, low=low, close=c))
                    price = c
                await ohlcv_repo.upsert_bars(db, last)
                await ohlcv_repo.upsert_bars(db, mark)
    finally:
        await db.close()


@pytest.fixture
async def history(tmp_path) -> str:
    path = str(tmp_path / "history_v4.db")
    await _build_history(path)
    return path


def _settings(path: str) -> Settings:
    return Settings(_env_file=None, EXPLORER_HISTORY_DB=path)


def _run(history: str, **kw):
    kw.setdefault("window", confluence.DEFAULT_WINDOW)
    kw.setdefault("exit", confluence.DEFAULT_EXIT)
    return route.run(**kw, settings=_settings(history))


# --- confluencia -------------------------------------------------------------------------------


def test_event_votes_last_for_the_window_and_the_most_recent_side_wins():
    long_ev = np.array([0, 1, 0, 0, 0, 0, 0, 1, 0, 0], dtype=bool)
    short_ev = np.array([0, 0, 0, 0, 0, 0, 0, 0, 1, 0], dtype=bool)
    bull, bear = confluence.event_votes(long_ev, short_ev, 3)
    assert bull.astype(int).tolist() == [0, 1, 1, 1, 0, 0, 0, 1, 0, 0]
    assert bear.astype(int).tolist() == [0, 0, 0, 0, 0, 0, 0, 0, 1, 1]
    bull1, _ = confluence.event_votes(long_ev, short_ev, 1)
    assert bull1.tolist() == long_ev.tolist()
    both = np.array([1, 0], dtype=bool)
    b, r = confluence.event_votes(both, both, 5)
    assert not b.any() and not r.any()


def _features(bull_a, bear_a, long_b, short_b) -> pd.DataFrame:
    return pd.DataFrame({"bull_ema": bull_a, "bear_ema": bear_a,
                         "long_sfp": long_b, "short_sfp": short_b}).astype(bool)


def test_confluence_enters_when_all_selected_start_to_agree():
    f = _features([0, 1, 1, 1, 1, 0, 0, 0], [1, 0, 0, 0, 0, 1, 1, 1],
                  [0, 0, 0, 1, 0, 0, 0, 0], [0, 0, 0, 0, 0, 0, 1, 0])
    long, short = confluence.confluence_signals(f, ["ema"], 1)
    assert long.astype(int).tolist() == [0, 1, 0, 0, 0, 0, 0, 0]  # un estado: entra al cambiar
    assert short.astype(int).tolist() == [1, 0, 0, 0, 0, 1, 0, 0]
    long, short = confluence.confluence_signals(f, ["ema", "sfp"], 2)
    assert long.astype(int).tolist() == [0, 0, 0, 1, 0, 0, 0, 0]  # EMA y SFP alcistas a la vez
    assert short.astype(int).tolist() == [0, 0, 0, 0, 0, 0, 1, 0]
    long, _ = confluence.confluence_signals(f, ["sfp"], 3)
    assert long.astype(int).tolist() == [0, 0, 0, 1, 0, 0, 0, 0]  # una senal, no cada vela


def test_a_single_state_indicator_enters_on_its_flips_like_the_range_filter_trigger():
    from app.research.combos import compute_features

    rng = random.Random(3)
    price, rows = 100.0, []
    for i in range(1500):
        c = price * (1 + rng.uniform(-0.02, 0.02))
        rows.append({"open_time": START_MS + i * 3_600_000, "open": price,
                     "high": max(price, c) * 1.003, "low": min(price, c) * 0.997, "close": c})
        price = c
    feats = compute_features(pd.DataFrame(rows), "1h")
    long, short = confluence.confluence_signals(feats, ["rf"], 1)
    assert long.tolist() == feats["long_rf_flip"].astype(bool).tolist()
    assert short.tolist() == feats["short_rf_flip"].astype(bool).tolist()


def test_range_filter_modes_vote_with_frost_sensitivity():
    from app.indicators.trend_filters import RANGE_FILTER_SENSITIVITY, range_filter

    rng = random.Random(5)
    price, rows = 100.0, []
    for i in range(1500):
        c = price * (1 + rng.uniform(-0.02, 0.02))
        rows.append({"open_time": START_MS + i * 3_600_000, "open": price,
                     "high": max(price, c) * 1.003, "low": min(price, c) * 0.997, "close": c})
        price = c
    df = pd.DataFrame(rows)
    feats = confluence.compute_votes(df, "1h")
    flips = {}
    for ind, mode in (("rf", "intraday"), ("rf_scalp", "scalping"), ("rf_swing", "swing")):
        rf = range_filter(df["close"], slow_mult=RANGE_FILTER_SENSITIVITY[mode])
        _, kind, bull_col, bear_col = confluence.INDICATORS[ind]
        assert kind == "estado"
        assert feats[bull_col].tolist() == (rf["rf_state"] == 1).tolist()
        assert feats[bear_col].tolist() == (rf["rf_state"] == -1).tolist()
        assert confluence.INDICATOR_VARIANTS[ind][0] == "rf"
        long, short = confluence.confluence_signals(feats, [ind], 1)
        flips[ind] = int(long.sum() + short.sum())
    # Scalping (1.3) gira mas seguido que Intraday (3.5), y este mas que Swing (4.2).
    assert flips["rf_scalp"] > flips["rf"] > flips["rf_swing"] > 0


def _random_walk(seed: int, n: int = 1500) -> pd.DataFrame:
    rng = random.Random(seed)
    price, rows = 100.0, []
    for i in range(n):
        c = price * (1 + rng.uniform(-0.02, 0.02))
        rows.append({"open_time": START_MS + i * 3_600_000, "open": price,
                     "high": max(price, c) * 1.003, "low": min(price, c) * 0.997, "close": c})
        price = c
    return pd.DataFrame(rows)


def test_ema_state_indicators_vote_close_against_pine_ema():
    from app.indicators._pine import pine_ema

    df = _random_walk(11)
    feats = confluence.compute_votes(df, "1h")
    for n in confluence.EMA_LENGTHS:
        ind = confluence.EMA_STATE_IDS[n]
        _, kind, bull_col, bear_col = confluence.INDICATORS[ind]
        assert kind == "estado"
        ema = pine_ema(df["close"], n)
        assert feats[bull_col].tolist() == (df["close"] > ema).tolist()
        assert feats[bear_col].tolist() == (df["close"] < ema).tolist()
        # Mientras la EMA no tiene valor (n - 1 velas) no vota ningun lado.
        assert not feats[bull_col].iloc[: n - 1].any() and not feats[bear_col].iloc[: n - 1].any()
        assert ind not in confluence.INDICATOR_VARIANTS


def test_ema_crosses_match_a_manual_crossover_for_all_15_pairs():
    from app.indicators._pine import pine_ema

    df = _random_walk(12)
    feats = confluence.compute_votes(df, "1h")
    assert len(confluence.EMA_CROSS_PAIRS) == 15
    close = df["close"].tolist()
    for fast, slow in confluence.EMA_CROSS_PAIRS:
        assert fast < slow
        ind = f"emax_{fast}_{slow}"
        _, kind, long_col, short_col = confluence.INDICATORS[ind]
        assert kind == "evento"
        assert confluence.INDICATOR_VARIANTS[ind] == ("emax", f"EMA {fast} / EMA {slow}")
        f = pine_ema(pd.Series(close), fast).tolist()
        s = pine_ema(pd.Series(close), slow).tolist()
        up, down = [False], [False]
        for i in range(1, len(close)):
            ok = not any(np.isnan(v) for v in (f[i], s[i], f[i - 1], s[i - 1]))
            up.append(ok and f[i] > s[i] and f[i - 1] <= s[i - 1])
            down.append(ok and f[i] < s[i] and f[i - 1] >= s[i - 1])
        assert feats[long_col].tolist() == up
        assert feats[short_col].tolist() == down
        assert not (feats[long_col] & feats[short_col]).any()
    # En 1500 velas de paseo al azar la 9/21 se cruza varias veces en ambos sentidos.
    assert feats["long_emax_9_21"].sum() > 3 and feats["short_emax_9_21"].sum() > 3


def test_ema_cross_signals_edge_cases():
    nan = float("nan")
    fast = pd.Series([nan, 1.0, 2.0, 2.0, 1.0, 1.0, 3.0])
    slow = pd.Series([nan, 2.0, 2.0, 2.0, 2.0, 1.0, 1.0])
    up, down = confluence.ema_cross_signals(fast, slow)
    # i=1: la vela anterior es NaN -> sin senal; i=2 y 3: iguales no es cruce; i=4: baja desde
    # igualdad -> corto; i=5: igual, nada; i=6: sube desde igualdad -> largo.
    assert up.tolist() == [False, False, False, False, False, False, True]
    assert down.tolist() == [False, False, False, False, True, False, False]


def test_a_single_ema_cross_enters_only_on_each_cross():
    df = _random_walk(13)
    feats = confluence.compute_votes(df, "1h")
    long, short = confluence.confluence_signals(feats, ["emax_9_21"], 5)
    assert long.tolist() == feats["long_emax_9_21"].astype(bool).tolist()
    assert short.tolist() == feats["short_emax_9_21"].astype(bool).tolist()
    # Combinado con un filtro de estado, el cruce vota durante la ventana: entra si el filtro
    # se alinea dentro de las 5 velas siguientes al cruce.
    long2, _ = confluence.confluence_signals(feats, ["emax_9_21", "ema200"], 5)
    assert long2.sum() >= 1
    strategy = confluence.ConfluenceStrategy(["emax_50_200", "ema50"], "1h")
    assert strategy.indicators == ("ema50", "emax_50_200")


def test_indicator_options_group_the_ema_crosses_with_a_label():
    opts = {o["id"]: o for o in confluence.indicator_options()}
    assert set(opts) == set(confluence.INDICATORS)
    assert opts["emax_50_200"]["group"] == "emax"
    assert opts["emax_50_200"]["variant"] == "EMA 50 / EMA 200"
    assert opts["emax_50_200"]["group_label"] == confluence.INDICATOR_GROUP_LABELS["emax"]
    assert opts["rf_swing"]["group_label"].startswith("Range Filter (Frost)")
    assert "group" not in opts["ema200"] and "group" not in opts["ema"]


def test_cloud_state_and_retest_vote_like_frost_cloud():
    from app.indicators.trend_filters import cloud_retest_signals, frost_cloud

    df = _random_walk(21)
    feats = confluence.compute_votes(df, "1h")
    for n in confluence.CLOUD_LENGTHS:
        cloud = frost_cloud(df, n)
        _, kind, bull_col, bear_col = confluence.INDICATORS[f"cloud{n}"]
        assert kind == "estado"
        assert feats[bull_col].tolist() == (df["close"] > cloud["cloud_top"]).tolist()
        assert feats[bear_col].tolist() == (df["close"] < cloud["cloud_bottom"]).tolist()
        # dentro de la nube no vota ningun lado
        inside = (df["close"] <= cloud["cloud_top"]) & (df["close"] >= cloud["cloud_bottom"])
        assert not (feats[bull_col] | feats[bear_col])[inside].any()
        _, kind, long_col, short_col = confluence.INDICATORS[f"cloud_retest{n}"]
        assert kind == "evento"
        long, short = cloud_retest_signals(df, cloud)
        assert feats[long_col].tolist() == long.tolist()
        assert feats[short_col].tolist() == short.tolist()
        assert long.sum() > 0 and short.sum() > 0
        # solo y con ventana 1, el retesteo entra en cada rebote (dos rebotes seguidos del mismo
        # lado son un solo voto continuo: entra en el primero)
        lo, sh = confluence.confluence_signals(feats, [f"cloud_retest{n}"], 1)
        first_long = long & ~np.concatenate(([False], long[:-1]))
        first_short = short & ~np.concatenate(([False], short[:-1]))
        assert lo.tolist() == first_long.tolist() and sh.tolist() == first_short.tolist()
        assert confluence.INDICATOR_VARIANTS[f"cloud{n}"] == ("cloud", f"Longitud {n}")
        assert confluence.INDICATOR_VARIANTS[f"cloud_retest{n}"] == (
            "cloud_retest", f"Longitud {n}")


def test_strategy_validates_its_parameters():
    for kwargs in ({"indicators": []}, {"indicators": ["nope"]}, {"window": 0},
                   {"window": confluence.MAX_WINDOW + 1}, {"exit_name": "x"}, {"interval": "2h"}):
        args = {"indicators": ["ema"], "interval": "1h", **kwargs}
        with pytest.raises(ValueError):
            confluence.ConfluenceStrategy(args.pop("indicators"), args.pop("interval"), **args)
    s = confluence.ConfluenceStrategy(["rs", "sfp", "ema"], "4h", window=3, exit_name="s3_trail3")
    assert s.name == "mix|sfp+ema+rs|w3|s3_trail3"


# --- validacion y API --------------------------------------------------------------------------


def test_validate_request_normalizes_and_rejects_bad_input():
    req = service.validate_request([" btcusdt", "BTCUSDT", "ethusdt"], "15m",
                                   ["ema", "sfp", "fvg", "btc"], 4, "s1_tp2r",
                                   "2024-01-01", "2025-01-01")
    assert req.symbols == ("BTCUSDT", "ETHUSDT")
    assert req.indicators == ("sfp", "fvg", "ema", "btc")
    assert req.start_ms == int(datetime(2024, 1, 1, tzinfo=UTC).timestamp() * 1000)
    every = service.validate_request(["BTCUSDT"], "1w", list(confluence.INDICATORS))
    assert len(every.indicators) == len(confluence.INDICATORS)
    bad = [
        ([], "1h", ["ema"]), (["BTCUSDT"], "2h", ["ema"]), (["BTCUSDT"], "1h", []),
        (["BTCUSDT"], "1h", ["foo"]), (["BTC/USDT"], "1h", ["ema"]),
        ([f"S{i}USDT" for i in range(service.MAX_SYMBOLS + 1)], "1h", ["ema"]),
    ]
    for args in bad:
        with pytest.raises(service.ExplorerRequestError):
            service.validate_request(*args)
    for kwargs in ({"window": 0}, {"exit_name": "x"}, {"start": "2024-13-01"},
                   {"start": "2025-01-01", "end": "2024-01-01"}):
        with pytest.raises(service.ExplorerRequestError):
            service.validate_request(["BTCUSDT"], "1h", ["ema"], **kwargs)


@pytest.mark.asyncio
async def test_options_without_history_file_says_how_to_create_it(tmp_path):
    out = await route.options(settings=_settings(str(tmp_path / "no_existe.db")))
    assert out["available"] is False
    assert "update_history_file" in out["detail"]
    assert {i["id"] for i in out["indicators"]} == set(confluence.INDICATORS)
    assert {i["kind"] for i in out["indicators"]} == {"estado", "evento"}
    assert [t["label"] for t in out["timeframes"]] == ["15m", "1h", "4h", "1D", "1S"]
    assert "reserved_from" not in out


@pytest.mark.asyncio
async def test_options_lists_every_series_of_the_file_without_a_cutoff(history):
    out = await route.options(settings=_settings(history))
    assert out["available"] is True
    assert out["symbols"] == ["BTCUSDT", "ETHUSDT"]
    assert {(s["symbol"], s["timeframe"]) for s in out["series"]} == {
        (s, tf) for s in ("BTCUSDT", "ETHUSDT") for tf in service.TIMEFRAMES}
    assert max(s["last_day"] for s in out["series"]) > "2026-10-01"


@pytest.mark.asyncio
async def test_history_file_is_opened_read_only_and_never_written(history):
    before = os.path.getmtime(history), os.path.getsize(history)
    db = service.ReadOnlyDatabase(history)
    await db.connect()
    try:
        with pytest.raises(Exception, match="readonly|read-only|read only"):
            await db.conn.execute("DELETE FROM ohlcv_cache")
    finally:
        await db.close()
    await _run(history, symbols=["BTCUSDT"], timeframe="4h", indicators=["rf"])
    assert (os.path.getmtime(history), os.path.getsize(history)) == before
    assert not os.path.exists(history + "-wal")


@pytest.mark.asyncio
async def test_run_end_to_end_with_many_indicators_up_to_the_end_of_the_file(history):
    out = await _run(history, symbols=["BTCUSDT", "ETHUSDT", "XRPUSDT"], timeframe="1h",
                     indicators=["rf", "ema"])
    assert out["exploratory"] is True and "EXPLORATORIA" in out["note"]
    assert out["params"]["name"] == "mix|rf+ema|w5|s2_tp2r"
    assert out["params"]["exit_label"] == "SL 2 ATR, TP 2R"
    assert out["period"]["end"] > "2026-10-01"
    res = out["result"]
    assert res["trades"] > 0
    assert 0.0 <= res["winrate"] <= 1.0
    assert res["longs"] + res["shorts"] == res["trades"]
    assert sum(r["trades"] for r in out["by_symbol"]) == res["trades"]
    assert sum(r["trades"] for r in out["by_year"]) == res["trades"]
    assert out["control"]["trades"] > 0
    assert out["missing"] == [
        {"symbol": "XRPUSDT", "reason": "No esta en el archivo de historial en este marco."}]
    every = await _run(history, symbols=["ETHUSDT"], timeframe="1h",
                       indicators=list(confluence.INDICATORS), window=10, exit="s3_trail3")
    assert len(every["params"]["indicators"]) == len(confluence.INDICATORS)
    assert every["result"]["trades"] >= 0


@pytest.mark.asyncio
async def test_run_works_in_15m_with_a_trailing_exit(history):
    out = await _run(history, symbols=["BTCUSDT"], timeframe="15m", indicators=["wt"],
                     exit="s2_trail2")
    assert out["result"]["trades"] > 0
    assert set(out["result"]["close_reasons"]) <= {"SL", "TRAILING", "TP", "END_OF_DATA",
                                                   "LIQUIDATION"}


@pytest.mark.asyncio
async def test_run_respects_requested_dates(history):
    out = await _run(history, symbols=["BTCUSDT"], timeframe="1h", indicators=["sfp"],
                     start="2026-11-01")
    assert out["result"]["trades"] == 0
    assert out["missing"][0]["reason"] == "Sin velas en el periodo pedido."


@pytest.mark.asyncio
async def test_a_short_range_loads_warmup_before_the_start_and_counts_only_later_entries(history):
    # 14 dias de 1h = 336 velas, menos que el calentamiento de 500: antes daba 0 operaciones.
    kw = {"symbols": ["BTCUSDT", "ETHUSDT"], "timeframe": "1h", "indicators": ["wt"],
          "start": "2026-09-20", "end": "2026-10-04"}
    out = await _run(history, **kw)
    assert out["result"]["trades"] > 0
    assert out["period"] == {"start": "2026-09-20", "end": "2026-10-03"}
    db = service.ReadOnlyDatabase(history)
    await db.connect()
    try:
        req = service.validate_request(**{**kw, "indicators": ["wt"]})
        full = await service.run_exploration_on_db(
            db, req, _settings(history), await service.list_available_series(db))
    finally:
        await db.close()
    assert full["result"] == out["result"]
    start_ms = int(datetime(2026, 9, 20, tzinfo=UTC).timestamp() * 1000)
    assert all(int(r["key"]) >= 2026 for r in out["by_year"])
    assert start_ms > START_MS


@pytest.mark.asyncio
async def test_run_errors_map_to_http_codes(tmp_path, history):
    with pytest.raises(HTTPException) as exc:
        await _run(history, symbols=["BTCUSDT"], timeframe="1h", indicators=["nope"])
    assert exc.value.status_code == 400
    with pytest.raises(HTTPException) as exc:
        await route.run(symbols=["BTCUSDT"], timeframe="1h", indicators=["sfp"], window=5,
                        exit="s2_tp2r", settings=_settings(str(tmp_path / "no.db")))
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_a_repeated_run_comes_from_the_cache(history):
    kw = {"symbols": ["ETHUSDT"], "timeframe": "4h", "indicators": ["wt_cross"]}
    first = await _run(history, **kw)
    second = await _run(history, **kw)
    assert first["cached"] is False and second["cached"] is True
    assert {**first, "cached": True} == second


@pytest.mark.asyncio
async def test_btc_and_rs_use_the_btc_series_of_the_same_timeframe(history):
    db = service.ReadOnlyDatabase(history)
    await db.connect()
    try:
        btc = await service.load_btc(db, "1h")
    finally:
        await db.close()
    assert btc is not None and len(btc) > 0
    out = await _run(history, symbols=["ETHUSDT"], timeframe="1h", indicators=["btc", "rs"])
    assert out["params"]["indicators"] == ["btc", "rs"]


def test_summarize_handles_no_trades():
    s = service.summarize([], 100.0)
    assert s["trades"] == 0 and s["winrate"] is None and s["profit_factor"] is None


def test_update_history_file_refuses_the_bot_db_and_data_folder(tmp_path):
    from scripts.update_history_file import EXTRA_TIMEFRAMES, check_target

    assert set(EXTRA_TIMEFRAMES) == {"15m", "1w"}
    bot = tmp_path / "minerva.db"
    assert check_target(str(tmp_path / "history" / "h.db"), str(bot)) == \
        (tmp_path / "history" / "h.db").resolve()
    for bad in (str(bot), str(tmp_path / "data" / "h.db"), str(tmp_path / "h.sqlite")):
        with pytest.raises(SystemExit):
            check_target(bad, str(bot))


def test_min_agree_needs_that_many_votes_and_more_than_the_other_side():
    f = pd.DataFrame({"bull_ema": [0, 1, 1, 1], "bear_ema": [1, 0, 0, 0],
                      "bull_rf": [0, 0, 1, 1], "bear_rf": [1, 1, 0, 0],
                      "bull_wt": [0, 1, 0, 1], "bear_wt": [1, 0, 1, 0]}).astype(bool)
    long, short = confluence.confluence_signals(f, ["ema", "rf", "wt"], 1, min_agree=2)
    assert long.astype(int).tolist() == [0, 1, 0, 0]  # 2 de 3 alcistas desde la vela 1
    assert short.astype(int).tolist() == [1, 0, 0, 0]
    long_all, _ = confluence.confluence_signals(f, ["ema", "rf", "wt"], 1)
    assert long_all.astype(int).tolist() == [0, 0, 0, 1]
    s = confluence.ConfluenceStrategy(["ema", "rf", "wt"], "1h", min_agree=2)
    assert s.name.endswith("|k2")
    assert confluence.ConfluenceStrategy(["ema", "rf"], "1h", min_agree=2).min_agree is None
    with pytest.raises(ValueError):
        confluence.ConfluenceStrategy(["ema"], "1h", min_agree=2)
    with pytest.raises(service.ExplorerRequestError):
        service.validate_request(["BTCUSDT"], "1h", ["ema", "rf"], min_agree=3)
    assert service.validate_request(["BTCUSDT"], "1h", ["ema", "rf"], min_agree=0).min_agree is None


@pytest.mark.asyncio
async def test_all_indicators_with_a_minimum_agreement_still_trade(history):
    out = await _run(history, symbols=["BTCUSDT"], timeframe="1h",
                     indicators=list(confluence.INDICATORS), min_agree=5)
    assert out["params"]["min_agree"] == 5
    assert out["params"]["name"].endswith("|k5")
    assert out["result"]["trades"] > 0


def test_pct_exit_prices_and_validation():
    s = confluence.ConfluenceStrategy(["ema"], "1h", exit_name=confluence.PCT_EXIT,
                                      pct_exit=(2.0, 4.0, None))
    assert s.name == "mix|ema|w5|pct_sl2_tp4"
    df = pd.DataFrame({"open_time": [START_MS + i * 3_600_000 for i in range(600)],
                       "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0})
    feats = s.precompute(df)
    window = feats.iloc[:300]
    assert s.stop_price(window, Signal.LONG, 200.0) == pytest.approx(196.0)
    assert s.stop_price(window, Signal.SHORT, 200.0) == pytest.approx(204.0)
    assert s.take_profit_price(window, Signal.LONG, 200.0) == pytest.approx(208.0)
    assert s.take_profit_price(window, Signal.SHORT, 200.0) == pytest.approx(192.0)
    assert s.trailing_distance(window) is None
    t = confluence.ConfluenceStrategy(["ema"], "1h", exit_name=confluence.PCT_EXIT,
                                      pct_exit=(3.0, None, 1.5))
    t.precompute(df)
    assert t.take_profit_price(window, Signal.LONG, 200.0) is None
    assert t.trailing_distance(window) == pytest.approx(1.5)  # 1.5 % del cierre (100)
    assert t.name.endswith("pct_sl3_tr1.5")
    for bad in ((None, None, None), (0, None, None), (60, None, None), (2, 0, None),
                (2, 600, None), (2, None, 70), (float("nan"), None, None)):
        with pytest.raises(ValueError):
            confluence.check_pct_exit(*bad)
    assert confluence.pct_exit_label(2, None, None) == "SL 2 % (TP de respaldo del motor)"


@pytest.mark.asyncio
async def test_run_with_a_pct_exit(history):
    out = await _run(history, symbols=["BTCUSDT"], timeframe="1h", indicators=["rf"],
                     exit="pct", sl_pct=1.5, tp_pct=3, trail_pct=1)
    assert out["params"]["exit_label"] == "SL 1.5 %, TP 3 %, trailing 1 %"
    assert out["params"]["name"] == "mix|rf|w5|pct_sl1.5_tp3_tr1"
    assert out["result"]["trades"] > 0
    with pytest.raises(HTTPException) as exc:
        await _run(history, symbols=["BTCUSDT"], timeframe="1h", indicators=["rf"], exit="pct")
    assert exc.value.status_code == 400
    opts = await route.options(settings=_settings(history))
    assert "pct" in {x["id"] for x in opts["exits"]}
    assert opts["max_sl_pct_before_skip"] == 5.0


@pytest.mark.asyncio
async def test_weekly_runs_with_weekly_camarilla(history):
    out = await _run(history, symbols=["BTCUSDT"], timeframe="1w",
                     indicators=list(confluence.INDICATORS), min_agree=1)
    assert out["missing"] == []
    rng = random.Random(5)
    price, rows = 100.0, []
    for i in range(300):
        c = price * (1 + rng.uniform(-0.08, 0.08))
        rows.append({"open_time": START_MS + i * 604_800_000, "open": price,
                     "high": max(price, c) * 1.02, "low": min(price, c) * 0.98, "close": c})
        price = c
    feats = confluence.compute_votes(pd.DataFrame(rows), "1w")
    assert feats["open_time"].tolist() == [r["open_time"] for r in rows]
    assert feats["cam_r3"].notna().sum() > 250


@pytest.mark.asyncio
async def test_progress_counts_two_steps_per_symbol_and_ends_idle(history, monkeypatch):
    seen = []
    real = service._set_progress

    def spy(**changes):
        real(**changes)
        seen.append(service.progress())

    monkeypatch.setattr(service, "_set_progress", spy)
    await _run(history, symbols=["BTCUSDT", "XRPUSDT"], timeframe="1h", indicators=["rf"])

    assert seen[0]["running"] is True and seen[0]["total"] == 4 and seen[0]["done"] == 0
    assert any(s["stage"] == "control" and s["symbol"] == "BTCUSDT" for s in seen)
    done_values = [s["done"] for s in seen]
    assert done_values == sorted(done_values)  # nunca retrocede
    final = service.progress()
    assert final["running"] is False and final["done"] == final["total"] == 4
    assert final["eta_s"] is None and final["elapsed_s"] is not None
