"""Sala de trading manual: ordenes, llenados, TP/SL, liquidacion, cierres y cuenta."""

from __future__ import annotations

import asyncio

import pytest

from app.config import Settings
from app.live.runner import LiveRunner
from app.live.store import LiveStore
from app.manual.desk import WATCH_SECONDS, ManualDesk, ManualError
from app.manual.store import DEFAULT_BALANCE_USDT, ManualStore

T0 = 1_763_000_000_000


class Clock:
    def __init__(self, ms: int = T0) -> None:
        self.ms = ms

    def __call__(self) -> float:
        return self.ms / 1000

    def tick(self, seconds: float = 1.0) -> int:
        self.ms += int(seconds * 1000)
        return self.ms


def settings() -> Settings:
    return Settings(_env_file=None)  # type: ignore[call-arg]


def make_desk(tmp_path, clock: Clock | None = None) -> tuple[ManualDesk, ManualStore, Clock]:
    clock = clock or Clock()
    store = ManualStore(tmp_path / "live.db")
    return ManualDesk(settings(), store, clock=clock), store, clock


def tick(desk: ManualDesk, clock: Clock, price: float, symbol: str = "BTCUSDT") -> None:
    desk.on_tick(symbol, clock.tick(1), price, 0.01, "buy")


def test_market_order_fills_on_the_next_tick_with_fee_and_slippage(tmp_path):
    desk, store, clock = make_desk(tmp_path)
    tick(desk, clock, 100.0)
    order = desk.place("btcusdt", "LONG", "market", 10, 10, tp_pct=2, sl_pct=1)
    assert order["symbol"] == "BTCUSDT" and desk.orders
    assert desk.available() == pytest.approx(DEFAULT_BALANCE_USDT - 10)
    # un tick con la misma hora que la orden no la llena (tiene que ser posterior)
    desk.on_tick("BTCUSDT", order["created_ms"], 100.0, 0.01, "buy")
    assert not desk.positions
    tick(desk, clock, 101.0)
    ((sym, pos, meta),) = desk.positions.values()
    assert sym == "BTCUSDT" and pos.entry_price == 101.0 and pos.leverage == 10
    assert pos.fee_entry_usdt == pytest.approx(100 * 0.0006)
    assert pos.slippage_entry_usdt == pytest.approx(100 * 5 / 10_000)
    assert pos.tp == pytest.approx(101 * 1.02) and pos.stop == pytest.approx(101 * 0.99)
    assert meta["no_sl"] is False and not desk.orders
    assert desk.balance == DEFAULT_BALANCE_USDT  # el saldo cambia solo al cerrar
    assert len(store.positions()) == 1 and not store.orders()


def test_take_profit_closes_and_credits_the_net_result(tmp_path):
    desk, store, clock = make_desk(tmp_path)
    tick(desk, clock, 100.0)
    desk.place("BTCUSDT", "LONG", "market", 10, 10, tp_pct=2, sl_pct=1)
    tick(desk, clock, 100.0)
    tick(desk, clock, 101.0)
    assert desk.positions
    tick(desk, clock, 102.5)
    assert not desk.positions
    (trade,) = store.trades()
    assert trade["close_reason"] == "TP" and trade["exit_price"] == pytest.approx(102.0)
    assert desk.balance == pytest.approx(DEFAULT_BALANCE_USDT + trade["pnl_net_usdt"])
    assert trade["pnl_net_usdt"] == pytest.approx(2.0 - 0.06 - 0.0612 - 0.05 - 0.051, abs=1e-6)


def test_short_stop_loss_fills_at_the_tick_price_when_it_jumps(tmp_path):
    desk, store, clock = make_desk(tmp_path)
    tick(desk, clock, 100.0)
    desk.place("BTCUSDT", "SHORT", "market", 20, 5, sl_pct=2)
    tick(desk, clock, 100.0)
    tick(desk, clock, 103.0)  # el SL (102) salto: se sale donde se transo
    (trade,) = store.trades()
    assert (trade["close_reason"], trade["exit_price"]) == ("SL", 103.0)
    assert trade["pnl_net_usdt"] < -0.03 * 100


def test_without_stop_loss_only_liquidation_closes_and_loses_about_the_margin(tmp_path):
    desk, store, clock = make_desk(tmp_path)
    tick(desk, clock, 100.0)
    desk.place("BTCUSDT", "LONG", "market", 10, 20)
    tick(desk, clock, 100.0)
    ((_, pos, meta),) = desk.positions.values()
    assert meta["no_sl"] is True and pos.tp is None and pos.liq > pos.stop
    tick(desk, clock, 96.5)
    assert desk.positions  # aun no llega a la liquidacion (~95.5)
    tick(desk, clock, 90.0)
    (trade,) = store.trades()
    assert trade["close_reason"] == "LIQUIDATION"
    assert -11.5 < trade["pnl_net_usdt"] < -8


def test_limit_order_fills_at_its_price_without_entry_slippage(tmp_path):
    desk, _, clock = make_desk(tmp_path)
    tick(desk, clock, 100.0)
    order = desk.place("BTCUSDT", "LONG", "limit", 10, 10, price=98.0)
    assert order["marketable"] is False
    tick(desk, clock, 99.0)
    assert desk.orders and not desk.positions
    tick(desk, clock, 97.5)
    ((_, pos, meta),) = desk.positions.values()
    assert pos.entry_price == 98.0 and pos.slippage_entry_usdt == 0.0 and meta["kind"] == "limit"


def test_marketable_limit_fills_on_the_next_tick_at_that_tick(tmp_path):
    desk, _, clock = make_desk(tmp_path)
    tick(desk, clock, 100.0)
    order = desk.place("BTCUSDT", "SHORT", "limit", 10, 10, price=99.0)  # bajo el precio
    assert order["marketable"] is True
    tick(desk, clock, 100.2)
    ((_, pos, _),) = desk.positions.values()
    assert pos.entry_price == 100.2


def test_cancel_frees_the_margin_and_a_second_cancel_fails(tmp_path):
    desk, store, clock = make_desk(tmp_path)
    tick(desk, clock, 100.0)
    order = desk.place("BTCUSDT", "LONG", "limit", 50, 10, price=90.0)
    assert desk.available() == pytest.approx(DEFAULT_BALANCE_USDT - 50)
    desk.cancel(order["id"])
    assert desk.available() == pytest.approx(DEFAULT_BALANCE_USDT) and not store.orders()
    with pytest.raises(ManualError):
        desk.cancel(order["id"])


@pytest.mark.parametrize("kwargs, message", [
    ({"symbol": "BTC-USD"}, "Moneda invalida"),
    ({"side": "UP"}, "largo o corto"),
    ({"margin_usdt": 0}, "margen"),
    ({"leverage": 101}, "apalancamiento"),
    ({"tp_pct": 600}, "TP"),
    ({"sl_pct": 5, "leverage": 20}, "liquida antes"),
    ({"kind": "limit", "price": None}, "precio limite"),
    ({"margin_usdt": 999.9}, "No alcanza"),
])
def test_invalid_orders_are_refused_with_a_reason(tmp_path, kwargs, message):
    desk, _, clock = make_desk(tmp_path)
    tick(desk, clock, 100.0)
    args = {"symbol": "BTCUSDT", "side": "LONG", "kind": "market", "margin_usdt": 10,
            "leverage": 10, "price": None, "tp_pct": None, "sl_pct": None, **kwargs}
    with pytest.raises(ManualError, match=message):
        desk.place(**args)
    assert not desk.orders


def test_partial_close_breakeven_and_full_close(tmp_path):
    desk, store, clock = make_desk(tmp_path)
    tick(desk, clock, 100.0)
    desk.place("BTCUSDT", "LONG", "market", 10, 10, sl_pct=2)
    tick(desk, clock, 100.0)
    (pid,) = desk.positions
    with pytest.raises(ManualError, match="va ganando"):
        desk.breakeven(pid)
    tick(desk, clock, 101.0)
    desk.close(pid, 0.5)
    pos = desk.positions[pid][1]
    assert pos.qty == pytest.approx(pos.qty_initial / 2)
    assert desk.used_margin() == pytest.approx(5.0)
    desk.breakeven(pid)
    assert pos.stop == pos.entry_price
    tick(desk, clock, 99.9)  # vuelve a la entrada: sale por breakeven
    (trade,) = store.trades()
    assert trade["close_reason"] == "BREAKEVEN" and len(trade["partials"]) == 2
    with pytest.raises(ManualError, match="cerrada"):
        desk.close(pid)


def test_manual_close_needs_a_recent_price(tmp_path):
    desk, _, clock = make_desk(tmp_path)
    tick(desk, clock, 100.0)
    desk.place("BTCUSDT", "LONG", "market", 10, 10)
    tick(desk, clock, 100.0)
    (pid,) = desk.positions
    clock.tick(300)  # cinco minutos sin ticks
    with pytest.raises(ManualError, match="Sin precio reciente"):
        desk.close(pid)


def test_state_survives_a_restart_and_reset_keeps_the_history(tmp_path):
    desk, store, clock = make_desk(tmp_path)
    tick(desk, clock, 100.0)
    desk.place("BTCUSDT", "LONG", "market", 10, 10, tp_pct=1)
    tick(desk, clock, 100.0)
    tick(desk, clock, 101.5)  # TP
    desk.place("BTCUSDT", "SHORT", "market", 10, 5)
    tick(desk, clock, 101.0)
    desk.place("BTCUSDT", "LONG", "limit", 10, 5, price=90)
    again = ManualDesk(settings(), ManualStore(tmp_path / "live.db"), clock=clock)
    assert again.balance == pytest.approx(desk.balance) and again.balance > DEFAULT_BALANCE_USDT
    assert len(again.positions) == 1 and len(again.orders) == 1
    assert again.snapshot("BTCUSDT")["trades"][0]["close_reason"] == "TP"
    again.reset()
    snap = again.snapshot("BTCUSDT")
    assert snap["account"]["balance"] == DEFAULT_BALANCE_USDT and not snap["positions"]
    assert not snap["orders"] and snap["trades"] == []
    assert len(store.trades(since_ms=0)) == 1  # el historial queda guardado


def test_snapshot_shows_live_values_and_watched_symbols_expire(tmp_path):
    desk, _, clock = make_desk(tmp_path)
    desk.watch("ethusdt")
    tick(desk, clock, 2000.0, "ETHUSDT")
    tick(desk, clock, 100.0)
    desk.place("BTCUSDT", "LONG", "market", 10, 10)
    tick(desk, clock, 100.0)
    tick(desk, clock, 102.0)
    snap = desk.snapshot("ETHUSDT")
    (pos,) = snap["positions"]
    assert pos["upnl"] == pytest.approx((102 - 100) * pos["qty"])
    assert pos["roe_pct"] == pytest.approx(pos["upnl"] / 10 * 100)
    assert snap["account"]["equity"] == pytest.approx(DEFAULT_BALANCE_USDT + pos["upnl"])
    assert snap["recent"][0]["price"] == 2000.0
    assert desk.symbols() == {"ETHUSDT", "BTCUSDT"}
    clock.tick(WATCH_SECONDS + 5)
    assert desk.symbols() == {"BTCUSDT"}  # ETH ya no se mira; BTC tiene posicion


def test_the_runner_feeds_the_desk_and_subscribes_its_symbols(tmp_path):
    clock = Clock()
    store = LiveStore(tmp_path / "live.db")
    desk = ManualDesk(settings(), ManualStore(tmp_path / "live.db"), clock=clock)
    sent: list[list[str]] = []

    async def on_symbols(symbols):
        sent.append(symbols)

    class NoRest:
        async def get_funding_rate_batch(self):
            return []

    runner = LiveRunner(settings(), store, None, NoRest(), clock=clock, on_symbols=on_symbols,
                        desk=desk)
    asyncio.run(runner.housekeeping())
    desk.watch("SOLUSDT")
    clock.tick(1)
    asyncio.run(runner.housekeeping())
    assert sent[-1] == ["SOLUSDT"]
    runner.on_tick("SOLUSDT", clock.tick(1), 72.1, 3.0, "sell")
    assert desk.last_price["SOLUSDT"] == 72.1 and desk.recent["SOLUSDT"][0][1] == 72.1


def test_partial_take_profits_close_in_steps_and_move_the_stop_to_entry(tmp_path):
    desk, store, clock = make_desk(tmp_path)
    tick(desk, clock, 100.0)
    desk.place("BTCUSDT", "LONG", "market", 10, 10, sl_pct=2, take_profits=[(3, 30), (1, 50)],
               breakeven=True)
    tick(desk, clock, 100.0)
    ((pid, (_, pos, _)),) = desk.positions.items()
    # ordenados por distancia; las fracciones son de la cantidad inicial
    assert [(round(lv[0], 6), lv[1]) for lv in pos.tp_levels] == [(101.0, 0.5), (103.0, 0.3)]
    assert pos.tp is None
    tick(desk, clock, 101.2)  # TP1: cierra el 50 % y el SL pasa a la entrada
    assert pos.qty == pytest.approx(pos.qty_initial * 0.5) and pos.stop == 100.0
    assert desk.used_margin() == pytest.approx(5.0)
    tick(desk, clock, 103.5)  # TP2: cierra otro 30 %; queda el 20 % con el SL en la entrada
    assert pos.qty == pytest.approx(pos.qty_initial * 0.2)
    tick(desk, clock, 99.8)
    (trade,) = store.trades()
    assert trade["close_reason"] == "BREAKEVEN"
    assert [p[3] for p in trade["partials"]] == ["TP1", "TP2", "BREAKEVEN"]
    assert pid not in desk.positions and trade["pnl_net_usdt"] > 0


def test_partial_take_profits_that_sum_100_close_the_whole_position(tmp_path):
    desk, store, clock = make_desk(tmp_path)
    tick(desk, clock, 100.0)
    desk.place("BTCUSDT", "SHORT", "market", 10, 5, take_profits=[(1, 60), (2, 40)])
    tick(desk, clock, 100.0)
    tick(desk, clock, 98.9)
    tick(desk, clock, 97.5)
    (trade,) = store.trades()
    assert trade["close_reason"] == "TP2" and not desk.positions


def test_trailing_stop_follows_the_best_price_and_closes_on_the_pullback(tmp_path):
    desk, store, clock = make_desk(tmp_path)
    tick(desk, clock, 100.0)
    desk.place("BTCUSDT", "LONG", "market", 10, 10, trail_pct=1)
    tick(desk, clock, 100.0)
    ((_, (_, pos, meta)),) = desk.positions.items()
    assert pos.trailing == pytest.approx(1.0) and meta["no_sl"] is False and pos.tp is None
    tick(desk, clock, 105.0)
    assert pos.stop == pytest.approx(104.0)
    tick(desk, clock, 104.5)  # retrocede sin tocar el stop: no se mueve hacia abajo
    assert pos.stop == pytest.approx(104.0)
    tick(desk, clock, 103.9)
    (trade,) = store.trades()
    assert trade["close_reason"] == "TRAILING" and trade["exit_price"] == pytest.approx(103.9)
    assert desk.snapshot()["trades"][0]["pnl_net_usdt"] > 0


@pytest.mark.parametrize("kwargs, message", [
    ({"take_profits": [(1, 50), (2, 30), (3, 10), (4, 10)]}, "Como maximo 3"),
    ({"take_profits": [(1, 70), (2, 40)]}, "mas del 100 %"),
    ({"take_profits": [(1, 50), (1, 20)]}, "misma distancia"),
    ({"take_profits": [(0, 50)]}, "distancia"),
    ({"trail_pct": 60}, "trailing va de 0"),
    ({"trail_pct": 5, "leverage": 20}, "trailing debe ser menor"),
])
def test_invalid_partials_and_trailing_are_refused(tmp_path, kwargs, message):
    desk, _, clock = make_desk(tmp_path)
    tick(desk, clock, 100.0)
    args = {"symbol": "BTCUSDT", "side": "LONG", "kind": "market", "margin_usdt": 10,
            "leverage": 10, **kwargs}
    with pytest.raises(ManualError, match=message):
        desk.place(**args)
