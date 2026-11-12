"""Pestana «En vivo»: rutas de la API, sus defensas y el corredor dentro de la app."""

from __future__ import annotations

import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes import auth as auth_routes
from app.api.routes import live as live_routes
from app.api.security import hash_password
from app.config import Settings
from app.live import service as live_service
from app.live.report import overview
from app.live.store import LiveStore

PASSWORD = "correcta-123"
JSON = {"Content-Type": "application/json"}
BODY = {"name": "RF 15m", "symbols": ["BTCUSDT", "ethusdt"], "timeframe": "15m",
        "indicators": ["rf", "ema"], "mode": "intrabar", "eval_seconds": 30}


def make_settings(tmp_path, **overrides) -> Settings:
    defaults = {
        "AUTH_USERNAME": "admin", "AUTH_PASSWORD_HASH": hash_password(PASSWORD),
        "JWT_SECRET": "un-secreto-de-prueba-bien-largo-no-el-real",
        "LIVE_TESTS_DB": str(tmp_path / "live.db"), "LIVE_TICKS_DB": str(tmp_path / "t.db"),
    }
    defaults.update(overrides)
    return Settings(_env_file=None, **defaults)  # type: ignore[call-arg]


def make_client(settings: Settings, authenticated: bool = True) -> TestClient:
    app = FastAPI()
    app.state.settings = settings
    app.include_router(auth_routes.router)
    app.include_router(live_routes.router)
    client = TestClient(app)
    if authenticated:
        r = client.post("/api/auth/login", json={"username": "admin", "password": PASSWORD})
        assert r.status_code == 200
    return client


@pytest.mark.parametrize("method, path", [
    ("get", "/api/live/options"), ("get", "/api/live/tests"), ("get", "/api/live/tests/1"),
    ("post", "/api/live/tests"), ("post", "/api/live/tests/1/stop"), ("post", "/api/live/bot"),
])
def test_every_live_route_requires_a_login(tmp_path, method, path):
    client = make_client(make_settings(tmp_path), authenticated=False)

    assert getattr(client, method)(path).status_code == 401


def test_create_list_detail_and_stop_a_test(tmp_path):
    settings = make_settings(tmp_path)
    client = make_client(settings)

    assert client.get("/api/live/tests").json()["tests"] == []  # sin archivo todavia
    created = client.post("/api/live/tests", json=BODY)
    assert created.status_code == 200
    test_id = created.json()["id"]

    rows = client.get("/api/live/tests").json()
    assert rows["runner"]["alive"] is False and rows["enabled"] is False
    (row,) = rows["tests"]
    assert row["config"]["symbols"] == ["BTCUSDT", "ETHUSDT"] and row["status"] == "active"
    assert row["summary"]["trades"] == 0 and row["open_positions"] == 0

    detail = client.get(f"/api/live/tests/{test_id}").json()
    assert detail["config"]["indicators"] == ["rf", "ema"] and detail["trades"] == []
    assert client.get("/api/live/tests/99").status_code == 404

    assert client.post(f"/api/live/tests/{test_id}/stop", json={"confirm": "si"}).status_code == 400
    ok = client.post(f"/api/live/tests/{test_id}/stop", json={"confirm": "DETENER"})
    assert ok.status_code == 200
    again = client.post(f"/api/live/tests/{test_id}/stop", json={"confirm": "DETENER"})
    assert again.status_code == 404
    assert client.get("/api/live/tests").json()["tests"][0]["status"] == "stopped"


@pytest.mark.parametrize("bad", [
    {"timeframe": "3m"}, {"indicators": []}, {"indicators": ["nope"]}, {"mode": "tick"},
    {"exit_name": "pct"}, {"symbols": ["BTC"]}, {"eval_seconds": 1},
])
def test_invalid_tests_are_rejected_with_the_reason(tmp_path, bad):
    client = make_client(make_settings(tmp_path))
    r = client.post("/api/live/tests", json={**BODY, **bad})

    assert r.status_code == 422
    assert not (tmp_path / "live.db").exists() or LiveStore(tmp_path / "live.db").tests() == []


def test_a_pct_exit_is_accepted_with_its_sl(tmp_path):
    client = make_client(make_settings(tmp_path))
    r = client.post("/api/live/tests", json={**BODY, "exit_name": "pct", "sl_pct": 1.5,
                                             "tp_pct": 3, "trail_pct": None})

    assert r.status_code == 200
    detail = client.get(f"/api/live/tests/{r.json()['id']}").json()
    assert detail["config"]["pct_exit"] == [1.5, 3.0, None]


def test_writes_refuse_another_origin_and_non_json(tmp_path):
    client = make_client(make_settings(tmp_path))
    foreign = client.post("/api/live/tests", json=BODY, headers={"Origin": "https://evil.example"})
    form = client.post("/api/live/tests", content="name=x",
                       headers={"Content-Type": "application/x-www-form-urlencoded"})

    assert foreign.status_code == 403 and form.status_code == 415


def test_options_list_the_catalog(tmp_path):
    out = make_client(make_settings(tmp_path)).get("/api/live/options").json()

    assert {i["id"] for i in out["indicators"]} >= {"rf", "ema", "sfp", "btc"}
    assert "pct" in {e["id"] for e in out["exits"]} and out["modes"] == ["close", "intrabar"]
    assert out["timeframes"] == ["15m", "1h", "4h", "1d", "1w"]


def test_overview_marks_the_runner_alive_by_its_heartbeat(tmp_path):
    with LiveStore(tmp_path / "live.db") as store:
        store.beat("1 pruebas", now_ms=1_000_000)

    assert overview(tmp_path / "live.db", now_s=1_030)["runner"]["alive"] is True
    assert overview(tmp_path / "live.db", now_s=1_100)["runner"]["alive"] is False


@pytest.mark.asyncio
async def test_the_service_is_off_by_default_and_starts_its_tasks_when_enabled(tmp_path,
                                                                                monkeypatch):
    tasks: list = []
    assert live_service.maybe_start_live_tests(make_settings(tmp_path), tasks) is None
    assert tasks == []

    async def quiet_housekeeping(self):
        return None

    monkeypatch.setattr(live_service.LiveRunner, "housekeeping", quiet_housekeeping)
    settings = make_settings(tmp_path, LIVE_TESTS_ENABLED=True, LIVE_MAX_EVALS_PER_SECOND=2)
    svc = live_service.maybe_start_live_tests(settings, tasks)
    assert svc is not None and len(tasks) == 1 and svc.ws is None  # sin monedas, sin conexion
    assert svc.runner.max_evals_per_cycle == 2
    await asyncio.sleep(0.05)
    svc.stop()
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    await svc.aclose()
    with LiveStore(tmp_path / "live.db", read_only=True) as store:
        kinds = [e[3] for e in store.events()]
    assert "RUN" in kinds and "STOP" in kinds


@pytest.mark.asyncio
async def test_a_failing_housekeeping_round_does_not_kill_the_service(tmp_path, monkeypatch):
    calls = []

    async def boom(self):
        calls.append(1)
        raise RuntimeError("fallo de prueba")

    monkeypatch.setattr(live_service.LiveRunner, "housekeeping", boom)
    svc = live_service.LiveTestsService(make_settings(tmp_path, LIVE_RECORD_TICKS=False))
    assert svc.ticks is None
    task = asyncio.create_task(svc.run_forever())
    await asyncio.sleep(1.2)
    svc.stop()
    await asyncio.wait_for(task, timeout=3)
    await svc.aclose()

    assert len(calls) >= 2 and svc.last_error is not None


def test_the_bot_switch_needs_apagar_to_turn_off_and_shows_in_the_overview(tmp_path):
    client = make_client(make_settings(tmp_path))

    assert client.get("/api/live/tests").json()["runner"]["bot_on"] is True  # encendido por defecto
    assert client.post("/api/live/bot", json={"on": False}).status_code == 400
    assert client.post("/api/live/bot", json={"on": False, "confirm": "si"}).status_code == 400
    off = client.post("/api/live/bot", json={"on": False, "confirm": "APAGAR"})
    assert off.status_code == 200 and off.json()["bot_on"] is False
    runner = client.get("/api/live/tests").json()["runner"]
    assert runner["bot_on"] is False and runner["switch_changed_ms"] is not None
    on = client.post("/api/live/bot", json={"on": True})
    assert on.status_code == 200 and on.json()["bot_on"] is True
    foreign = client.post("/api/live/bot", json={"on": True},
                          headers={"Origin": "https://evil.example"})
    assert foreign.status_code == 403
    with LiveStore(tmp_path / "live.db", read_only=True) as store:
        assert [e[4] for e in store.events() if e[3] == "SWITCH"] == ["bot encendido",
                                                                      "bot apagado"]


@pytest.mark.asyncio
async def test_the_service_opens_and_closes_the_websocket_with_the_symbols(tmp_path,
                                                                           monkeypatch):
    started = []

    async def fake_run(self):
        started.append(list(self.symbols))
        await asyncio.sleep(3600)

    monkeypatch.setattr(live_service.BitunixPublicWSClient, "run_forever", fake_run)
    svc = live_service.LiveTestsService(make_settings(tmp_path, LIVE_RECORD_TICKS=False))
    await svc.sync_ws(["BTCUSDT"])
    await asyncio.sleep(0)
    first = svc.ws
    assert first is not None and started == [["BTCUSDT"]]
    await svc.sync_ws(["BTCUSDT", "ETHUSDT"])
    assert svc.ws is first and first.symbols == ["BTCUSDT", "ETHUSDT"]  # misma conexion
    await svc.sync_ws([])
    assert svc.ws is None and svc._ws_task is None
    await svc.sync_ws(["SOLUSDT"])
    await asyncio.sleep(0)
    assert svc.ws is not first and started[-1] == ["SOLUSDT"]  # conexion nueva al volver
    await svc.aclose()


def test_create_with_trading_options_and_options_list_them(tmp_path):
    client = make_client(make_settings(tmp_path))
    body = {**BODY, "margin_usdt": 30, "leverage": 5, "direction": "short",
            "take_profits": [[1, 50], [2, 50]], "breakeven": True, "max_open": 3}
    r = client.post("/api/live/tests", json=body)
    assert r.status_code == 200
    c = client.get(f"/api/live/tests/{r.json()['id']}").json()["config"]
    assert (c["margin_usdt"], c["leverage"], c["direction"], c["max_open"]) == (30.0, 5, "short", 3)
    assert c["take_profits"] == [[1.0, 50.0], [2.0, 50.0]] and c["breakeven"] is True
    bad = client.post("/api/live/tests", json={**body, "take_profits": [[1, 80], [2, 80]]})
    assert bad.status_code == 422 and "suman" in bad.json()["detail"]

    opts = client.get("/api/live/options").json()
    assert opts["leverage"]["max"] == 100 and opts["directions"] == ["both", "long", "short"]
    assert opts["max_tp_levels"] == 3 and opts["margin"]["min"] == 1.0


def test_tests_without_margin_or_leverage_show_the_defaults(tmp_path):
    settings = make_settings(tmp_path, DEFAULT_MARGIN_USDT=7, LEVERAGE=4)
    client = make_client(settings)
    test_id = client.post("/api/live/tests", json=BODY).json()["id"]

    row = client.get("/api/live/tests").json()["tests"][0]["config"]
    detail = client.get(f"/api/live/tests/{test_id}").json()["config"]
    assert (row["margin_usdt"], row["leverage"]) == (7.0, 4)
    assert (detail["margin_usdt"], detail["leverage"]) == (7.0, 4)
