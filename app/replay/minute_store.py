"""Archivo local de velas de 1 minuto de Bitunix (LAST_PRICE), aparte de la base del bot.

Vive por defecto en `history/minute_v4.db` (ajuste `MINUTE_DATA_DB`). Una tabla, `minute_bars`,
con clave `(symbol, open_time)` y sin rowid: unos 525 600 minutos por moneda y por ano. Se usa
`sqlite3` sincronico porque las lecturas son grandes y secuenciales (el motor de repeticion las
recorre de una vez) y la descarga escribe por lotes.

Formato de cada vela devuelto por `GET /api/v1/futures/market/kline` (verificado en la v1, ver
`app/market/ohlcv_history.py`): `time` (ms de apertura), `open`, `high`, `low`, `close`,
`baseVol`, `quoteVol`, todos como texto o numero.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

MINUTE_MS = 60_000

_SCHEMA = """
CREATE TABLE IF NOT EXISTS minute_bars (
    symbol TEXT NOT NULL,
    open_time INTEGER NOT NULL,
    open REAL NOT NULL,
    high REAL NOT NULL,
    low REAL NOT NULL,
    close REAL NOT NULL,
    base_vol REAL NOT NULL,
    quote_vol REAL NOT NULL,
    PRIMARY KEY (symbol, open_time)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS minute_floor (
    symbol TEXT PRIMARY KEY,
    floor_time INTEGER NOT NULL
);
"""


@dataclass(frozen=True, slots=True)
class MinuteBar:
    """Una vela de 1 minuto (precio de la ultima operacion, `LAST_PRICE`).

    Attributes:
        open_time: apertura en ms Unix UTC (multiplo de 60 000).
        open, high, low, close: precios.
        volume: volumen en USDT (`quoteVol`).
    """
    open_time: int
    open: float
    high: float
    low: float
    close: float
    volume: float


def bar_from_raw(raw: dict) -> tuple[int, float, float, float, float, float, float]:
    """Convierte una vela cruda de Bitunix en la fila de `minute_bars` (sin el simbolo).

    Args:
        raw: diccionario con `time`, `open`, `high`, `low`, `close`, `baseVol`, `quoteVol`.

    Returns:
        La tupla `(open_time, open, high, low, close, base_vol, quote_vol)`.

    Raises:
        ValueError: si falta un campo o la vela es incoherente (`high < low`, precio <= 0) o
            su `open_time` no cae en un minuto exacto. Un `open` o `close` fuera de
            `[low, high]` no es error: se amplia el rango.
    """
    t = int(raw["time"])
    o, h, lo, c = (float(raw[k]) for k in ("open", "high", "low", "close"))
    base = float(raw.get("baseVol") or 0.0)
    quote = float(raw.get("quoteVol") or 0.0)
    if t % MINUTE_MS != 0:
        raise ValueError(f"open_time {t} no es un minuto exacto")
    if min(o, h, lo, c) <= 0 or h < lo:
        raise ValueError(f"vela incoherente en {t}: o={o} h={h} l={lo} c={c}")
    # En Bitunix el `open` de un minuto es el cierre del anterior y puede quedar fuera del rango
    # de las operaciones de ese minuto (medido el 2026-11-11: ~10 % de los minutos de BTC y TRX).
    # Se amplia el rango para incluirlo, como lo dibuja cualquier grafico de velas.
    return t, o, max(h, o, c), min(lo, o, c), c, base, quote


class MinuteStore:
    """Lectura y escritura del archivo de minutos.

    Args:
        path: ruta del archivo SQLite; se crea con su esquema si no existe.
        read_only: abre en `mode=ro` (el motor de repeticion y la API nunca escriben).
    """

    def __init__(self, path: str | Path, read_only: bool = False) -> None:
        self.path = Path(path)
        if read_only:
            self._conn = sqlite3.connect(f"file:{self.path.as_posix()}?mode=ro", uri=True)
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(self.path)
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.executescript(_SCHEMA)

    def close(self) -> None:
        """Cierra la conexion."""
        self._conn.close()

    def __enter__(self) -> MinuteStore:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def upsert(self, symbol: str, rows: Iterable[tuple]) -> int:
        """Guarda (o reemplaza) velas de un simbolo.

        Args:
            symbol: simbolo, p. ej. `BTCUSDT`.
            rows: filas de `bar_from_raw`.

        Returns:
            Cuantas filas se escribieron.

        Efectos:
            Escribe `minute_bars` en una transaccion.
        """
        data = [(symbol, *r) for r in rows]
        with self._conn:
            self._conn.executemany(
                "INSERT OR REPLACE INTO minute_bars VALUES (?, ?, ?, ?, ?, ?, ?, ?)", data
            )
        return len(data)

    def bounds(self, symbol: str) -> tuple[int, int] | None:
        """El primer y el ultimo `open_time` guardados de un simbolo.

        Args:
            symbol: simbolo.

        Returns:
            La tupla `(primero, ultimo)`, o `None` si no hay minutos.

        Efectos:
            Lee `minute_bars`.
        """
        row = self._conn.execute(
            "SELECT MIN(open_time), MAX(open_time) FROM minute_bars WHERE symbol = ?", (symbol,)
        ).fetchone()
        return None if row is None or row[0] is None else (int(row[0]), int(row[1]))

    def count(self, symbol: str, start_ms: int, end_ms: int) -> int:
        """Cuantos minutos hay guardados con `start_ms <= open_time < end_ms`.

        Args:
            symbol: simbolo.
            start_ms: inicio (inclusive), ms Unix.
            end_ms: fin (exclusivo), ms Unix.

        Returns:
            La cantidad.

        Efectos:
            Lee `minute_bars`.
        """
        return int(self._conn.execute(
            "SELECT COUNT(*) FROM minute_bars "
            "WHERE symbol = ? AND open_time >= ? AND open_time < ?",
            (symbol, start_ms, end_ms),
        ).fetchone()[0])

    def set_floor(self, symbol: str, floor_ms: int) -> None:
        """Anota que la API no tiene minutos anteriores a `floor_ms` para ese simbolo.

        Args:
            symbol: simbolo.
            floor_ms: primer minuto disponible, ms Unix.

        Efectos:
            Escribe `minute_floor`.
        """
        with self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO minute_floor VALUES (?, ?)", (symbol, floor_ms)
            )

    def floor(self, symbol: str) -> int | None:
        """El piso anotado con `set_floor`.

        Args:
            symbol: simbolo.

        Returns:
            El piso en ms Unix, o `None`.

        Efectos:
            Lee `minute_floor`.
        """
        row = self._conn.execute(
            "SELECT floor_time FROM minute_floor WHERE symbol = ?", (symbol,)
        ).fetchone()
        return None if row is None else int(row[0])

    def load(self, symbol: str, start_ms: int, end_ms: int) -> list[MinuteBar]:
        """Los minutos con `start_ms <= open_time < end_ms`, en orden.

        Args:
            symbol: simbolo.
            start_ms: inicio (inclusive), ms Unix.
            end_ms: fin (exclusivo), ms Unix.

        Returns:
            La lista de `MinuteBar` ordenada por `open_time`.

        Efectos:
            Lee `minute_bars`.
        """
        cur = self._conn.execute(
            "SELECT open_time, open, high, low, close, quote_vol FROM minute_bars "
            "WHERE symbol = ? AND open_time >= ? AND open_time < ? ORDER BY open_time",
            (symbol, start_ms, end_ms),
        )
        return [MinuteBar(*row) for row in cur]

    def missing_minutes(self, symbol: str, start_ms: int, end_ms: int) -> list[int]:
        """Los `open_time` de minuto que faltan en `[start_ms, end_ms)` (para reparar huecos).

        Args:
            symbol: simbolo.
            start_ms: inicio (se redondea hacia arriba al minuto).
            end_ms: fin exclusivo.

        Returns:
            La lista ordenada de minutos ausentes.

        Efectos:
            Lee `minute_bars`.
        """
        first = -(-start_ms // MINUTE_MS) * MINUTE_MS
        have = {r[0] for r in self._conn.execute(
            "SELECT open_time FROM minute_bars "
            "WHERE symbol = ? AND open_time >= ? AND open_time < ?",
            (symbol, first, end_ms),
        )}
        return [t for t in range(first, end_ms, MINUTE_MS) if t not in have]
