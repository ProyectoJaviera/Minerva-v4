"""Descarga incremental de velas de 1 minuto de Bitunix al archivo de minutos.

Usa `GET /api/v1/futures/market/kline` con `interval=1m` (verificado: maximo 200 velas por
llamada; con `endTime` devuelve velas con `open_time` estrictamente menor y se pagina hacia
atras, ver `app/market/ohlcv_history.py`). Sondeado el 2026-11-11: hay minutos desde enero de
2023 para BTCUSDT y TRXUSDT, sin huecos en las muestras.

- Solo pide lo que falta: hacia adelante desde el ultimo minuto guardado y hacia atras hasta el
  inicio pedido (o hasta el piso donde la API ya no devuelve nada, que se anota).
- Nunca guarda el minuto en curso: el fin efectivo es el inicio del minuto actual.
- Despues repara huecos con pedidos estrechos (Bitunix a veces omite una vela en una respuesta
  grande que si aparece al pedirla sola) y cuenta los que quedan (pueden ser cortes reales).
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol

from app.replay.minute_store import MINUTE_MS, MinuteStore, bar_from_raw

logger = logging.getLogger(__name__)

PAGE = 200
EMPTY_PAGE_RETRIES = 3


class KlineSource(Protocol):
    """Lo que la descarga necesita del cliente REST (`BitunixRestClient.get_kline`)."""

    def get_kline(self, symbol: str, interval: str, start_time: int | None = None,
                  end_time: int | None = None, limit: int = 100,
                  price_type: str = "LAST_PRICE") -> Awaitable[list[dict]]: ...


@dataclass
class DownloadReport:
    """Lo que hizo la descarga de un simbolo."""
    symbol: str
    requests: int = 0
    saved: int = 0
    rejected: int = 0
    gaps_before_repair: int = 0
    gaps_after_repair: int = 0
    floor_ms: int | None = None


async def _page_back(source: KlineSource, store: MinuteStore, rep: DownloadReport,
                     range_start: int, range_end: int,
                     progress: Callable[[str], None] | None) -> bool:
    """Baja `[range_start, range_end)` pagina por pagina hacia atras.

    Returns:
        `True` si la API se quedo sin datos antes de llegar a `range_start` (piso).
    """
    cursor = range_end
    while cursor > range_start:
        raw: list[dict] = []
        for _ in range(EMPTY_PAGE_RETRIES):  # una pagina vacia suelta no es el piso
            raw = await source.get_kline(symbol=rep.symbol, interval="1m", end_time=cursor,
                                         limit=PAGE)
            rep.requests += 1
            if raw:
                break
        rows = []
        for r in raw:
            try:
                row = bar_from_raw(r)
            except (KeyError, ValueError, TypeError):
                rep.rejected += 1
                continue
            if range_start <= row[0] < range_end:
                rows.append(row)
        if not raw:
            return True
        times = [int(r["time"]) for r in raw if "time" in r]
        oldest = min(times) if times else cursor
        rep.saved += store.upsert(rep.symbol, rows)
        if oldest >= cursor:  # la API no retrocedio: cortar en vez de girar en falso
            return True
        cursor = oldest
        if progress and rep.requests % 50 == 0:
            progress(f"{rep.symbol}: {rep.requests} pedidos, {rep.saved} minutos")
    return False


async def _repair_gaps(source: KlineSource, store: MinuteStore, rep: DownloadReport,
                       start: int, end: int) -> None:
    missing = store.missing_minutes(rep.symbol, start, end)
    rep.gaps_before_repair = len(missing)
    # Agrupa los minutos ausentes en tramos contiguos y pide cada tramo con una ventana chica.
    groups: list[list[int]] = []
    for t in missing:
        if groups and t - groups[-1][-1] == MINUTE_MS and len(groups[-1]) < PAGE - 4:
            groups[-1].append(t)
        else:
            groups.append([t])
    for g in groups[:500]:  # tope: un corte largo de la API no se repara pidiendo de a poco
        raw = await source.get_kline(symbol=rep.symbol, interval="1m",
                                     end_time=g[-1] + 2 * MINUTE_MS, limit=len(g) + 4)
        rep.requests += 1
        rows = []
        for r in raw:
            try:
                row = bar_from_raw(r)
            except (KeyError, ValueError, TypeError):
                rep.rejected += 1
                continue
            if start <= row[0] < end:
                rows.append(row)
        rep.saved += store.upsert(rep.symbol, rows)
    rep.gaps_after_repair = len(store.missing_minutes(rep.symbol, start, end))


async def download_symbol(source: KlineSource, store: MinuteStore, symbol: str,
                          start_ms: int, now_ms: int,
                          progress: Callable[[str], None] | None = None) -> DownloadReport:
    """Completa los minutos de `symbol` entre `start_ms` y el minuto actual (sin incluirlo).

    Args:
        source: cliente REST.
        store: archivo de minutos (abierto para escritura).
        symbol: simbolo.
        start_ms: inicio pedido, ms Unix (se redondea al minuto).
        now_ms: instante actual, ms Unix.
        progress: funcion para mensajes de avance, o `None`.

    Returns:
        El reporte de la descarga.

    Efectos:
        Red: GET `kline` a Bitunix. Escribe `minute_bars` y `minute_floor`.
    """
    rep = DownloadReport(symbol)
    start = start_ms // MINUTE_MS * MINUTE_MS
    end = now_ms // MINUTE_MS * MINUTE_MS  # el minuto en curso no se guarda
    floor = store.floor(symbol)
    if floor is not None and floor > start:
        start = floor
    bounds = store.bounds(symbol)
    if bounds is None:
        hit_floor = await _page_back(source, store, rep, start, end, progress)
    else:
        lo, hi = bounds
        hit_floor = False
        if hi + MINUTE_MS < end:
            await _page_back(source, store, rep, hi + MINUTE_MS, end, progress)
        if lo > start:
            hit_floor = await _page_back(source, store, rep, start, lo, progress)
    if hit_floor:
        b = store.bounds(symbol)
        if b is not None:
            store.set_floor(symbol, b[0])
            start = max(start, b[0])
    rep.floor_ms = store.floor(symbol)
    await _repair_gaps(source, store, rep, start, end)
    return rep
