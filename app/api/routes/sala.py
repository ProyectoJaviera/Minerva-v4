"""Pestana «Sala de trading» (v4): trading manual en paper con el precio real de Bitunix.

La cuenta manual vive en las tablas `manual_*` del archivo de pruebas en vivo y la opera el
escritorio del corredor (`app.manual.desk.ManualDesk`), que recibe los ticks del WebSocket publico.
Las ordenes nunca salen de Minerva: todo es paper. No toca ninguna prueba «En vivo», su gemelo ni la
medicion.

- `GET /api/sala/state`: cuenta, posiciones con sus valores en vivo, ordenes, historial y
  operaciones recientes del mercado de la moneda mirada (que pasa a escucharse).
- `GET /api/sala/market`: velas (REST de Bitunix), libro de ordenes (REST de profundidad, 50
  niveles), ticker de 24 h y funding de una moneda, con cache corta para respetar los limites.
- `POST /api/sala/orders`, `.../orders/{id}/cancel`, `.../positions/{id}/close`,
  `.../positions/{id}/breakeven`, `.../close-all` (pide `CERRAR`) y `.../reset` (pide `REINICIAR`).

Las escrituras llevan las mismas defensas que el resto: login, mismo origen y solo JSON.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.api.deps import get_settings
from app.api.routes.control import require_json, require_same_origin
from app.api.security import require_auth
from app.config import Settings
from app.manual.desk import (
    MAX_LEVERAGE,
    MAX_TP_LEVELS,
    MAX_TP_PCT,
    MAX_TRAIL_PCT,
    SYMBOL_RE,
    ManualDesk,
    ManualError,
)
from app.manual.store import DEFAULT_BALANCE_USDT, ManualStore

router = APIRouter(prefix="/api/sala", dependencies=[Depends(require_auth)])

INTERVALS = ("1m", "5m", "15m", "1h", "4h", "1d")
DEPTH_LEVELS = "50"          # verificado contra la API publica (scripts/record_market_data.py)
CANDLES_TTL_S = 15.0
DEPTH_TTL_S = 1.0
TICKER_TTL_S = 3.0
CLOSE_ALL_WORD = "CERRAR"
RESET_WORD = "REINICIAR"

_cache: dict[tuple, tuple[float, Any]] = {}
_locks: dict[tuple, asyncio.Lock] = {}


class OrderRequest(BaseModel):
    """Cuerpo de `POST /api/sala/orders`."""
    symbol: str = Field(max_length=24)
    side: Literal["LONG", "SHORT"]
    kind: Literal["market", "limit"] = "market"
    margin_usdt: float = Field(gt=0, le=1_000_000)
    leverage: int = Field(ge=1, le=MAX_LEVERAGE)
    price: float | None = Field(default=None, gt=0)
    tp_pct: float | None = Field(default=None, gt=0, le=MAX_TP_PCT)
    sl_pct: float | None = Field(default=None, gt=0, le=100)
    take_profits: list[tuple[float, float]] = Field(default_factory=list, max_length=MAX_TP_LEVELS)
    breakeven: bool = False
    trail_pct: float | None = Field(default=None, gt=0, le=MAX_TRAIL_PCT)


class CloseRequest(BaseModel):
    """Cuerpo de `POST /api/sala/positions/{id}/close`."""
    fraction: float = Field(default=1.0, gt=0, le=1)


class ConfirmRequest(BaseModel):
    """Cuerpo de las acciones que piden una palabra de confirmacion."""
    confirm: str = Field(default="", max_length=16)


def _service(request: Request):
    return getattr(request.app.state, "live_tests", None)


def _desk(request: Request) -> ManualDesk:
    """El escritorio manual del corredor, listo para operar.

    Raises:
        HTTPException: 409 si el corredor no corre en esta instancia o el bot esta apagado.
    """
    service = _service(request)
    if service is None:
        raise HTTPException(status_code=409, detail="El corredor de «En vivo» está apagado en esta "
                            "instancia (LIVE_TESTS_ENABLED=false): la sala no puede operar.")
    if service.runner.paused:
        raise HTTPException(status_code=409, detail="El bot está apagado: enciéndelo en «En vivo» "
                            "para operar en la sala.")
    return service.desk


def _act(fn, *args) -> Any:
    try:
        return fn(*args)
    except ManualError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _check_symbol(symbol: str) -> str:
    symbol = symbol.strip().upper()
    if not SYMBOL_RE.match(symbol):
        raise HTTPException(status_code=400, detail=f"Moneda invalida: {symbol}.")
    return symbol


def _offline_state(path: str) -> dict:
    """Lo guardado de la sala cuando el corredor no corre (solo lectura, sin precios)."""
    try:
        store = ManualStore(path, read_only=True)
    except Exception:  # noqa: BLE001 - sin archivo todavia: cuenta nueva
        return {"account": {"balance": DEFAULT_BALANCE_USDT, "equity": DEFAULT_BALANCE_USDT,
                            "available": DEFAULT_BALANCE_USDT, "used_margin": 0.0, "upnl": 0.0,
                            "realized_today": 0.0, "start_balance": DEFAULT_BALANCE_USDT,
                            "reset_at_ms": 0},
                "positions": [], "orders": [], "trades": [], "recent": [], "prices": {},
                "events": []}
    try:
        balance, reset_at = store.balance()
        positions = [{"id": pid, "symbol": sym, **pos.to_dict(), "price": None,
                      "no_sl": bool(meta.get("no_sl"))}
                     for pid, (sym, pos, meta) in store.positions().items()]
        return {"account": {"balance": balance, "equity": balance, "available": None,
                            "used_margin": None, "upnl": None, "realized_today": None,
                            "start_balance": DEFAULT_BALANCE_USDT, "reset_at_ms": reset_at},
                "positions": positions, "orders": list(store.orders().values()),
                "trades": store.trades(limit=100, since_ms=reset_at), "recent": [],
                "prices": {}, "events": []}
    except Exception:  # noqa: BLE001 - tablas todavia no creadas: cuenta nueva
        return _offline_state("")
    finally:
        store.close()


@router.get("/state")
async def state(request: Request, symbol: str = Query("BTCUSDT", max_length=24),
                watch: str = Query("", max_length=400),
                settings: Settings = Depends(get_settings)) -> dict:
    """`GET /api/sala/state`: el estado de la sala (y empieza a escuchar `symbol`).

    Args:
        request: la peticion (para llegar al corredor).
        symbol: moneda que se esta mirando.
        watch: otras monedas de la lista «Monedas», separadas por comas (hasta 10), para tener
            su precio en vivo.
        settings: configuracion (inyectada).

    Returns:
        `runner` (`enabled`, `bot_on`, `alive`), `account`, `positions`, `orders`, `trades`,
        `recent`, `prices`, `events` y `fees`.

    Efectos:
        Marca `symbol` (y las de `watch`) como miradas: el corredor se suscribe a sus
        operaciones en segundos.
    """
    symbol = _check_symbol(symbol)
    service = _service(request)
    if service is None:
        out = await asyncio.to_thread(_offline_state, settings.live_tests_db)
        out["runner"] = {"enabled": False, "bot_on": False, "alive": False}
        out["fees"] = {"taker_pct": settings.taker_fee_pct,
                       "slippage_bps": settings.backtest_slippage_bps}
        return out
    desk: ManualDesk = service.desk
    if not service.runner.paused:
        extra = [w for w in (x.strip().upper() for x in watch.split(",")) if SYMBOL_RE.match(w)]
        for w in [*extra[:10], symbol]:
            desk.watch(w)
    out = desk.snapshot(symbol)
    out["runner"] = {"enabled": True, "bot_on": not service.runner.paused,
                     "alive": service.last_error is None}
    return out


async def _cached(key: tuple, ttl: float, fetch) -> Any:
    now = time.monotonic()
    hit = _cache.get(key)
    if hit is not None and now - hit[0] < ttl:
        return hit[1]
    lock = _locks.setdefault(key, asyncio.Lock())
    async with lock:
        hit = _cache.get(key)
        if hit is not None and time.monotonic() - hit[0] < ttl:
            return hit[1]
        value = await fetch()
        _cache[key] = (time.monotonic(), value)
        if len(_cache) > 200:
            _cache.clear()
        return value


def _rest(request: Request):
    service = _service(request)
    if service is not None:
        return service.rest
    client = getattr(request.app.state, "rest_client", None)
    if client is None:
        raise HTTPException(status_code=503, detail="Sin cliente de datos de mercado.")
    return client


def _candles(raw: list[dict]) -> list[dict]:
    out = []
    for r in raw:
        try:
            out.append({"t": int(r["time"]), "o": float(r["open"]), "h": float(r["high"]),
                        "l": float(r["low"]), "c": float(r["close"]),
                        "v": float(r.get("baseVol") or 0)})
        except (KeyError, TypeError, ValueError):
            continue
    return sorted(out, key=lambda b: b["t"])


def _levels(raw: Any) -> list[list[float]]:
    out = []
    for lv in raw or []:
        try:
            out.append([float(lv[0]), float(lv[1])])
        except (IndexError, TypeError, ValueError):
            continue
    return out


@router.get("/market")
async def market(request: Request, symbol: str = Query("BTCUSDT", max_length=24),
                 interval: str = Query("15m", max_length=4),
                 parts: str = Query("candles,depth,ticker", max_length=40)) -> dict:
    """`GET /api/sala/market`: velas, libro de ordenes, ticker y funding de una moneda.

    Args:
        request: la peticion.
        symbol: moneda.
        interval: marco de las velas (`1m`, `5m`, `15m`, `1h`, `4h`, `1d`).
        parts: que traer, separado por comas (`candles`, `depth`, `ticker`).

    Raises:
        HTTPException: 400 si la moneda o el marco no son validos; 503 sin cliente de mercado.

    Returns:
        `symbol`, `interval` y, segun `parts`, `candles` (200 velas, la ultima en curso),
        `depth` (`bids` y `asks` como `[precio, cantidad]`), `ticker` (el de Bitunix tal cual) y
        `funding` (tasa vigente, o `None`). Una parte que falla viene como `None` con su motivo en
        `errors`.

    Efectos:
        Red: GET publicos de Bitunix (con cache de 15 s para velas, 1 s para el libro y 3 s para
        el ticker).
    """
    symbol = _check_symbol(symbol)
    if interval not in INTERVALS:
        raise HTTPException(status_code=400, detail=f"Marco invalido (usa {', '.join(INTERVALS)}).")
    rest = _rest(request)
    wanted = {p.strip() for p in parts.split(",")}
    out: dict[str, Any] = {"symbol": symbol, "interval": interval, "errors": {}}

    async def part(name: str, key: tuple, ttl: float, fetch, convert) -> None:
        if name not in wanted:
            return
        try:
            out[name] = convert(await _cached(key, ttl, fetch))
        except Exception as exc:  # noqa: BLE001 - una parte caida no tumba la sala
            out[name] = None
            out["errors"][name] = f"{type(exc).__name__}: {exc}"[:200]

    await asyncio.gather(
        part("candles", ("k", symbol, interval), CANDLES_TTL_S,
             lambda: rest.get_kline(symbol=symbol, interval=interval, limit=200), _candles),
        part("depth", ("d", symbol), DEPTH_TTL_S,
             lambda: rest.get_depth(symbol, DEPTH_LEVELS),
             lambda d: {"bids": _levels((d or {}).get("bids")),
                        "asks": _levels((d or {}).get("asks"))}),
        part("ticker", ("t", symbol), TICKER_TTL_S, lambda: rest.get_tickers(symbol),
             lambda rows: next((r for r in rows if r.get("symbol") == symbol), None)),
    )
    service = _service(request)
    out["funding"] = None if service is None else service.runner.funding.get(symbol)
    return out


@router.post("/orders", dependencies=[Depends(require_same_origin), Depends(require_json)])
async def place_order(request: Request, body: OrderRequest) -> dict:
    """`POST /api/sala/orders`: crea una orden a mercado o limite (paper).

    Args:
        request: la peticion.
        body: la orden.

    Returns:
        `order` creada (una a mercado se llena con el primer tick siguiente).

    Raises:
        HTTPException: 400 si la orden no es valida; 409 si el corredor no puede operar.
    """
    desk = _desk(request)
    order = _act(desk.place, _check_symbol(body.symbol), body.side, body.kind, body.margin_usdt,
                 body.leverage, body.price, body.tp_pct, body.sl_pct, body.take_profits,
                 body.breakeven, body.trail_pct)
    return {"order": order}


@router.post("/orders/{order_id}/cancel",
             dependencies=[Depends(require_same_origin), Depends(require_json)])
async def cancel_order(request: Request, order_id: int) -> dict:
    """`POST /api/sala/orders/{id}/cancel`: cancela una orden pendiente.

    Args:
        request: la peticion.
        order_id: id de la orden.

    Returns:
        `cancelled` con el id.
    """
    _act(_desk(request).cancel, order_id)
    return {"cancelled": order_id}


@router.post("/positions/{pos_id}/close",
             dependencies=[Depends(require_same_origin), Depends(require_json)])
async def close_position(request: Request, pos_id: int, body: CloseRequest) -> dict:
    """`POST /api/sala/positions/{id}/close`: cierra toda la posicion o una parte.

    Args:
        request: la peticion.
        pos_id: id de la posicion.
        body: `fraction` (0 a 1; 1 = todo).

    Returns:
        `closed` con el id y `fraction`.
    """
    _act(_desk(request).close, pos_id, body.fraction)
    return {"closed": pos_id, "fraction": body.fraction}


@router.post("/positions/{pos_id}/breakeven",
             dependencies=[Depends(require_same_origin), Depends(require_json)])
async def breakeven(request: Request, pos_id: int) -> dict:
    """`POST /api/sala/positions/{id}/breakeven`: mueve el SL al precio de entrada.

    Args:
        request: la peticion.
        pos_id: id de la posicion.

    Returns:
        `breakeven` con el id.
    """
    _act(_desk(request).breakeven, pos_id)
    return {"breakeven": pos_id}


@router.post("/close-all", dependencies=[Depends(require_same_origin), Depends(require_json)])
async def close_all(request: Request, body: ConfirmRequest) -> dict:
    """`POST /api/sala/close-all`: cierra todas las posiciones y cancela las ordenes.

    Args:
        request: la peticion.
        body: `confirm` con la palabra `CERRAR`.

    Returns:
        `closed`: cuantas posiciones se cerraron.

    Raises:
        HTTPException: 400 sin la palabra `CERRAR`.
    """
    if body.confirm.strip().upper() != CLOSE_ALL_WORD:
        raise HTTPException(status_code=400, detail=f"Escribe {CLOSE_ALL_WORD} para confirmar.")
    closed = _act(_desk(request).close_all)
    return {"closed": closed}


@router.post("/reset", dependencies=[Depends(require_same_origin), Depends(require_json)])
async def reset(request: Request, body: ConfirmRequest) -> dict:
    """`POST /api/sala/reset`: vuelve la cuenta manual al saldo inicial (borra posiciones y
    ordenes; el historial queda guardado).

    Args:
        request: la peticion.
        body: `confirm` con la palabra `REINICIAR`.

    Returns:
        `balance`: el saldo inicial.

    Raises:
        HTTPException: 400 sin la palabra `REINICIAR`.
    """
    if body.confirm.strip().upper() != RESET_WORD:
        raise HTTPException(status_code=400, detail=f"Escribe {RESET_WORD} para confirmar.")
    service = _service(request)
    if service is None:
        raise HTTPException(status_code=409, detail="El corredor no corre en esta instancia.")
    service.desk.reset()
    return {"balance": DEFAULT_BALANCE_USDT}
