"""Pruebas en vivo: configuracion, posicion paper por tick, velas con ticks, archivo y corredor."""

from __future__ import annotations

import math
import time

import numpy as np
import pytest

from app.config import Settings
from app.live.book import CandleBook
from app.live.config import TestConfig, validate
from app.live.paper import OpenRefusal, PaperPosition, close_position, on_tick, open_position
from app.live.report import summarize
from app.live.runner import LiveRunner, parse_trade_message
from app.live.store import LiveStore, TickStore
from app.persistence.models import Side
from app.strategies.base import Signal

T0 = 1_767_571_200_000  # 2026-01-05 00:00 UTC (lunes)
M15 = 15 * 60_000


def make_settings(**overrides) -> Settings:
    defaults = dict(
        DEFAULT_MARGIN_USDT=10.0, LEVERAGE=10, TAKER_FEE_PCT=0.0006, MAKER_FEE_PCT=0.0002,
        MAX_SL_MARGIN_LOSS_PCT=50.0, BACKTEST_SLIPPAGE_BPS=0.0,
        BACKTEST_FALLBACK_SL_PCT=0.05, BACKTEST_FALLBACK_TP_PCT=0.10,
    )
    defaults.update(overrides)
    return Settings(_env_file=None, **defaults)  # type: ignore[call-arg]


def cfg(**kw) -> TestConfig:
    base = dict(name="p", symbols=["btcusdt", "ETHUSDT", "BTCUSDT"], timeframe="15m",
                indicators=["ema", "rf"])
    base.update(kw)
    return validate(**base)


# --- configuracion ---------------------------------------------------------------------------


def test_validate_normalizes_and_roundtrips_through_json():
    c = cfg(mode="intrabar", exit_name="pct", pct_exit=(1.0, 2.0, None), min_agree=1)

    assert c.symbols == ("BTCUSDT", "ETHUSDT")
    assert c.indicators == ("rf", "ema")  # en el orden del catalogo
    assert TestConfig.from_json(c.to_json()) == c


@pytest.mark.parametrize("kw", [
    {"name": ""}, {"symbols": []}, {"symbols": ["BTC"]}, {"timeframe": "3m"},
    {"indicators": []}, {"indicators": ["nope"]}, {"mode": "tick"}, {"exit_name": "x"},
    {"exit_name": "pct"}, {"window": 0}, {"min_agree": 3}, {"eval_seconds": 1},
])
def test_validate_rejects_bad_choices(kw):
    with pytest.raises(ValueError):
        cfg(**kw)


# --- posicion paper --------------------------------------------------------------------------


def open_long(price=100.0, sl=98.0, tp=104.0, trailing=None, **kw) -> PaperPosition:
    pos = open_position(Side.LONG, price, T0, sl=sl, tp=tp, trailing=trailing,
                        settings=make_settings(**kw), signal_time_ms=T0 - 500,
                        decision_price=price)
    assert isinstance(pos, PaperPosition)
    return pos


def test_a_stop_fills_at_the_tick_price_and_a_tp_at_its_level():
    s = make_settings()
    sl_trade = on_tick(open_long(), 97.5, T0 + 1000, s)  # el mercado salto por debajo del SL
    tp_trade = on_tick(open_long(), 105.0, T0 + 1000, s)

    assert (sl_trade.close_reason, sl_trade.exit_price) == ("SL", 97.5)
    assert (tp_trade.close_reason, tp_trade.exit_price) == ("TP", 104.0)
    assert sl_trade.pnl_net_usdt < 0 < tp_trade.pnl_net_usdt
    # 10 USDT a 10x = 100 de nocional: +4 % menos comisiones de entrada y salida.
    assert tp_trade.pnl_net_usdt == pytest.approx(4.0 - 100 * 0.0006 - 104 * 0.0006)


def test_a_short_mirrors_the_long():
    s = make_settings()
    pos = open_position(Side.SHORT, 100.0, T0, sl=102.0, tp=96.0, trailing=None, settings=s,
                        signal_time_ms=T0, decision_price=100.0)
    assert on_tick(pos, 101.9, T0 + 1, s) is None
    trade = on_tick(pos, 95.0, T0 + 2, s)

    assert (trade.close_reason, trade.exit_price) == ("TP", 96.0) and trade.pnl_gross_usdt > 0


def test_the_trailing_follows_each_tick_and_fires_on_a_later_one():
    s = make_settings()
    pos = open_long(sl=97.0, tp=None, trailing=3.0)

    assert on_tick(pos, 106.0, T0 + 1, s) is None and pos.stop == 103.0
    assert on_tick(pos, 104.0, T0 + 2, s) is None and pos.stop == 103.0
    trade = on_tick(pos, 102.9, T0 + 3, s)
    assert (trade.close_reason, trade.exit_price) == ("TRAILING", 102.9)


def test_entries_are_refused_by_the_risk_cap_and_a_crossed_sl_and_fall_back_without_sl():
    s = make_settings()
    capped = open_position(Side.LONG, 100.0, T0, sl=90.0, tp=None, trailing=None, settings=s,
                           signal_time_ms=T0, decision_price=100.0)
    crossed = open_position(Side.LONG, 100.0, T0, sl=100.5, tp=None, trailing=None, settings=s,
                            signal_time_ms=T0, decision_price=100.0)
    fallback = open_position(Side.LONG, 100.0, T0, sl=None, tp=None, trailing=None, settings=s,
                             signal_time_ms=T0, decision_price=100.0)

    assert isinstance(capped, OpenRefusal) and capped.reason == "RISK_CAP"
    assert isinstance(crossed, OpenRefusal) and crossed.reason == "SL_CROSSED"
    assert (fallback.stop, fallback.tp) == (95.0, pytest.approx(110.0))


def test_funding_accrues_with_time_and_is_flagged_when_unknown():
    s = make_settings()
    known, unknown = open_long(tp=200.0), open_long(tp=200.0)
    on_tick(known, 100.0, T0 + 8 * 3_600_000, s, funding_rate=0.0001)
    on_tick(unknown, 100.0, T0 + 8 * 3_600_000, s)

    assert known.funding_paid_usdt == pytest.approx(100 * 0.0001) and not known.funding_approx
    assert unknown.funding_paid_usdt == 0 and unknown.funding_approx


def test_a_position_survives_a_json_roundtrip():
    pos = open_long(trailing=2.0)

    assert PaperPosition.from_dict(pos.to_dict()) == pos
    assert close_position(pos, 101.0, T0 + 5, "MANUAL", make_settings()).close_reason == "MANUAL"


# --- velas con ticks -------------------------------------------------------------------------


def raw_bars(n: int, end_ms: int, step: int = M15, price: float = 100.0) -> list[dict]:
    return [{"time": end_ms - (n - i) * step, "open": price, "high": price + 1, "low": price - 1,
             "close": price, "quoteVol": 5} for i in range(n)]


def test_the_book_drops_the_open_bar_from_rest_and_builds_it_from_ticks():
    book = CandleBook("BTCUSDT", "15m")
    now = T0 + 5 * 60_000
    raw = raw_bars(3, T0) + [{"time": T0, "open": 100, "high": 150, "low": 50, "close": 120}]

    assert book.load(raw, now) == 3  # la de T0 todavia no cerro
    assert book.on_tick(101.0, 2.0, now) is False
    book.on_tick(99.0, 1.0, now + 1000)
    f = book.forming
    assert (f["open_time"], f["open"], f["high"], f["low"], f["close"]) == (T0, 100, 101, 99, 99)
    assert f["volume"] == pytest.approx(101 * 2 + 99)

    assert book.on_tick(102.0, 1.0, T0 + M15 + 10) is True  # primer tick de la vela siguiente
    assert book.closed[-1]["open_time"] == T0 and book.forming["open"] == 99.0
    frame = book.frame(include_forming=True)
    assert list(frame["open_time"])[-2:] == [T0, T0 + M15] and len(frame) == 5
    assert len(book.frame(include_forming=False)) == 4


def test_the_book_closes_by_clock_ignores_late_ticks_and_rest_wins_on_reconcile():
    book = CandleBook("BTCUSDT", "15m")
    book.load(raw_bars(2, T0), T0)
    book.on_tick(100.5, 1.0, T0 + 1000)

    assert book.roll(T0 + M15 - 1) is False and book.roll(T0 + M15) is True
    assert book.forming is None and book.closed[-1]["close"] == 100.5
    assert book.on_tick(90.0, 1.0, T0 + 2000) is False and book.forming is None  # atrasado

    book.load(raw_bars(3, T0 + M15, price=101.0), T0 + M15 + 5000)
    assert book.closed[-1] == {"open_time": T0, "open": 101.0, "high": 102.0, "low": 100.0,
                               "close": 101.0, "volume": 5.0}


# --- archivo ---------------------------------------------------------------------------------


def test_the_store_keeps_tests_positions_trades_and_events(tmp_path):
    s = make_settings()
    with LiveStore(tmp_path / "live.db") as store:
        a = store.add_test(cfg(name="a"), now_ms=T0)
        b = store.add_test(cfg(name="b"), now_ms=T0 + 1)
        assert store.stop_test(b, now_ms=T0 + 2) and not store.stop_test(b)
        assert [t.id for t in store.tests(only_active=True)] == [a]
        assert [(t.config.name, t.status) for t in store.tests()] == [("a", "active"),
                                                                      ("b", "stopped")]
        pos = open_long()
        store.save_position(a, "BTCUSDT", pos)
        assert store.positions() == {(a, "BTCUSDT"): pos}
        trade = on_tick(pos, 105.0, T0 + 9, s)
        store.record_trade(a, "BTCUSDT", trade)
        assert store.positions() == {} and store.trades(a) == [("BTCUSDT", trade)]
        store.log("SIGNAL", "x", test_id=a, symbol="BTCUSDT", now_ms=T0)
        assert store.events(a)[0][3:] == ("SIGNAL", "x") and store.events()[0][1] == a
        assert store.heartbeat() is None
        store.beat("ok", now_ms=T0)
        assert store.heartbeat() == (T0, "ok")
    with LiveStore(tmp_path / "live.db", read_only=True) as ro:
        assert len(ro.tests()) == 2
    ticks = TickStore(tmp_path / "ticks.db")
    assert ticks.append([("BTCUSDT", T0, 1.0, 2.0, "buy"), ("ETHUSDT", T0, 3.0, 1.0, "sell")]) == 2
    assert ticks.append([]) == 0 and ticks.count() == 2 and ticks.count("BTCUSDT") == 1
    ticks.close()


def test_summarize_counts_wins_pf_and_net():
    s = make_settings()
    win = on_tick(open_long(), 105.0, T0 + 1, s)
    loss = on_tick(open_long(), 97.0, T0 + 1, s)

    out = summarize([win, loss, loss])
    assert out["trades"] == 3 and out["wins"] == 1
    assert out["pf"] == pytest.approx(win.pnl_net_usdt / (-2 * loss.pnl_net_usdt))
    assert summarize([])["pf"] is None and math.isinf(summarize([win])["pf"])


# --- mensajes del WebSocket ------------------------------------------------------------------


def test_parse_trade_message_reads_real_shaped_messages_and_ignores_the_rest():
    msg = {"ch": "trade", "symbol": "BTCUSDT", "ts": 1794427836935,
           "data": [{"t": "2026-11-11T20:10:36Z", "p": "74430.6", "v": "0.0272", "s": "buy"},
                    {"t": "x", "p": "bad", "v": "1", "s": "sell"}]}

    assert parse_trade_message(msg) == [("BTCUSDT", 1794427836935, 74430.6, 0.0272, "buy")]
    assert parse_trade_message({"op": "ping", "pong": 1}) == []
    assert parse_trade_message({"ch": "trade", "symbol": "X", "data": []}) == []
    assert parse_trade_message({"ch": "trade", "symbol": "X", "ts": "?", "data": [{}]}) == []


# --- corredor --------------------------------------------------------------------------------


class FakeRest:
    def __init__(self, end_ms: int) -> None:
        self.end_ms = end_ms
        self.kline_calls = 0

    async def get_kline(self, symbol, interval, start_time=None, end_time=None, limit=100,
                        price_type="LAST_PRICE"):
        self.kline_calls += 1
        top = self.end_ms if end_time is None else min(end_time, self.end_ms)
        rng = np.random.default_rng(7)
        bars = []
        for i in range(limit):
            t = top - (limit - i) * M15
            p = 100 + float(rng.normal(0, 0.2))
            bars.append({"time": t, "open": p, "high": p + 0.5, "low": p - 0.5, "close": p,
                         "quoteVol": 10})
        return bars

    async def get_funding_rate_batch(self):
        return [{"symbol": "BTCUSDT", "fundingRate": "0.0001"}, {"symbol": "ETHUSDT"}]

    async def get_funding_rate_history(self, symbol, start_time=None, end_time=None, limit=100):
        return [{"fundingTime": self.end_ms - 8 * 3_600_000, "fundingRate": "0.0001"}]


class Clock:
    def __init__(self, ms: int) -> None:
        self.ms = ms

    def __call__(self) -> float:
        return self.ms / 1000


class StubStrategy:
    """Devuelve SL y TP fijos alrededor de la entrada (reemplaza a la confluencia)."""

    def stop_price(self, frame, side, entry):
        return entry * (0.99 if side == Signal.LONG else 1.01)

    def take_profit_price(self, frame, side, entry):
        return entry * (1.02 if side == Signal.LONG else 0.98)

    def trailing_distance(self, frame):
        return None


def make_runner(tmp_path, *configs: TestConfig, signals: dict | None = None):
    clock = Clock(T0 + 60_000)
    store = LiveStore(tmp_path / "live.db")
    ids = [store.add_test(c, now_ms=clock.ms) for c in configs]
    ticks = TickStore(tmp_path / "ticks.db")
    subscribed: list[list[str]] = []

    async def on_symbols(symbols):
        subscribed.append(list(symbols))

    runner = LiveRunner(make_settings(), store, ticks, FakeRest(T0), clock=clock,
                        on_symbols=on_symbols, inline_eval=signals is not None)
    if signals is not None:
        def fake_evaluate(cfg_, symbol, include_forming):
            sig = signals.get((cfg_.name, symbol, include_forming), Signal.HOLD)
            return sig, StubStrategy(), None
        runner.evaluate = fake_evaluate  # type: ignore[method-assign]
    return runner, store, ticks, clock, ids, subscribed


@pytest.mark.asyncio
async def test_the_runner_loads_books_subscribes_and_records_ticks(tmp_path):
    runner, store, ticks, clock, ids, subscribed = make_runner(
        tmp_path, cfg(symbols=["ETHUSDT"], indicators=["rf", "btc"]))
    await runner.housekeeping()

    assert subscribed[-1] == ["BTCUSDT", "ETHUSDT"]  # BTC entra por el filtro `btc`
    assert set(runner.books) == {("ETHUSDT", "15m"), ("BTCUSDT", "15m")}
    assert len(runner.books[("ETHUSDT", "15m")].closed) == 800
    assert runner.funding == {"BTCUSDT": 0.0001}
    await runner.on_message({"ch": "trade", "symbol": "ETHUSDT", "ts": clock.ms,
                             "data": [{"p": "100", "v": "1", "s": "buy"}]})
    clock.ms += 1000
    await runner.housekeeping()
    assert ticks.count("ETHUSDT") == 1 and store.heartbeat() is not None
    # La confluencia real corre sin errores sobre las velas cargadas.
    result = runner.evaluate(runner.tests[ids[0]].config, "ETHUSDT", include_forming=True)
    assert result is not None and result[0] in (Signal.LONG, Signal.SHORT, Signal.HOLD)


@pytest.mark.asyncio
async def test_intrabar_decides_with_the_open_candle_and_fills_on_the_next_tick(tmp_path):
    signals = {("p", "BTCUSDT", True): Signal.LONG}
    runner, store, _t, clock, ids, _s = make_runner(
        tmp_path, cfg(symbols=["BTCUSDT"], mode="intrabar", eval_seconds=5), signals=signals)
    await runner.housekeeping()
    runner.on_tick("BTCUSDT", clock.ms, 100.0, 1.0, "buy")
    clock.ms += 6000
    await runner.housekeeping()  # decide con el precio 100

    key = (ids[0], "BTCUSDT")
    assert key in runner.pending and key not in runner.positions
    runner.on_tick("BTCUSDT", clock.ms - 500, 100.1, 1.0, "buy")  # tick anterior: no llena
    assert key in runner.pending
    runner.on_tick("BTCUSDT", clock.ms + 200, 100.3, 1.0, "buy")
    pos = runner.positions[key]
    assert pos.entry_price == 100.3 and pos.decision_price == 100.0
    assert store.positions()[key].entry_price == 100.3  # ya esta en el archivo

    # Con la posicion abierta no vuelve a decidir; el TP (+2 %) la cierra y queda registrada.
    clock.ms += 6000
    await runner.housekeeping()
    assert key not in runner.pending
    runner.on_tick("BTCUSDT", clock.ms, 103.0, 1.0, "buy")
    (symbol, trade), = store.trades(ids[0])
    assert symbol == "BTCUSDT" and trade.close_reason == "TP"
    assert trade.exit_price == pytest.approx(100.3 * 1.02)
    # Como mucho una entrada por vela: la senal sigue pero no reabre en la misma vela.
    clock.ms += 6000
    await runner.housekeeping()
    assert key not in runner.pending and key not in runner.positions


@pytest.mark.asyncio
async def test_close_mode_decides_only_when_the_candle_closes(tmp_path):
    signals = {("p", "BTCUSDT", False): Signal.SHORT}
    runner, store, _t, clock, ids, _s = make_runner(
        tmp_path, cfg(symbols=["BTCUSDT"], mode="close"), signals=signals)
    await runner.housekeeping()
    key = (ids[0], "BTCUSDT")
    runner.on_tick("BTCUSDT", clock.ms, 100.0, 1.0, "sell")
    clock.ms += 5000
    await runner.housekeeping()
    assert key not in runner.pending  # la vela de T0 sigue abierta

    clock.ms = T0 + M15 + 500
    runner.on_tick("BTCUSDT", clock.ms, 99.8, 1.0, "sell")  # primer tick de la vela nueva
    assert key not in runner.pending  # la decision va en cola: la toma `housekeeping`
    await runner.housekeeping()
    assert runner.pending[key].side == Side.SHORT
    runner.on_tick("BTCUSDT", clock.ms + 300, 99.7, 1.0, "sell")
    assert runner.positions[key].side == "SHORT" and runner.positions[key].entry_price == 99.7
    kinds = [e[3] for e in store.events(ids[0])]
    assert kinds[:2] == ["OPEN", "SIGNAL"]


@pytest.mark.asyncio
async def test_stale_symbols_do_not_open_and_a_stopped_test_closes_its_position(tmp_path):
    signals = {("p", "BTCUSDT", True): Signal.LONG}
    runner, store, _t, clock, ids, _s = make_runner(
        tmp_path, cfg(symbols=["BTCUSDT"], mode="intrabar", eval_seconds=5), signals=signals)
    await runner.housekeeping()
    key = (ids[0], "BTCUSDT")
    runner.on_tick("BTCUSDT", clock.ms, 100.0, 1.0, "buy")
    clock.ms += 200_000  # mas de 120 s sin ticks
    await runner.housekeeping()
    assert key not in runner.pending
    assert any(e[3] == "STALE" for e in store.events())

    runner.on_tick("BTCUSDT", clock.ms, 100.0, 1.0, "buy")
    clock.ms += 6000
    await runner.housekeeping()
    runner.on_tick("BTCUSDT", clock.ms + 1, 100.2, 1.0, "buy")
    assert key in runner.positions

    store.stop_test(ids[0], now_ms=clock.ms)
    clock.ms += 11_000
    await runner.housekeeping()
    assert runner.positions == {} and store.positions() == {}
    assert store.trades(ids[0])[0][1].close_reason == "MANUAL"


@pytest.mark.asyncio
async def test_positions_survive_a_restart_and_a_gap_exits_at_the_first_tick(tmp_path):
    signals = {("p", "BTCUSDT", True): Signal.LONG}
    runner, store, ticks, clock, ids, _s = make_runner(
        tmp_path, cfg(symbols=["BTCUSDT"], mode="intrabar", eval_seconds=5), signals=signals)
    await runner.housekeeping()
    runner.on_tick("BTCUSDT", clock.ms, 100.0, 1.0, "buy")
    clock.ms += 6000
    await runner.housekeeping()
    runner.on_tick("BTCUSDT", clock.ms + 1, 100.0, 1.0, "buy")
    runner.shutdown()

    clock.ms += 3_600_000
    again = LiveRunner(make_settings(), store, ticks, FakeRest(T0), clock=clock)
    assert (ids[0], "BTCUSDT") in again.positions
    await again.housekeeping()
    again.on_tick("BTCUSDT", clock.ms, 95.0, 1.0, "sell")  # el SL (99) salto durante la caida
    trade = store.trades(ids[0])[0][1]
    assert (trade.close_reason, trade.exit_price) == ("SL", 95.0)


def test_evaluation_is_fast_enough_for_live_use(tmp_path):
    book = CandleBook("BTCUSDT", "15m")
    rng = np.random.default_rng(1)
    price = 100 + np.cumsum(rng.normal(0, 0.3, 800))
    book.load([{"time": T0 - (800 - i) * M15, "open": p, "high": p + 0.4, "low": p - 0.4,
                "close": p, "quoteVol": 1} for i, p in enumerate(price)], T0)
    store = LiveStore(tmp_path / "live.db")
    runner = LiveRunner(make_settings(), store, None, FakeRest(T0), clock=Clock(T0))
    runner.books[("BTCUSDT", "15m")] = book
    c = cfg(symbols=["BTCUSDT"], indicators=["rf", "ema", "vidya", "wt", "sfp", "fvg"])

    t0 = time.perf_counter()
    for i in range(3):
        book.on_tick(float(price[-1]) + i * 0.01, 1.0, T0 + i * 1000)
        assert runner.evaluate(c, "BTCUSDT", include_forming=True) is not None
    assert (time.perf_counter() - t0) / 3 < 2.0


@pytest.mark.asyncio
async def test_evaluations_are_capped_per_cycle_and_run_off_the_event_loop(tmp_path):
    signals = {}
    symbols = ["BTCUSDT", "ETHUSDT", "TRXUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT"]
    runner, _store, _t, clock, ids, _s = make_runner(
        tmp_path, cfg(symbols=symbols, mode="intrabar", eval_seconds=60), signals=signals)
    runner.max_evals_per_cycle = 2
    calls = []
    real = runner.evaluate

    def counting(cfg_, symbol, include_forming):
        calls.append(symbol)
        return real(cfg_, symbol, include_forming)

    runner.evaluate = counting  # type: ignore[method-assign]
    await runner.housekeeping()
    for s in symbols:
        runner.on_tick(s, clock.ms, 100.0, 1.0, "buy")
    calls.clear()
    clock.ms += 1000
    await runner.housekeeping()
    assert len(calls) == 2  # tope de 2 por ciclo
    clock.ms += 1000
    await runner.housekeeping()
    assert len(calls) == 4 and len(set(calls)) == 4  # siguen las mas atrasadas, sin repetir

    # Sin `inline_eval` la cuenta real corre en un hilo y deja la senal igual que en linea.
    threaded = LiveRunner(make_settings(), runner.store, None, FakeRest(T0), clock=clock)
    threaded.tests = runner.tests
    threaded.books = runner.books
    threaded.last_price = dict(runner.last_price)
    threaded.last_tick_ms = dict(runner.last_tick_ms)
    got = await threaded._evaluate_async(runner.tests[ids[0]].config, "BTCUSDT", True)
    assert got is not None and got[0] in (Signal.LONG, Signal.SHORT, Signal.HOLD)


@pytest.mark.asyncio
async def test_switching_the_bot_off_closes_positions_ignores_ticks_and_on_reloads(tmp_path):
    signals = {("p", "BTCUSDT", True): Signal.LONG}
    runner, store, ticks, clock, ids, subscribed = make_runner(
        tmp_path, cfg(symbols=["BTCUSDT"], mode="intrabar", eval_seconds=5), signals=signals)
    await runner.housekeeping()
    key = (ids[0], "BTCUSDT")
    runner.on_tick("BTCUSDT", clock.ms, 100.0, 1.0, "buy")
    clock.ms += 6000
    await runner.housekeeping()
    runner.on_tick("BTCUSDT", clock.ms + 1, 100.2, 1.0, "buy")
    assert key in runner.positions

    store.set_bot_on(False, now_ms=clock.ms)
    clock.ms += 1000
    await runner.housekeeping()
    assert runner.paused and runner.positions == {} and store.positions() == {}
    trade = store.trades(ids[0])[0][1]
    assert trade.close_reason == "MANUAL" and trade.exit_price == 100.2
    assert subscribed[-1] == []  # deja de escuchar el mercado
    seen = runner.ticks_seen
    runner.on_tick("BTCUSDT", clock.ms + 5, 99.0, 1.0, "sell")
    assert runner.ticks_seen == seen  # apagado no mira ticks
    clock.ms += 11_000
    await runner.housekeeping()
    assert store.heartbeat()[1] == "apagado desde el dashboard"

    store.set_bot_on(True, now_ms=clock.ms)
    clock.ms += 1000
    await runner.housekeeping()
    assert not runner.paused and subscribed[-1] == ["BTCUSDT"]
    assert ("BTCUSDT", "15m") in runner.books  # velas recargadas desde REST
    kinds = [e[3] for e in store.events()]
    assert "PAUSE" in kinds and "RESUME" in kinds


# --- operacion configurable: margen, apalancamiento, direccion, TP parciales -----------------


def test_validate_accepts_and_roundtrips_the_trading_options():
    c = cfg(margin_usdt=25, leverage=5, direction="long", take_profits=[(1.5, 50), (3, 50)],
            breakeven=True, max_open=2, exit_name="pct", pct_exit=(1.0, None, 1.0))

    assert (c.margin_usdt, c.leverage, c.direction, c.max_open) == (25.0, 5, "long", 2)
    assert c.take_profits == ((1.5, 50.0), (3.0, 50.0)) and c.breakeven
    assert TestConfig.from_json(c.to_json()) == c
    old = TestConfig.from_json('{"name": "vieja", "symbols": ["BTCUSDT"], "timeframe": "1h", '
                               '"indicators": ["rf"]}')
    assert old.margin_usdt is None and old.take_profits == () and old.direction == "both"


@pytest.mark.parametrize("kw", [
    {"margin_usdt": 0.5}, {"leverage": 0}, {"leverage": 101}, {"direction": "up"},
    {"take_profits": [(2, 50), (1, 50)]}, {"take_profits": [(1, 60), (2, 60)]},
    {"take_profits": [(1, 10), (2, 10), (3, 10), (4, 10)]}, {"take_profits": [(1, 0)]},
    {"breakeven": True}, {"max_open": 0},
])
def test_validate_rejects_bad_trading_options(kw):
    with pytest.raises(ValueError):
        cfg(**kw)


def test_margin_and_leverage_set_the_size_the_fees_and_the_risk_cap():
    s = make_settings()
    pos = open_position(Side.LONG, 100.0, T0, sl=99.0, tp=110.0, trailing=None, settings=s,
                        signal_time_ms=T0, decision_price=100.0, margin_usdt=50, leverage=3)
    assert (pos.margin_usdt, pos.leverage, pos.notional) == (50, 3, pytest.approx(150))
    assert pos.qty == pytest.approx(1.5) and pos.fee_entry_usdt == pytest.approx(150 * 0.0006)
    assert pos.sl_margin_loss_pct == pytest.approx(3.0)  # 1 % del precio a 3x
    capped = open_position(Side.LONG, 100.0, T0, sl=95.0, tp=None, trailing=None, settings=s,
                           signal_time_ms=T0, decision_price=100.0, leverage=20)
    assert isinstance(capped, OpenRefusal) and capped.reason == "RISK_CAP"  # 5 % a 20x = 100 %


def test_partial_take_profits_close_by_level_and_add_up():
    s = make_settings()
    pos = open_position(Side.LONG, 100.0, T0, sl=98.0, tp=None, trailing=None, settings=s,
                        signal_time_ms=T0, decision_price=100.0,
                        take_profits=[(102.0, 0.5), (104.0, 0.5)])
    assert pos.tp is None and len(pos.tp_levels) == 2

    assert on_tick(pos, 102.5, T0 + 1, s) is None  # TP1: cierra la mitad en 102
    assert pos.qty == pytest.approx(0.5) and pos.partials[0][3] == "TP1"
    trade = on_tick(pos, 104.2, T0 + 2, s)  # TP2 cierra el resto en 104
    assert trade.close_reason == "TP2" and trade.qty == pytest.approx(1.0)
    assert trade.exit_price == pytest.approx(103.0)  # media ponderada
    assert [p[3] for p in trade.partials] == ["TP1", "TP2"]
    assert trade.pnl_gross_usdt == pytest.approx(0.5 * 2 + 0.5 * 4)
    fees = 100 * 0.0006 + (51 + 52) * 0.0006
    assert trade.pnl_net_usdt == pytest.approx(3.0 - fees)


def test_breakeven_after_tp1_and_a_remainder_that_rides_the_trailing():
    s = make_settings()
    be = open_position(Side.LONG, 100.0, T0, sl=98.0, tp=None, trailing=None, settings=s,
                       signal_time_ms=T0, decision_price=100.0,
                       take_profits=[(102.0, 0.5)], breakeven=True)
    on_tick(be, 102.0, T0 + 1, s)
    assert be.stop == 100.0
    trade = on_tick(be, 99.9, T0 + 2, s)
    assert trade.close_reason == "BREAKEVEN"
    assert trade.pnl_gross_usdt == pytest.approx(0.5 * 2 - 0.05)

    rider = open_position(Side.SHORT, 100.0, T0, sl=102.0, tp=None, trailing=1.0, settings=s,
                          signal_time_ms=T0, decision_price=100.0, take_profits=[(98.0, 0.3)])
    on_tick(rider, 97.0, T0 + 1, s)  # TP1 cierra 30 %, el trailing sigue con el resto
    assert rider.qty == pytest.approx(0.7) and rider.stop == pytest.approx(98.0)
    trade = on_tick(rider, 98.1, T0 + 2, s)
    assert trade.close_reason == "TRAILING" and len(trade.partials) == 2


@pytest.mark.asyncio
async def test_direction_filter_and_max_open_limit_the_entries(tmp_path):
    signals = {("p", "BTCUSDT", True): Signal.SHORT, ("p", "ETHUSDT", True): Signal.LONG,
               ("p", "SOLUSDT", True): Signal.LONG}
    runner, store, _t, clock, ids, _s = make_runner(
        tmp_path, cfg(symbols=["BTCUSDT", "ETHUSDT", "SOLUSDT"], mode="intrabar",
                      eval_seconds=5, direction="long", max_open=1, margin_usdt=20, leverage=4),
        signals=signals)
    await runner.housekeeping()
    for s in ("BTCUSDT", "ETHUSDT", "SOLUSDT"):
        runner.on_tick(s, clock.ms, 100.0, 1.0, "buy")
    clock.ms += 6000
    await runner.housekeeping()

    pending = sorted(k[1] for k in runner.pending)
    assert pending == ["ETHUSDT"]  # BTC era corto (solo largos) y SOL superaba el maximo
    assert any(e[3] == "SKIP" for e in store.events())
    runner.on_tick("ETHUSDT", clock.ms + 1, 100.0, 1.0, "buy")
    pos = runner.positions[(ids[0], "ETHUSDT")]
    assert (pos.margin_usdt, pos.leverage) == (20, 4)


def test_tick_store_prune_deletes_only_old_ticks_in_batches(tmp_path):
    ticks = TickStore(tmp_path / "ticks.db")
    ticks.append([("BTCUSDT", T0 + i, 1.0, 1.0, "buy") for i in range(25)])

    assert ticks.prune(T0 + 20, batch=7) == 20
    assert ticks.count() == 5
    assert ticks.prune(T0 + 20) == 0
    ticks.close()


@pytest.mark.asyncio
async def test_the_runner_prunes_ticks_older_than_the_retention(tmp_path):
    runner, store, ticks, clock, ids, _s = make_runner(tmp_path, cfg(symbols=["BTCUSDT"]))
    runner.ticks_retention_days = 1
    ticks.append([("BTCUSDT", clock.ms - 2 * 86_400_000, 1.0, 1.0, "buy"),
                  ("BTCUSDT", clock.ms - 3_600_000, 1.0, 1.0, "buy")])
    await runner.housekeeping()

    assert ticks.count() == 1
