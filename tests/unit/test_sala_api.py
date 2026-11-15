"""Pestana «Sala de trading»: rutas de la API, sus defensas y los datos de mercado."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes import auth as auth_routes
from app.api.routes import sala as sala_routes
from app.api.security import hash_password
from app.config import Settings
from app.manual.desk import ManualDesk
from app.manual.store import DEFAULT_BALANCE_USDT, ManualStore

PASSWORD = "correcta-123"
T0 = 1_763_000_000_000
ORDER = {"symbol": "BTCUSDT", "side": "LONG", "kind": "market", "margin_usdt": 10, "leverage": 10,
         "tp_pct": 2, "sl_pct": 1}


def make_settings(tmp_path) -> Settings:
    return Settings(_env_file=None, **{  # type: ignore[call-arg]
        "AUTH_USERNAME": "admin", "AUTH_PASSWORD_HASH": hash_password(PASSWORD),
        "JWT_SECRET": "un-secreto-de-prueba-bien-largo-no-el-real",
        "LIVE_TESTS_DB": str(tmp_path / "live.db"), "LIVE_TICKS_DB": str(tmp_path / "t.db")})


class FakeRest:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def get_kline(self, symbol, interval, limit=100, **_):
        self.calls.append("kline")
        return [{"time": T0 + i * 900_000, "open": "100", "high": "101", "low": "99",
                 "close": str(100 + i), "baseVol": "5"} for i in (1, 0)]

    async def get_depth(self, symbol, limit=None):
        self.calls.append("depth")
        return {"bids": [["99.9", "1.5"], ["99.8", "2"]], "asks": [["100.1", "0.7"], ["x", "1"]]}

    async def get_tickers(self, symbol=None):
        self.calls.append("ticker")
        return [{"symbol": symbol, "lastPrice": "100.5", "high": "104", "low": "97"}]


class Clock:
    ms = T0

    def __call__(self) -> float:
        return self.ms / 1000


def make_client(tmp_path, *, service: bool = True, paused: bool = False,
                authenticated: bool = True):
    settings = make_settings(tmp_path)
    app = FastAPI()
    app.state.settings = settings
    clock = Clock()
    desk = ManualDesk(settings, ManualStore(settings.live_tests_db), clock=clock)
    rest = FakeRest()
    app.state.live_tests = SimpleNamespace(
        desk=desk, rest=rest, last_error=None,
        runner=SimpleNamespace(paused=paused, funding={"BTCUSDT": 0.0001})) if service else None
    app.include_router(auth_routes.router)
    app.include_router(sala_routes.router)
    sala_routes._cache.clear()
    client = TestClient(app)
    if authenticated:
        r = client.post("/api/auth/login", json={"username": "admin", "password": PASSWORD})
        assert r.status_code == 200
    return client, desk, rest, clock


@pytest.mark.parametrize("method, path", [
    ("get", "/api/sala/state"), ("get", "/api/sala/market"), ("post", "/api/sala/orders"),
    ("post", "/api/sala/orders/1/cancel"), ("post", "/api/sala/positions/1/close"),
    ("post", "/api/sala/positions/1/breakeven"), ("post", "/api/sala/close-all"),
    ("post", "/api/sala/reset"),
])
def test_every_sala_route_requires_a_login(tmp_path, method, path):
    client, *_ = make_client(tmp_path, authenticated=False)
    assert getattr(client, method)(path).status_code == 401


def test_state_without_the_runner_shows_the_saved_account_read_only(tmp_path):
    client, *_ = make_client(tmp_path, service=False)
    out = client.get("/api/sala/state").json()
    assert out["runner"] == {"enabled": False, "bot_on": False, "alive": False}
    assert out["account"]["balance"] == DEFAULT_BALANCE_USDT and out["positions"] == []
    r = client.post("/api/sala/orders", json=ORDER)
    assert r.status_code == 409 and "LIVE_TESTS_ENABLED" in r.json()["detail"]


def test_place_fill_close_and_history_through_the_api(tmp_path):
    client, desk, _, clock = make_client(tmp_path)
    state = client.get("/api/sala/state", params={"symbol": "btcusdt"}).json()
    assert state["runner"]["enabled"] is True and "BTCUSDT" in desk.watched
    r = client.post("/api/sala/orders", json=ORDER)
    assert r.status_code == 200 and r.json()["order"]["kind"] == "market"
    clock.ms += 1000
    desk.on_tick("BTCUSDT", clock.ms, 100.0, 0.1, "buy")
    (pos,) = client.get("/api/sala/state").json()["positions"]
    assert pos["symbol"] == "BTCUSDT" and pos["entry_price"] == 100.0 and pos["no_sl"] is False
    r = client.post(f"/api/sala/positions/{pos['id']}/close", json={"fraction": 0.5})
    assert r.status_code == 200
    r = client.post(f"/api/sala/positions/{pos['id']}/close", json={})
    assert r.status_code == 200
    out = client.get("/api/sala/state").json()
    assert out["positions"] == [] and out["trades"][0]["close_reason"] == "MANUAL"
    assert out["account"]["balance"] == pytest.approx(DEFAULT_BALANCE_USDT
                                                      + out["trades"][0]["pnl_net_usdt"])


def test_an_order_with_partial_tps_and_trailing_goes_through_the_api(tmp_path):
    client, desk, _, clock = make_client(tmp_path)
    body = {**ORDER, "tp_pct": None, "sl_pct": 2, "take_profits": [[1.5, 50], [3, 50]],
            "breakeven": True, "trail_pct": 1}
    assert client.post("/api/sala/orders", json=body).status_code == 200
    clock.ms += 1000
    desk.on_tick("BTCUSDT", clock.ms, 100.0, 0.1, "buy")
    (pos,) = client.get("/api/sala/state").json()["positions"]
    assert [lv[1] for lv in pos["tp_levels"]] == [0.5, 0.5] and pos["breakeven"] is True
    assert pos["trailing"] == 1.0 and pos["trail_pct"] == 1
    r = client.post("/api/sala/orders", json={**body, "take_profits": [[1, 50]] * 4})
    assert r.status_code == 422


def test_invalid_orders_and_missing_items_return_the_reason(tmp_path):
    client, *_ = make_client(tmp_path)
    r = client.post("/api/sala/orders", json={**ORDER, "sl_pct": 9, "leverage": 10})
    assert r.status_code == 400 and "liquida antes" in r.json()["detail"]
    assert client.post("/api/sala/orders", json={**ORDER, "leverage": 0}).status_code == 422
    assert client.post("/api/sala/orders", json={**ORDER, "symbol": "BTC/USDT"}).status_code == 400
    assert client.post("/api/sala/orders/99/cancel", json={}).status_code == 400
    assert client.post("/api/sala/positions/99/close", json={}).status_code == 400


def test_with_the_bot_off_the_sala_does_not_trade(tmp_path):
    client, *_ = make_client(tmp_path, paused=True)
    out = client.get("/api/sala/state").json()
    assert out["runner"]["bot_on"] is False
    r = client.post("/api/sala/orders", json=ORDER)
    assert r.status_code == 409 and "bot está apagado" in r.json()["detail"]


def test_close_all_and_reset_need_their_confirmation_word(tmp_path):
    client, desk, _, clock = make_client(tmp_path)
    client.post("/api/sala/orders", json={**ORDER, "kind": "limit", "price": 90})
    assert client.post("/api/sala/close-all", json={"confirm": "si"}).status_code == 400
    assert client.post("/api/sala/close-all", json={"confirm": "cerrar"}).json() == {"closed": 0}
    assert desk.orders == {}
    assert client.post("/api/sala/reset", json={}).status_code == 400
    r = client.post("/api/sala/reset", json={"confirm": "REINICIAR"})
    assert r.status_code == 200 and desk.balance == DEFAULT_BALANCE_USDT


def test_writes_from_another_site_are_refused(tmp_path):
    client, *_ = make_client(tmp_path)
    r = client.post("/api/sala/orders", json=ORDER, headers={"Origin": "https://malo.example"})
    assert r.status_code == 403
    r = client.post("/api/sala/orders", content="x", headers={"Content-Type": "text/plain"})
    assert r.status_code == 415


def test_market_data_is_parsed_sorted_and_cached(tmp_path):
    client, _, rest, _ = make_client(tmp_path)
    out = client.get("/api/sala/market", params={"symbol": "BTCUSDT", "interval": "15m"}).json()
    assert [b["t"] for b in out["candles"]] == [T0, T0 + 900_000]
    assert out["candles"][-1]["c"] == 101.0
    assert out["depth"] == {"bids": [[99.9, 1.5], [99.8, 2.0]], "asks": [[100.1, 0.7]]}
    assert out["ticker"]["lastPrice"] == "100.5" and out["funding"] == 0.0001
    client.get("/api/sala/market", params={"symbol": "BTCUSDT", "interval": "15m"})
    assert rest.calls.count("kline") == 1  # la segunda vez sale de la cache
    assert client.get("/api/sala/market", params={"interval": "2h"}).status_code == 400
    only = client.get("/api/sala/market", params={"parts": "depth"}).json()
    assert "candles" not in only and only["depth"]["bids"]
