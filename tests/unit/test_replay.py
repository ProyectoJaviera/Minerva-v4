"""Backtest en tiempo real (repeticion minuto a minuto): velas, ordenes, salidas y casos limite."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.config import Settings
from app.persistence.models import Side
from app.replay.candles import aggregate, bucket_start
from app.replay.engine import (
    EntryKind,
    EntryOrder,
    MarketView,
    ReplayStrategy,
    run_replay,
)
from app.replay.minute_store import MINUTE_MS, MinuteBar, MinuteStore, bar_from_raw

T0 = int(datetime(2026, 1, 5, tzinfo=UTC).timestamp() * 1000)  # lunes 00:00 UTC
HOUR = 60 * MINUTE_MS


def make_settings(**overrides) -> Settings:
    defaults = dict(
        DEFAULT_MARGIN_USDT=10.0, LEVERAGE=10, TAKER_FEE_PCT=0.0006, MAKER_FEE_PCT=0.0002,
        MAX_SL_MARGIN_LOSS_PCT=50.0, BACKTEST_SLIPPAGE_BPS=0.0,
        BACKTEST_FALLBACK_SL_PCT=0.05, BACKTEST_FALLBACK_TP_PCT=0.10,
    )
    defaults.update(overrides)
    return Settings(_env_file=None, **defaults)  # type: ignore[call-arg]


def flat(start_ms: int, n: int, price: float = 100.0) -> list[MinuteBar]:
    return [MinuteBar(start_ms + i * MINUTE_MS, price, price, price, price, 1.0) for i in range(n)]


def bar(t: int, o: float, h: float, lo: float, c: float) -> MinuteBar:
    return MinuteBar(t, o, h, lo, c, 1.0)


def replace(minutes: list[MinuteBar], *bars: MinuteBar) -> list[MinuteBar]:
    by_t = {m.open_time: m for m in minutes}
    for b in bars:
        by_t[b.open_time] = b
    return [by_t[t] for t in sorted(by_t)]


class OneShot(ReplayStrategy):
    """Pide una orden al primer cierre de vela y despues nada."""

    def __init__(self, order: EntryOrder, sl: float | None = None, tp: float | None = None,
                 trailing: float | None = None, timeframe: str = "1h") -> None:
        self.order, self.sl, self.tp, self.trailing = order, sl, tp, trailing
        self.timeframe = timeframe
        self.done = False
        self.views: list[MarketView] = []

    def on_bar_close(self, view):
        self.views.append(view)
        if self.done:
            return None
        self.done = True
        return self.order

    def stop_price(self, view, side, entry_price):
        return self.sl

    def take_profit_price(self, view, side, entry_price):
        return self.tp

    def trailing_distance(self, view):
        return self.trailing


# --- velas -----------------------------------------------------------------------------------


def test_buckets_align_to_utc_and_weeks_start_on_monday():
    wednesday_noon = T0 + 2 * 24 * HOUR + 12 * HOUR + 7 * MINUTE_MS
    assert bucket_start(wednesday_noon, "1h") == T0 + 2 * 24 * HOUR + 12 * HOUR
    assert bucket_start(wednesday_noon, "4h") == T0 + 2 * 24 * HOUR + 12 * HOUR
    assert bucket_start(wednesday_noon, "1d") == T0 + 2 * 24 * HOUR
    assert bucket_start(wednesday_noon, "1w") == T0
    with pytest.raises(ValueError):
        bucket_start(T0, "3h")


def test_aggregate_builds_ohlcv_and_counts_missing_minutes():
    minutes = [bar(T0, 10, 12, 9, 11), bar(T0 + MINUTE_MS, 11, 15, 10, 14)]
    minutes += flat(T0 + HOUR, 60, 20)
    candles = aggregate(minutes, "1h")

    assert len(candles) == 2
    first = candles[0]
    assert (first.open, first.high, first.low, first.close) == (10, 15, 9, 14)
    assert first.volume == 2.0 and first.minutes == 2 and first.missing == 58
    assert candles[1].missing == 0


# --- entradas --------------------------------------------------------------------------------


def test_a_market_order_at_bar_close_fills_at_the_next_minute_open():
    minutes = flat(T0, 60) + [bar(T0 + HOUR, 101, 101, 101, 101)]
    minutes += flat(T0 + HOUR + MINUTE_MS, 200, 101)
    strat = OneShot(EntryOrder(Side.LONG), sl=97, tp=200)

    res = run_replay(minutes, strat, "s", "BTCUSDT", make_settings())

    t = res.trades[0]
    assert t.entry_price == 101 and t.entry_time.timestamp() * 1000 == T0 + HOUR
    assert strat.views[0].price == 100  # al decidir solo conocia el minuto 59
    assert t.close_reason == "END_OF_DATA"


def test_a_stop_entry_fills_at_its_level_mid_candle_and_at_the_open_after_a_gap():
    minutes = flat(T0, 60) + flat(T0 + HOUR, 120)
    touch = replace(minutes, bar(T0 + HOUR + 10 * MINUTE_MS, 100, 103, 100, 100))
    gap = replace(minutes, bar(T0 + HOUR + 10 * MINUTE_MS, 104, 104, 104, 104))

    r1 = run_replay(touch, OneShot(EntryOrder(Side.LONG, EntryKind.STOP, 102), sl=99, tp=150),
                    "s", "X", make_settings())
    r2 = run_replay(gap, OneShot(EntryOrder(Side.LONG, EntryKind.STOP, 102), sl=99, tp=150),
                    "s", "X", make_settings())

    assert r1.trades[0].entry_price == 102
    assert r1.trades[0].entry_time.timestamp() * 1000 == T0 + HOUR + 10 * MINUTE_MS
    assert r2.trades[0].entry_price == 104  # nunca mejor que el precio que se transo


def test_a_limit_entry_needs_to_cross_its_level_and_pays_maker():
    minutes = flat(T0, 60) + flat(T0 + HOUR, 120)
    touch = replace(minutes, bar(T0 + HOUR + 5 * MINUTE_MS, 100, 100, 98, 100))
    cross = replace(minutes, bar(T0 + HOUR + 5 * MINUTE_MS, 100, 100, 97.5, 100))
    order = EntryOrder(Side.LONG, EntryKind.LIMIT, 98)

    assert run_replay(touch, OneShot(order, sl=95, tp=150), "s", "X", make_settings()).trades == []
    t = run_replay(cross, OneShot(order, sl=95, tp=150), "s", "X", make_settings()).trades[0]
    assert t.entry_price == 98
    assert t.fee_entry_usdt == pytest.approx(10 * 10 * 0.0002)
    assert t.slippage_cost_usdt == 0


def test_an_order_without_expiry_dies_at_the_next_bar_close():
    minutes = flat(T0, 60) + flat(T0 + HOUR, 60) + [bar(T0 + 2 * HOUR, 100, 110, 100, 100)]
    minutes += flat(T0 + 2 * HOUR + MINUTE_MS, 30)
    res = run_replay(minutes, OneShot(EntryOrder(Side.LONG, EntryKind.STOP, 105), sl=95),
                     "s", "X", make_settings())

    assert res.trades == [] and res.stats.orders_expired == 1


def test_entries_are_skipped_by_the_risk_cap_and_when_the_sl_is_already_crossed():
    minutes = flat(T0, 60) + flat(T0 + HOUR, 60)
    capped = run_replay(minutes, OneShot(EntryOrder(Side.LONG), sl=90), "s", "X",
                        make_settings(MAX_SL_MARGIN_LOSS_PCT=50.0))  # 10 % a 10x = 100 %
    crossed = run_replay(minutes, OneShot(EntryOrder(Side.LONG), sl=101), "s", "X",
                         make_settings())

    assert capped.trades == []
    assert capped.skipped[0].intended_sl_margin_loss_pct == pytest.approx(100)
    assert crossed.trades == [] and crossed.stats.entries_skipped_crossed_sl == 1


# --- salidas ---------------------------------------------------------------------------------


def test_sl_and_tp_in_the_same_minute_assume_the_sl_first():
    minutes = flat(T0, 60) + flat(T0 + HOUR, 60)
    minutes = replace(minutes, bar(T0 + HOUR + 3 * MINUTE_MS, 100, 104, 97, 100))
    res = run_replay(minutes, OneShot(EntryOrder(Side.LONG), sl=98, tp=103), "s", "X",
                     make_settings())

    t = res.trades[0]
    assert (t.close_reason, t.exit_price) == ("SL", 98)
    assert res.stats.same_minute_sl_and_tp == 1


def test_a_gap_through_the_sl_exits_at_the_open_and_a_short_takes_its_tp():
    minutes = flat(T0, 60) + flat(T0 + HOUR, 60)
    gap = replace(minutes, bar(T0 + HOUR + 3 * MINUTE_MS, 95, 96, 94, 95))
    short_tp = replace(minutes, bar(T0 + HOUR + 3 * MINUTE_MS, 100, 100, 96, 97))

    t1 = run_replay(gap, OneShot(EntryOrder(Side.LONG), sl=98, tp=110), "s", "X",
                    make_settings()).trades[0]
    t2 = run_replay(short_tp, OneShot(EntryOrder(Side.SHORT), sl=102, tp=97), "s", "X",
                    make_settings()).trades[0]

    assert (t1.close_reason, t1.exit_price) == ("SL", 95)
    assert (t2.close_reason, t2.exit_price) == ("TP", 97) and t2.pnl_gross_usdt > 0


def test_a_mid_minute_entry_only_checks_the_adverse_side_in_its_own_minute():
    minutes = flat(T0, 60) + flat(T0 + HOUR, 60)
    # El minuto que dispara el stop en 102 tambien toca 110 (el TP): no se sabe si fue antes.
    minutes = replace(minutes, bar(T0 + HOUR + 4 * MINUTE_MS, 100, 110, 100, 101))
    res = run_replay(minutes, OneShot(EntryOrder(Side.LONG, EntryKind.STOP, 102), sl=99, tp=110),
                     "s", "X", make_settings())

    assert res.trades[0].close_reason == "END_OF_DATA"


def test_the_trailing_moves_with_the_minute_extreme_and_fires_from_the_next_minute():
    minutes = flat(T0, 60) + flat(T0 + HOUR, 60)
    minutes = replace(
        minutes,
        bar(T0 + HOUR + 2 * MINUTE_MS, 100, 106, 100, 105),  # el mejor precio pasa a 106
        bar(T0 + HOUR + 3 * MINUTE_MS, 105, 105, 102, 103),  # toca 103 = 106 - 3
    )
    res = run_replay(minutes, OneShot(EntryOrder(Side.LONG), sl=97, trailing=3.0), "s", "X",
                     make_settings())

    t = res.trades[0]
    assert (t.close_reason, t.exit_price) == ("TRAILING", 103)
    assert t.exit_time.timestamp() * 1000 == T0 + HOUR + 3 * MINUTE_MS


def test_funding_is_prorated_by_minute():
    minutes = flat(T0, 60) + flat(T0 + HOUR, 8 * 60)
    res = run_replay(minutes, OneShot(EntryOrder(Side.LONG), sl=97, tp=200), "s", "X",
                     make_settings(), funding_events=[(T0 - HOUR, 0.0001)])

    t = res.trades[0]
    assert t.funding_paid_usdt == pytest.approx(100 * 0.0001, rel=1e-9)
    assert not t.funding_is_approximated


def test_max_hold_closes_at_the_minute_close():
    class Hold(OneShot):
        max_hold_ms = 30 * MINUTE_MS

    minutes = flat(T0, 60) + flat(T0 + HOUR, 60, 101)
    t = run_replay(minutes, Hold(EntryOrder(Side.LONG), sl=98), "s", "X",
                   make_settings()).trades[0]

    assert t.close_reason == "TIME" and t.exit_price == 101
    assert t.exit_time.timestamp() * 1000 == T0 + HOUR + 29 * MINUTE_MS


# --- decisiones dentro de la vela ------------------------------------------------------------


def test_on_minute_sees_the_forming_candle_but_never_the_future():
    class Breakout(ReplayStrategy):
        timeframe = "1h"
        uses_minutes = True

        def __init__(self):
            self.seen: list[tuple[int, float, float]] = []

        def on_minute(self, view):
            assert view.forming is not None
            self.seen.append((view.now_ms, view.price, view.forming.high))
            if view.price > float(view.closed["high"].iloc[-1]):
                return EntryOrder(Side.LONG)
            return None

        def stop_price(self, view, side, entry_price):
            return entry_price * 0.99

    minutes = flat(T0, 60) + flat(T0 + HOUR, 60)
    minutes = replace(minutes, bar(T0 + HOUR + 20 * MINUTE_MS, 100, 102, 100, 101.5),
                      bar(T0 + HOUR + 21 * MINUTE_MS, 101.7, 102, 101, 101.8))
    strat = Breakout()
    res = run_replay(minutes, strat, "s", "X", make_settings())

    t = res.trades[0]
    # Decidio al terminar el minuto 20 (precio 101.5) y entro a la apertura del 21, no antes.
    assert t.entry_time.timestamp() * 1000 == T0 + HOUR + 21 * MINUTE_MS
    assert t.entry_price == 101.7
    decision = [s for s in strat.seen if s[0] == T0 + HOUR + 21 * MINUTE_MS][0]
    assert decision[1] == 101.5 and decision[2] == 102


def test_with_forming_appends_the_open_candle():
    class Peek(ReplayStrategy):
        timeframe = "1h"
        uses_minutes = True
        frame = None

        def on_minute(self, view):
            if Peek.frame is None and view.forming and view.forming.minutes == 3:
                Peek.frame = view.with_forming()
            return None

    run_replay(flat(T0, 60) + flat(T0 + HOUR, 10, 105), Peek(), "s", "X", make_settings())

    assert list(Peek.frame["close"]) == [100.0, 105.0]
    assert Peek.frame["open_time"].iloc[-1] == T0 + HOUR


def test_unsorted_minutes_are_rejected_and_empty_input_is_empty():
    with pytest.raises(ValueError):
        run_replay(list(reversed(flat(T0, 3))), ReplayStrategy(), "s", "X", make_settings())
    assert run_replay([], ReplayStrategy(), "s", "X", make_settings()).trades == []


# --- archivo de minutos ----------------------------------------------------------------------


def test_minute_store_roundtrip_bounds_and_missing(tmp_path):
    rows = [bar_from_raw({"time": T0 + i * MINUTE_MS, "open": "1", "high": "2", "low": "0.5",
                          "close": "1.5", "baseVol": "3", "quoteVol": "4"}) for i in (0, 1, 3)]
    with MinuteStore(tmp_path / "m.db") as store:
        assert store.upsert("BTCUSDT", rows) == 3
        assert store.upsert("BTCUSDT", rows[:1]) == 1  # reemplaza, no duplica
        assert store.bounds("BTCUSDT") == (T0, T0 + 3 * MINUTE_MS)
        assert store.count("BTCUSDT", T0, T0 + 4 * MINUTE_MS) == 3
        assert store.missing_minutes("BTCUSDT", T0, T0 + 4 * MINUTE_MS) == [T0 + 2 * MINUTE_MS]
        loaded = store.load("BTCUSDT", T0, T0 + HOUR)
        store.set_floor("BTCUSDT", T0)
        assert store.floor("BTCUSDT") == T0 and store.floor("ETHUSDT") is None
    assert [m.open_time for m in loaded] == [T0, T0 + MINUTE_MS, T0 + 3 * MINUTE_MS]
    assert loaded[0].volume == 4.0
    with MinuteStore(tmp_path / "m.db", read_only=True) as ro:
        assert ro.bounds("ETHUSDT") is None


@pytest.mark.parametrize("raw", [
    {"time": T0 + 1, "open": 1, "high": 1, "low": 1, "close": 1},
    {"time": T0, "open": 1, "high": 0.5, "low": 1, "close": 1},
    {"time": T0, "open": 0, "high": 1, "low": 0, "close": 1},
])
def test_bar_from_raw_rejects_incoherent_bars(raw):
    with pytest.raises(ValueError):
        bar_from_raw(raw)


def test_bar_from_raw_widens_the_range_when_the_open_is_the_previous_close():
    row = bar_from_raw({"time": T0, "open": 3, "high": 2, "low": 1, "close": 0.9})

    assert row[1:5] == (3.0, 3.0, 0.9, 0.9)
