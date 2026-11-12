"""Pestana «En vivo» (v3): crear, ver y detener las pruebas en vivo desde el dashboard.

Las pruebas viven en `LIVE_TESTS_DB`, aparte de la base del bot; el corredor (dentro de la app, ver
`app/live/service.py`) las relee cada pocos segundos. Las escrituras (crear, detener y el
interruptor del bot) solo tocan ese archivo, nunca la medicion ni la cuenta paper del bot, y llevan
las mismas defensas que los controles: login, mismo origen y solo JSON. Detener pide ademas la
palabra `DETENER`. El interruptor (`POST /api/live/bot`) enciende o apaga el corredor; apagar pide
`APAGAR`.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.deps import get_settings
from app.api.routes.control import require_json, require_same_origin
from app.api.security import require_auth
from app.config import Settings
from app.explorer.confluence import (
    DEFAULT_EXIT,
    DEFAULT_WINDOW,
    EXITS,
    INDICATORS,
    MAX_WINDOW,
    PCT_EXIT,
    PCT_EXIT_LABEL,
    WARMUP_BY_TIMEFRAME,
)
from app.live import report
from app.live.config import (
    DIRECTIONS,
    MAX_EVAL_SECONDS,
    MAX_LEVERAGE,
    MAX_MARGIN_USDT,
    MAX_SYMBOLS,
    MAX_TP_LEVELS,
    MIN_EVAL_SECONDS,
    MIN_MARGIN_USDT,
    MODES,
    validate,
)
from app.live.store import LiveStore
from app.research.forward import SYMBOLS

router = APIRouter(prefix="/api/live", dependencies=[Depends(require_auth)])

STOP_WORD = "DETENER"
OFF_WORD = "APAGAR"


class CreateTestRequest(BaseModel):
    """Cuerpo de `POST /api/live/tests`."""
    name: str = Field(max_length=60)
    symbols: list[str] = Field(max_length=MAX_SYMBOLS)
    timeframe: str = Field(max_length=4)
    indicators: list[str] = Field(max_length=len(INDICATORS))
    mode: str = Field(default="close", max_length=10)
    exit_name: str = Field(default=DEFAULT_EXIT, max_length=32)
    sl_pct: float | None = None
    tp_pct: float | None = None
    trail_pct: float | None = None
    window: int = DEFAULT_WINDOW
    min_agree: int | None = None
    eval_seconds: int = 60
    margin_usdt: float | None = None
    leverage: int | None = None
    direction: str = Field(default="both", max_length=8)
    take_profits: list[tuple[float, float]] = Field(default_factory=list, max_length=MAX_TP_LEVELS)
    breakeven: bool = False
    max_open: int | None = None


class StopRequest(BaseModel):
    """Cuerpo de `POST /api/live/tests/{id}/stop`: la palabra de confirmacion."""
    confirm: str = Field(max_length=16)


class BotSwitchRequest(BaseModel):
    """Cuerpo de `POST /api/live/bot`: encender o apagar (apagar pide la palabra `APAGAR`)."""
    on: bool
    confirm: str = Field(default="", max_length=16)


@router.get("/options")
async def options(settings: Settings = Depends(get_settings)) -> dict:
    """`GET /api/live/options`: lo que se puede elegir al crear una prueba.

    Args:
        settings: configuracion (inyectada).

    Returns:
        Indicadores, salidas, marcos, modos, limites, monedas sugeridas y si el corredor esta
        encendido en esta instancia.
    """
    return {
        "enabled": settings.live_tests_enabled,
        "indicators": [{"id": k, "label": v[0], "kind": v[1]} for k, v in INDICATORS.items()],
        "exits": [{"id": k, "label": v[0]} for k, v in EXITS.items()]
        + [{"id": PCT_EXIT, "label": PCT_EXIT_LABEL}],
        "timeframes": list(WARMUP_BY_TIMEFRAME), "modes": list(MODES),
        "default_symbols": list(SYMBOLS), "max_symbols": MAX_SYMBOLS,
        "max_window": MAX_WINDOW, "default_window": DEFAULT_WINDOW,
        "eval_seconds": {"min": MIN_EVAL_SECONDS, "max": MAX_EVAL_SECONDS, "default": 60},
        "margin": {"min": MIN_MARGIN_USDT, "max": MAX_MARGIN_USDT,
                   "default": settings.default_margin_usdt},
        "leverage": {"min": 1, "max": MAX_LEVERAGE, "default": settings.leverage},
        "directions": list(DIRECTIONS), "max_tp_levels": MAX_TP_LEVELS,
        "fees": {"taker_pct": settings.taker_fee_pct,
                 "slippage_bps": settings.backtest_slippage_bps},
        "max_sl_margin_loss_pct": settings.max_sl_margin_loss_pct,
    }


def _with_defaults(config: dict, settings: Settings) -> dict:
    """Completa el margen y el apalancamiento de una prueba que usa los valores por defecto."""
    out = dict(config)
    if out.get("margin_usdt") is None:
        out["margin_usdt"] = settings.default_margin_usdt
    if out.get("leverage") is None:
        out["leverage"] = settings.leverage
    return out


@router.get("/tests")
async def list_tests(settings: Settings = Depends(get_settings)) -> dict:
    """`GET /api/live/tests`: estado del corredor y resumen de cada prueba.

    Args:
        settings: configuracion (inyectada).

    Returns:
        `runner` y `tests` (ver `report.overview`), mas `enabled`.

    Efectos:
        Lee el archivo de pruebas en solo lectura, en un hilo aparte.
    """
    out = await asyncio.to_thread(report.overview, settings.live_tests_db)
    out["enabled"] = settings.live_tests_enabled
    for row in out["tests"]:
        row["config"] = _with_defaults(row["config"], settings)
    return out


@router.get("/positions")
async def live_positions(settings: Settings = Depends(get_settings)) -> dict:
    """`GET /api/live/positions`: las posiciones abiertas con sus valores en vivo (v4).

    El corredor guarda el ultimo precio de cada moneda una vez por segundo; con eso se calculan
    el PnL no realizado, el ROE, el resultado si se cerrara ahora y las distancias al SL, al TP y
    a la liquidacion, como en la ficha de una posicion de un exchange.

    Args:
        settings: configuracion (inyectada).

    Returns:
        `now_ms` y `positions` (ver `report.live_positions`).

    Efectos:
        Lee el archivo de pruebas en solo lectura, en un hilo aparte.
    """
    return await asyncio.to_thread(report.live_positions, settings.live_tests_db,
                                   settings.taker_fee_pct, settings.backtest_slippage_bps)


@router.get("/tests/{test_id}")
async def get_test(test_id: int, settings: Settings = Depends(get_settings)) -> dict:
    """`GET /api/live/tests/{test_id}`: detalle de una prueba.

    Args:
        test_id: la prueba.
        settings: configuracion (inyectada).

    Returns:
        El detalle (ver `report.detail`).

    Raises:
        HTTPException: 404 si no existe.

    Efectos:
        Lee el archivo de pruebas en solo lectura, en un hilo aparte.
    """
    out = await asyncio.to_thread(report.detail, settings.live_tests_db, test_id)
    if out is None:
        raise HTTPException(status_code=404, detail="No existe esa prueba")
    out["config"] = _with_defaults(out["config"], settings)
    return out


def _create(path: str, body: CreateTestRequest) -> dict:
    pct = (body.sl_pct, body.tp_pct, body.trail_pct) if body.exit_name == PCT_EXIT else None
    cfg = validate(name=body.name, symbols=body.symbols, timeframe=body.timeframe,
                   indicators=body.indicators, mode=body.mode, exit_name=body.exit_name,
                   pct_exit=pct, window=body.window, min_agree=body.min_agree,
                   eval_seconds=body.eval_seconds, margin_usdt=body.margin_usdt,
                   leverage=body.leverage, direction=body.direction,
                   take_profits=body.take_profits, breakeven=body.breakeven,
                   max_open=body.max_open)
    with LiveStore(path) as store:
        test_id = store.add_test(cfg)
        store.ensure_controls()  # v4: su gemelo de control al azar
    return {"id": test_id, "name": cfg.name}


@router.post("/tests", dependencies=[Depends(require_same_origin), Depends(require_json)])
async def create_test(body: CreateTestRequest, settings: Settings = Depends(get_settings)) -> dict:
    """`POST /api/live/tests`: crea una prueba, activa desde ahora.

    Args:
        body: la configuracion elegida.
        settings: configuracion (inyectada).

    Returns:
        `id` y `name` de la prueba creada.

    Raises:
        HTTPException: 422 con el motivo si la configuracion no es valida.

    Efectos:
        Escribe `live_tests` en el archivo de pruebas.
    """
    try:
        return await asyncio.to_thread(_create, settings.live_tests_db, body)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _stop(path: str, test_id: int) -> bool:
    with LiveStore(path) as store:
        return store.stop_test(test_id)


@router.post("/tests/{test_id}/stop",
             dependencies=[Depends(require_same_origin), Depends(require_json)])
async def stop_test(test_id: int, body: StopRequest,
                    settings: Settings = Depends(get_settings)) -> dict:
    """`POST /api/live/tests/{test_id}/stop`: detiene una prueba (cierra sus posiciones).

    Args:
        test_id: la prueba.
        body: la palabra `DETENER`.
        settings: configuracion (inyectada).

    Returns:
        `id` y `stopped`.

    Raises:
        HTTPException: 400 sin la palabra exacta; 404 si no existe o ya estaba detenida.

    Efectos:
        Escribe `live_tests` en el archivo de pruebas.
    """
    if body.confirm != STOP_WORD:
        raise HTTPException(status_code=400, detail=f"Escribe {STOP_WORD} para confirmar")
    stopped = await asyncio.to_thread(_stop, settings.live_tests_db, test_id)
    if not stopped:
        raise HTTPException(status_code=404, detail="La prueba no existe o ya estaba detenida")
    return {"id": test_id, "stopped": True}


def _switch(path: str, on: bool) -> bool:
    with LiveStore(path) as store:
        store.set_bot_on(on)
        return store.bot_on()


@router.post("/bot", dependencies=[Depends(require_same_origin), Depends(require_json)])
async def bot_switch(body: BotSwitchRequest, settings: Settings = Depends(get_settings)) -> dict:
    """`POST /api/live/bot`: enciende o apaga el bot de pruebas en vivo.

    Apagado, el bot cierra las posiciones abiertas con el ultimo precio y deja de escuchar el
    mercado (en unos segundos); encendido, vuelve a cargar las velas y a operar las pruebas
    activas. No toca la medicion ni la cuenta paper del bot heredado.

    Args:
        body: el estado pedido (y `APAGAR` para apagar).
        settings: configuracion (inyectada).

    Returns:
        `bot_on` (el estado guardado) y `enabled` (si el corredor existe en esta instancia).

    Raises:
        HTTPException: 400 si se pide apagar sin la palabra exacta.

    Efectos:
        Escribe `live_control` y `live_events` en el archivo de pruebas.
    """
    if not body.on and body.confirm != OFF_WORD:
        raise HTTPException(status_code=400, detail=f"Escribe {OFF_WORD} para confirmar")
    bot_on = await asyncio.to_thread(_switch, settings.live_tests_db, body.on)
    return {"bot_on": bot_on, "enabled": settings.live_tests_enabled}
