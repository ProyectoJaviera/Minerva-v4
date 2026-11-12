"""v4: las posiciones abiertas de las pruebas en vivo con sus valores al ultimo precio."""

from __future__ import annotations

import pytest

from app.live.paper import on_tick, open_position
from app.live.report import PRICE_STALE_SECONDS, live_positions, position_view
from app.live.store import LiveStore
from app.persistence.models import Side
from tests.unit.test_live import T0, cfg, make_runner, make_settings
from tests.unit.test_live_api import make_client
from tests.unit.test_live_api import make_settings as api_settings


def _long(**kw):
    pos = open_position(Side.LONG, 100.0, T0, sl=98.0, tp=104.0, trailing=None,
                        settings=make_settings(), signal_time_ms=T0, decision_price=100.0, **kw)
    assert not hasattr(pos, "reason")
    return pos


def test_a_long_shows_pnl_roe_and_distances_like_an_exchange():
    pos = _long()  # 10 USDT x10 a 100: 1 unidad
    v = position_view(pos, 101.0, taker_fee_pct=0.0006, slippage_bps=0.0)

    assert v["notional"] == pytest.approx(101.0)
    assert v["upnl"] == pytest.approx(1.0) and v["roe_pct"] == pytest.approx(10.0)
    assert v["to_sl_pct"] == pytest.approx(3 / 101 * 100)
    assert v["to_tp_pct"] == pytest.approx(3 / 101 * 100)
    assert v["to_liq_pct"] > v["to_sl_pct"] > 0
    # Neto si cerrara ahora: PnL menos la comision de entrada (0,06) y la de salida (0,0606).
    assert v["net_if_closed"] == pytest.approx(1.0 - 0.06 - 0.0606)
    assert v["size_pct"] == pytest.approx(100.0) and v["realized"] == 0.0


def test_a_short_mirrors_and_a_losing_price_gives_negative_values():
    pos = open_position(Side.SHORT, 100.0, T0, sl=102.0, tp=96.0, trailing=None,
                        settings=make_settings(), signal_time_ms=T0, decision_price=100.0)
    v = position_view(pos, 101.0, taker_fee_pct=0.0, slippage_bps=0.0)

    assert v["upnl"] == pytest.approx(-1.0) and v["roe_pct"] == pytest.approx(-10.0)
    assert v["to_sl_pct"] == pytest.approx(1 / 101 * 100)
    assert v["to_tp_pct"] == pytest.approx(5 / 101 * 100)


def test_after_a_partial_tp_the_view_uses_the_open_part_and_the_next_level():
    s = make_settings()
    pos = _long(take_profits=[(101.0, 0.5), (103.0, 0.5)], breakeven=True)
    assert on_tick(pos, 101.0, T0 + 1000, s, funding_rate=0.0) is None
    v = position_view(pos, 102.0, taker_fee_pct=0.0, slippage_bps=0.0)

    assert v["size_pct"] == pytest.approx(50.0) and v["next_tp"] == 103.0
    assert v["upnl"] == pytest.approx(1.0)  # media unidad x 2
    assert v["roe_pct"] == pytest.approx(20.0)  # sobre el margen de la mitad abierta
    assert v["realized"] == pytest.approx(0.5 - 101.0 * 0.5 * s.taker_fee_pct)
    assert v["to_sl_pct"] == pytest.approx(2 / 102 * 100)  # el SL ya esta en la entrada


def test_without_a_price_the_live_values_are_empty():
    v = position_view(_long(), None, taker_fee_pct=0.0006, slippage_bps=5)
    assert v["upnl"] is None and v["roe_pct"] is None and v["next_tp"] == 104.0


def test_the_store_keeps_the_last_price_per_symbol(tmp_path):
    store = LiveStore(tmp_path / "live.db")
    store.save_prices({"BTCUSDT": (100.0, T0), "ETHUSDT": (2.0, T0)})
    store.save_prices({"BTCUSDT": (101.0, T0 + 5)})
    store.save_prices({})
    assert store.prices() == {"BTCUSDT": (101.0, T0 + 5), "ETHUSDT": (2.0, T0)}


@pytest.mark.asyncio
async def test_the_runner_saves_prices_every_second_and_the_view_reads_them(tmp_path):
    runner, store, _t, clock, ids, _s = make_runner(tmp_path, cfg(symbols=["BTCUSDT"]),
                                                    signals={})
    await runner.housekeeping()
    runner.on_tick("BTCUSDT", clock.ms, 100.0, 1.0, "buy")
    runner.on_tick("BTCUSDT", clock.ms + 10, 100.5, 1.0, "buy")
    assert store.prices() == {}  # se guarda en la vuelta de un segundo
    clock.ms += 1000
    await runner.housekeeping()
    assert store.prices() == {"BTCUSDT": (100.5, clock.ms - 990)}

    store.save_position(ids[0], "BTCUSDT", _long())
    out = live_positions(store.path, 0.0, 0.0, now_s=clock.ms / 1000)
    (row,) = out["positions"]
    assert row["test_id"] == ids[0] and row["is_control"] is False
    assert row["price"] == 100.5 and row["price_stale"] is False
    assert row["upnl"] == pytest.approx(0.5)
    later = live_positions(store.path, 0.0, 0.0,
                           now_s=clock.ms / 1000 + PRICE_STALE_SECONDS + 5)
    assert later["positions"][0]["price_stale"] is True


def test_the_positions_route_requires_login_and_lists_open_positions(tmp_path):
    settings = api_settings(tmp_path)
    assert make_client(settings, authenticated=False).get(
        "/api/live/positions").status_code == 401
    client = make_client(settings)
    assert client.get("/api/live/positions").json()["positions"] == []
    with LiveStore(settings.live_tests_db) as store:
        test_id = store.add_test(cfg(), now_ms=T0)
        store.save_position(test_id, "BTCUSDT", _long())
        store.save_prices({"BTCUSDT": (99.0, T0)})
    (row,) = client.get("/api/live/positions").json()["positions"]
    assert row["symbol"] == "BTCUSDT" and row["price"] == 99.0 and row["upnl"] == pytest.approx(-1)
    assert row["price_stale"] is True  # el precio es de 2026-01-05
