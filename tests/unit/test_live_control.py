"""v4: el gemelo de control al azar de las pruebas en vivo (sorteo, salida, archivo, corredor)."""

from __future__ import annotations

import random
import sqlite3

import numpy as np
import pandas as pd
import pytest

from app.explorer.confluence import ConfluenceStrategy
from app.live import control
from app.live.control import (
    RandomExitStrategy,
    compare,
    control_config,
    control_name,
    draw_entry,
    entry_delay_ms,
    signal_rate,
)
from app.live.report import detail, overview
from app.live.store import LiveStore
from app.persistence.models import Side
from app.strategies.base import Signal
from tests.unit.test_live import M15, T0, cfg, make_runner
from tests.unit.test_live_api import BODY, JSON, make_client, make_settings


class FixedRng(random.Random):
    """Devuelve los numeros dados en orden (para fijar el sorteo)."""

    def __init__(self, values: list[float]) -> None:
        super().__init__(0)
        self.values = list(values)

    def random(self) -> float:
        return self.values.pop(0) if self.values else 0.99

    def uniform(self, a: float, b: float) -> float:
        return a + (b - a) * self.random()


# --- sorteo ----------------------------------------------------------------------------------


def test_the_twin_copies_everything_but_the_name():
    base = cfg(name="x" * 60, mode="intrabar", direction="long", take_profits=[(1, 50), (2, 50)],
               breakeven=True, max_open=2, margin_usdt=25, leverage=5)
    twin = control_config(base)

    assert twin.name == control_name(base.name) and len(twin.name) == 60
    assert twin.name.startswith("Azar: ")
    assert {**twin.__dict__, "name": base.name} == base.__dict__


@pytest.mark.parametrize("eligible, signals, rate", [
    (0, 0, 0.0), (0, 3, 0.0), (10, 0, 0.0), (10, 1, 0.1), (4, 4, 1.0), (2, 5, 1.0),
])
def test_signal_rate_edges(eligible, signals, rate):
    assert signal_rate(eligible, signals) == pytest.approx(rate)


def test_draw_entry_respects_the_rate_and_the_direction():
    assert draw_entry(FixedRng([0.0]), 0.0, "both") is None  # sin senales, nunca entra
    assert draw_entry(FixedRng([0.30]), 0.25, "both") is None
    assert draw_entry(FixedRng([0.20, 0.4]), 0.25, "both") == Side.LONG
    assert draw_entry(FixedRng([0.20, 0.6]), 0.25, "both") == Side.SHORT
    assert draw_entry(FixedRng([0.20]), 0.25, "long") == Side.LONG
    assert draw_entry(FixedRng([0.20]), 0.25, "short") == Side.SHORT
    assert draw_entry(FixedRng([0.999]), 1.0, "both") is not None


def test_draw_entry_frequency_and_balance_over_many_bars():
    rng = random.Random(1)
    sides = [draw_entry(rng, 0.1, "both") for _ in range(20_000)]
    taken = [s for s in sides if s is not None]

    assert 0.09 < len(taken) / len(sides) < 0.11
    assert 0.46 < sum(s == Side.LONG for s in taken) / len(taken) < 0.54


def test_entry_delay_is_zero_at_close_and_inside_the_candle_intrabar():
    assert entry_delay_ms(random.Random(3), cfg(mode="close")) == 0
    rng = random.Random(3)
    delays = [entry_delay_ms(rng, cfg(mode="intrabar", timeframe="1h")) for _ in range(500)]
    assert min(delays) >= 0 and max(delays) < 3_600_000 and max(delays) > 1_800_000


# --- salida ----------------------------------------------------------------------------------


def _frame(n: int = 600) -> pd.DataFrame:
    rng = np.random.default_rng(5)
    close = 100 + np.cumsum(rng.normal(0, 0.5, n))
    return pd.DataFrame({"open_time": T0 + np.arange(n, dtype="int64") * M15, "open": close,
                         "high": close + rng.uniform(0.1, 1, n),
                         "low": close - rng.uniform(0.1, 1, n), "close": close,
                         "volume": np.ones(n)})


@pytest.mark.parametrize("exit_name, pct", [
    ("s2_tp2r", None), ("s15_tp3r", None), ("s3_trail3", None), ("pct", (1.5, 3.0, 0.8)),
])
def test_the_twin_exits_match_the_strategy_exits(exit_name, pct):
    frame = _frame()
    c = cfg(indicators=["ema", "rf"], exit_name=exit_name, pct_exit=pct)
    real = ConfluenceStrategy(c.indicators, c.timeframe, window=c.window, exit_name=c.exit_name,
                              min_agree=c.min_agree, pct_exit=c.pct_exit)
    real.evaluate(frame)  # en el corredor la salida se pide despues de evaluar
    twin = RandomExitStrategy(c, frame)
    for side in (Signal.LONG, Signal.SHORT):
        assert twin.stop_price(frame, side, 101.0) == pytest.approx(
            real.stop_price(frame, side, 101.0))
        r_tp, t_tp = real.take_profit_price(frame, side, 101.0), \
            twin.take_profit_price(frame, side, 101.0)
        assert (r_tp is None and t_tp is None) or t_tp == pytest.approx(r_tp)
    r_tr, t_tr = real.trailing_distance(frame), twin.trailing_distance(frame)
    assert (r_tr is None and t_tr is None) or t_tr == pytest.approx(r_tr)


# --- comparacion -----------------------------------------------------------------------------


def test_compare_needs_enough_trades_per_side():
    out = compare([1.0] * 28 + [2.0], [0.0, 1.0] * 20)
    assert out["verdict"] == "pocas_operaciones" and out["diff"] == pytest.approx(30 / 29 - 0.5,
                                                                                  abs=1e-3)
    assert compare([], [])["diff"] is None and compare([], [])["t"] is None
    assert compare([1.0], [2.0])["t"] is None  # una sola operacion: sin varianza


def test_compare_verdicts():
    rng = np.random.default_rng(11)
    noise = list(rng.normal(0, 1, 60))
    better = [x + 2 for x in noise]
    assert compare(better, noise)["verdict"] == "le_gana"
    assert compare(noise, better)["verdict"] == "pierde"
    assert compare(noise, list(rng.normal(0, 1, 60)))["verdict"] == "no_se_distingue"
    same = compare([1.0] * 40, [1.0] * 40)
    assert same["verdict"] == "no_se_distingue" and same["t"] is None


# --- archivo ---------------------------------------------------------------------------------


def test_the_store_creates_one_twin_per_test_and_stops_it_with_its_test(tmp_path):
    store = LiveStore(tmp_path / "live.db")
    a = store.add_test(cfg(name="a"), now_ms=T0)
    b = store.add_test(cfg(name="b"), now_ms=T0)
    created = store.ensure_controls(now_ms=T0 + 1)
    assert len(created) == 2 and store.ensure_controls() == []  # una sola vez

    twins = {t.control_of: t for t in store.tests() if t.control_of is not None}
    assert set(twins) == {a, b} and twins[a].config.name == "Azar: a"
    assert store.stop_test(twins[a].id) is False  # un gemelo no se detiene solo
    assert store.stop_test(a) is True
    status = {t.id: t.status for t in store.tests()}
    assert status[a] == status[twins[a].id] == "stopped"
    assert status[b] == status[twins[b].id] == "active"
    # Una prueba detenida no recibe un gemelo nuevo.
    assert store.ensure_controls() == []

    store.bump_rate(b, eligible=3)
    store.bump_rate(b, eligible=2, signals=1)
    assert store.rates() == {a: (0, 0), b: (5, 1)}


def test_a_v3_file_is_migrated_and_read_only_still_works(tmp_path):
    path = tmp_path / "live.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE live_tests (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT "
                 "NULL, config_json TEXT NOT NULL, status TEXT NOT NULL, created_at_ms INTEGER "
                 "NOT NULL, stopped_at_ms INTEGER)")
    conn.execute("INSERT INTO live_tests (name, config_json, status, created_at_ms) VALUES "
                 "(?, ?, 'active', ?)", ("vieja", cfg(name="vieja").to_json(), T0))
    conn.commit()
    conn.close()
    with LiveStore(path, read_only=True) as ro:
        assert [t.control_of for t in ro.tests()] == [None] and ro.rates() == {}
    with LiveStore(path) as rw:
        assert len(rw.ensure_controls()) == 1


# --- corredor --------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_twin_is_created_counts_bars_and_enters_at_the_strategy_frequency(tmp_path):
    signals = {("p", "BTCUSDT", False): Signal.SHORT}
    runner, store, _t, clock, ids, _s = make_runner(
        tmp_path, cfg(symbols=["BTCUSDT"], mode="close"), signals=signals)
    runner.rng = FixedRng([0.0, 0.3])  # el gemelo entra en cuanto la frecuencia sea positiva
    await runner.housekeeping()
    twin_id = runner._twin_of[ids[0]]
    assert runner.tests[twin_id].config.name == "Azar: p"

    # Vela 1: sin senales previas el gemelo no entra; la vela cuenta como elegible y la prueba
    # da su senal (la decision va despues del sorteo: el gemelo no mira adelante).
    runner.on_tick("BTCUSDT", clock.ms, 100.0, 1.0, "sell")
    clock.ms = T0 + M15 + 500
    runner.on_tick("BTCUSDT", clock.ms, 99.8, 1.0, "sell")
    assert runner._twin_due == []
    await runner.housekeeping()
    assert runner.rates[ids[0]] == (1, 1) and store.rates()[ids[0]] == (1, 1)
    runner.on_tick("BTCUSDT", clock.ms + 300, 99.7, 1.0, "sell")
    assert (ids[0], "BTCUSDT") in runner.positions

    # Vela 2: la prueba tiene posicion (no es elegible), el gemelo sortea con frecuencia 1/1.
    clock.ms = T0 + 2 * M15 + 500
    runner.on_tick("BTCUSDT", clock.ms, 99.7, 1.0, "sell")
    assert runner.rates[ids[0]] == (1, 1)
    assert [(d[1], d[2], d[3]) for d in runner._twin_due] == [(twin_id, "BTCUSDT", Side.LONG)]
    await runner.housekeeping()
    pend = runner.pending[(twin_id, "BTCUSDT")]
    assert pend.side == Side.LONG and isinstance(pend.strategy, RandomExitStrategy)
    runner.on_tick("BTCUSDT", clock.ms + 300, 99.75, 1.0, "buy")
    pos = runner.positions[(twin_id, "BTCUSDT")]
    assert pos.side == "LONG" and pos.entry_price == 99.75 and pos.stop < 99.75
    kinds = [e[3] for e in store.events(twin_id)]
    assert kinds[:2] == ["OPEN", "SIGNAL"]


@pytest.mark.asyncio
async def test_intrabar_twin_waits_a_random_moment_inside_the_candle(tmp_path):
    runner, store, _t, clock, ids, _s = make_runner(
        tmp_path, cfg(symbols=["BTCUSDT"], mode="intrabar", eval_seconds=5), signals={})
    await runner.housekeeping()
    twin_id = runner._twin_of[ids[0]]
    runner.rates[ids[0]] = (1, 1)
    runner.rng = FixedRng([0.0, 0.7, 0.5])  # entra, corto, a mitad de la vela
    runner.on_tick("BTCUSDT", clock.ms, 100.0, 1.0, "buy")
    clock.ms = T0 + M15 + 100
    runner.on_tick("BTCUSDT", clock.ms, 100.0, 1.0, "buy")
    (due, tid, _sym, side), = runner._twin_due
    assert tid == twin_id and side == Side.SHORT and due == clock.ms + M15 // 2

    await runner.housekeeping()
    assert (twin_id, "BTCUSDT") not in runner.pending  # todavia no llega su momento
    clock.ms = due + 10
    runner.on_tick("BTCUSDT", clock.ms, 100.1, 1.0, "buy")
    await runner.housekeeping()
    assert runner.pending[(twin_id, "BTCUSDT")].side == Side.SHORT


@pytest.mark.asyncio
async def test_the_twin_respects_max_open_stale_data_and_stops_with_its_test(tmp_path):
    runner, store, _t, clock, ids, _s = make_runner(
        tmp_path, cfg(symbols=["BTCUSDT", "ETHUSDT"], mode="close", max_open=1), signals={})
    await runner.housekeeping()
    twin_id = runner._twin_of[ids[0]]
    runner.rates[ids[0]] = (1, 1)
    runner.rng = FixedRng([0.0, 0.1, 0.0, 0.1])
    for sym in ("BTCUSDT", "ETHUSDT"):
        runner.on_tick(sym, clock.ms, 100.0, 1.0, "buy")
    clock.ms = T0 + M15 + 100
    runner.on_tick("BTCUSDT", clock.ms, 100.0, 1.0, "buy")
    runner.on_tick("ETHUSDT", clock.ms, 100.0, 1.0, "buy")
    # Tope de 1 posicion: solo una entrada sorteada queda en cola.
    assert len(runner._twin_due) == 1

    # Datos viejos: ni sortea ni cuenta la vela.
    await runner.housekeeping()
    runner.pending.clear()
    before = runner.rates[ids[0]]
    clock.ms = T0 + 2 * M15 + 200_000
    await runner.housekeeping()
    assert runner._twin_due == [] and runner.rates[ids[0]] == before

    # Al detener la prueba se detiene el gemelo y sus posiciones se cierran.
    runner.on_tick("BTCUSDT", clock.ms, 100.0, 1.0, "buy")
    store.stop_test(ids[0], now_ms=clock.ms)
    clock.ms += 11_000
    await runner.housekeeping()
    assert twin_id not in runner.tests and ids[0] not in runner.tests


@pytest.mark.asyncio
async def test_the_runner_without_controls_ignores_twins(tmp_path):
    runner, store, _t, clock, ids, _s = make_runner(tmp_path, cfg(symbols=["BTCUSDT"]),
                                                    signals={})
    runner.controls = False
    await runner.housekeeping()
    assert list(runner.tests) == ids and store.ensure_controls() != []
    runner._next_reload = 0
    await runner.housekeeping()
    assert list(runner.tests) == ids  # el gemelo existe en el archivo pero no se opera


# --- resumen y API ---------------------------------------------------------------------------


def test_overview_nests_the_twin_and_its_comparison(tmp_path):
    path = tmp_path / "live.db"
    with LiveStore(path) as store:
        test_id = store.add_test(cfg(name="real"), now_ms=T0)
        twin_id, = store.ensure_controls(now_ms=T0)
        store.bump_rate(test_id, eligible=20, signals=2)
    out = overview(path, now_s=T0 / 1000)
    (row,) = out["tests"]
    assert row["id"] == test_id and row["control"]["id"] == twin_id
    assert row["control"]["name"] == "Azar: real"
    assert row["rate"] == {"eligible_bars": 20, "signals": 2, "rate": pytest.approx(0.1)}
    assert row["vs_control"]["verdict"] == "pocas_operaciones"
    d = detail(path, test_id)
    assert d["control"]["id"] == twin_id and d["control_of"] is None
    assert detail(path, twin_id)["control_of"] == test_id


def test_the_api_creates_the_twin_and_refuses_to_stop_it_alone(tmp_path):
    settings = make_settings(tmp_path)
    client = make_client(settings)
    r = client.post("/api/live/tests", json=BODY, headers=JSON)
    assert r.status_code == 200
    test_id = r.json()["id"]
    (row,) = client.get("/api/live/tests").json()["tests"]
    twin_id = row["control"]["id"]
    assert row["id"] == test_id and row["control"]["name"] == "Azar: RF 15m"

    r = client.post(f"/api/live/tests/{twin_id}/stop", json={"confirm": "DETENER"}, headers=JSON)
    assert r.status_code == 404
    r = client.post(f"/api/live/tests/{test_id}/stop", json={"confirm": "DETENER"}, headers=JSON)
    assert r.status_code == 200
    (row,) = client.get("/api/live/tests").json()["tests"]
    assert row["status"] == "stopped" and row["control"]["status"] == "stopped"


def test_the_control_constants_are_sane():
    assert control.MIN_TRADES_FOR_VERDICT >= 30 and control.T_CRITICAL == pytest.approx(1.645)
    assert set(control.MS_BY_TIMEFRAME) == {"15m", "1h", "4h", "1d", "1w"}
