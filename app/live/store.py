"""Archivos de las pruebas en vivo (SQLite, aparte de la base del bot).

- `history/live_v4.db` (`LiveStore`): las pruebas que definio el dueno, sus posiciones abiertas,
  sus operaciones cerradas y un registro de eventos (senales, rechazos, avisos del feed).
- `history/ticks_v4.db` (`TickStore`): cada operacion del canal `trade` del WebSocket publico
  (hora en ms, precio, cantidad y lado). Es la historia segundo a segundo que Bitunix no entrega
  gratis hacia atras; medido el 2026-11-11, BTC trae ~2 operaciones por segundo por este canal.

Los comandos y el proceso corredor abren el mismo archivo: `LiveStore` usa WAL y transacciones
cortas, y el corredor relee las pruebas cada pocos segundos.
"""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from app.live.config import TestConfig
from app.live.paper import ClosedTrade, PaperPosition

_LIVE_SCHEMA = """
CREATE TABLE IF NOT EXISTS live_tests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    config_json TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('active', 'stopped')),
    created_at_ms INTEGER NOT NULL,
    stopped_at_ms INTEGER
);
CREATE TABLE IF NOT EXISTS live_positions (
    test_id INTEGER NOT NULL,
    symbol TEXT NOT NULL,
    position_json TEXT NOT NULL,
    PRIMARY KEY (test_id, symbol)
);
CREATE TABLE IF NOT EXISTS live_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    test_id INTEGER NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    entry_time_ms INTEGER NOT NULL,
    exit_time_ms INTEGER NOT NULL,
    entry_price REAL NOT NULL,
    exit_price REAL NOT NULL,
    pnl_net_usdt REAL NOT NULL,
    close_reason TEXT NOT NULL,
    trade_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_live_trades_test ON live_trades (test_id, exit_time_ms);
CREATE TABLE IF NOT EXISTS live_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_ms INTEGER NOT NULL,
    test_id INTEGER,
    symbol TEXT,
    kind TEXT NOT NULL,
    detail TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_live_events_test ON live_events (test_id, ts_ms);
CREATE TABLE IF NOT EXISTS live_control (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    changed_at_ms INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS live_control_rates (
    test_id INTEGER PRIMARY KEY,
    eligible_bars INTEGER NOT NULL DEFAULT 0,
    signals INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS live_prices (
    symbol TEXT PRIMARY KEY,
    price REAL NOT NULL,
    ts_ms INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS live_heartbeat (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    ts_ms INTEGER NOT NULL,
    detail TEXT NOT NULL
);
"""

_TICK_SCHEMA = """
CREATE TABLE IF NOT EXISTS ticks (
    symbol TEXT NOT NULL,
    ts_ms INTEGER NOT NULL,
    price REAL NOT NULL,
    qty REAL NOT NULL,
    side TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ticks_symbol_ts ON ticks (symbol, ts_ms);
"""


@dataclass(frozen=True)
class StoredTest:
    """Una prueba guardada.

    Attributes:
        id: identificador.
        config: su configuracion.
        status: `active` o `stopped`.
        created_at_ms: cuando se creo (solo cuentan las operaciones posteriores).
        stopped_at_ms: cuando se detuvo, o `None`.
        control_of: si es el gemelo al azar de otra prueba, el id de esa prueba (v4).
    """
    id: int
    config: TestConfig
    status: str
    created_at_ms: int
    stopped_at_ms: int | None
    control_of: int | None = None


def _connect(path: Path, read_only: bool, schema: str) -> sqlite3.Connection:
    if read_only:
        return sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=10)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(schema)
    if schema is _LIVE_SCHEMA:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(live_tests)")}
        if "control_of" not in cols:
            # v4: el gemelo al azar apunta a su prueba. Los archivos de la v3 se migran solos.
            conn.execute("ALTER TABLE live_tests ADD COLUMN control_of INTEGER")
            conn.commit()
    return conn


class LiveStore:
    """Pruebas, posiciones, operaciones y eventos de las pruebas en vivo.

    Args:
        path: ruta del archivo; se crea con su esquema si no existe.
        read_only: abre en `mode=ro` (para los reportes).
    """

    def __init__(self, path: str | Path, read_only: bool = False) -> None:
        self.path = Path(path)
        self._conn = _connect(self.path, read_only, _LIVE_SCHEMA)
        tables = {r[0] for r in self._conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'")}
        cols = {r[1] for r in self._conn.execute("PRAGMA table_info(live_tests)")}
        # Un archivo de la v3 abierto en solo lectura no tiene las columnas de la v4.
        self._has_controls = "control_of" in cols and "live_control_rates" in tables
        self._has_prices = "live_prices" in tables

    def close(self) -> None:
        """Cierra la conexion."""
        self._conn.close()

    def __enter__(self) -> LiveStore:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # --- pruebas ---

    def add_test(self, config: TestConfig, now_ms: int | None = None) -> int:
        """Guarda una prueba nueva, activa desde ahora.

        Args:
            config: configuracion ya validada.
            now_ms: instante de creacion; por defecto, el reloj.

        Returns:
            El id de la prueba.

        Efectos:
            Escribe `live_tests`.
        """
        now = int(time.time() * 1000) if now_ms is None else now_ms
        with self._conn:
            cur = self._conn.execute(
                "INSERT INTO live_tests (name, config_json, status, created_at_ms) "
                "VALUES (?, ?, 'active', ?)", (config.name, config.to_json(), now))
        return int(cur.lastrowid)

    def stop_test(self, test_id: int, now_ms: int | None = None) -> bool:
        """Detiene una prueba activa y su gemelo al azar (no borra nada; no se puede reanudar).

        Un gemelo no se detiene solo: se detiene con su prueba.

        Args:
            test_id: la prueba.
            now_ms: instante; por defecto, el reloj.

        Returns:
            `True` si la prueba estaba activa y quedo detenida.

        Efectos:
            Escribe `live_tests`.
        """
        now = int(time.time() * 1000) if now_ms is None else now_ms
        with self._conn:
            cur = self._conn.execute(
                "UPDATE live_tests SET status = 'stopped', stopped_at_ms = ? "
                "WHERE id = ? AND status = 'active' AND control_of IS NULL", (now, test_id))
            stopped = cur.rowcount == 1
            if stopped:
                self._conn.execute(
                    "UPDATE live_tests SET status = 'stopped', stopped_at_ms = ? "
                    "WHERE control_of = ? AND status = 'active'", (now, test_id))
        return stopped

    def ensure_controls(self, now_ms: int | None = None) -> list[int]:
        """Crea el gemelo al azar de cada prueba activa que todavia no tiene uno (v4).

        Args:
            now_ms: instante de creacion; por defecto, el reloj.

        Returns:
            Los ids de los gemelos creados.

        Efectos:
            Escribe `live_tests` y `live_control_rates`.
        """
        from app.live.control import control_config

        now = int(time.time() * 1000) if now_ms is None else now_ms
        created = []
        for test in self.tests(only_active=True):
            if test.control_of is not None:
                continue
            has = self._conn.execute("SELECT 1 FROM live_tests WHERE control_of = ?",
                                     (test.id,)).fetchone()
            if has:
                continue
            twin = control_config(test.config)
            with self._conn:
                cur = self._conn.execute(
                    "INSERT INTO live_tests (name, config_json, status, created_at_ms, control_of) "
                    "VALUES (?, ?, 'active', ?, ?)", (twin.name, twin.to_json(), now, test.id))
                self._conn.execute("INSERT OR IGNORE INTO live_control_rates (test_id) VALUES (?)",
                                   (test.id,))
            created.append(int(cur.lastrowid))
        return created

    def bump_rate(self, test_id: int, eligible: int = 0, signals: int = 0) -> None:
        """Suma velas elegibles o senales al contador de frecuencia de una prueba (v4).

        Args:
            test_id: la prueba (no el gemelo).
            eligible: velas elegibles a sumar.
            signals: senales a sumar.

        Efectos:
            Escribe `live_control_rates`.
        """
        with self._conn:
            self._conn.execute(
                "INSERT INTO live_control_rates (test_id, eligible_bars, signals) VALUES (?, ?, ?) "
                "ON CONFLICT(test_id) DO UPDATE SET eligible_bars = eligible_bars + excluded."
                "eligible_bars, signals = signals + excluded.signals", (test_id, eligible, signals))

    def rates(self) -> dict[int, tuple[int, int]]:
        """Los contadores de frecuencia de todas las pruebas (v4).

        Returns:
            Un diccionario `test_id -> (velas elegibles, senales)`.

        Efectos:
            Lee `live_control_rates`.
        """
        if not self._has_controls:
            return {}
        return {int(r[0]): (int(r[1]), int(r[2])) for r in self._conn.execute(
            "SELECT test_id, eligible_bars, signals FROM live_control_rates")}

    def tests(self, only_active: bool = False) -> list[StoredTest]:
        """Las pruebas guardadas, por id.

        Args:
            only_active: solo las activas.

        Returns:
            La lista de pruebas (las que tengan una configuracion ilegible se omiten).

        Efectos:
            Lee `live_tests`.
        """
        control_col = "control_of" if self._has_controls else "NULL"
        sql = ("SELECT id, config_json, status, created_at_ms, stopped_at_ms, "
               f"{control_col} FROM live_tests")
        if only_active:
            sql += " WHERE status = 'active'"
        out = []
        for row in self._conn.execute(sql + " ORDER BY id"):
            try:
                cfg = TestConfig.from_json(row[1])
            except (ValueError, TypeError):
                continue
            out.append(StoredTest(int(row[0]), cfg, row[2], int(row[3]), row[4],
                                  None if row[5] is None else int(row[5])))
        return out

    # --- posiciones ---

    def save_position(self, test_id: int, symbol: str, pos: PaperPosition) -> None:
        """Guarda (o reemplaza) la posicion abierta de una prueba en una moneda.

        Args:
            test_id: la prueba.
            symbol: la moneda.
            pos: la posicion.

        Efectos:
            Escribe `live_positions`.
        """
        with self._conn:
            self._conn.execute("INSERT OR REPLACE INTO live_positions VALUES (?, ?, ?)",
                               (test_id, symbol, json.dumps(pos.to_dict())))

    def delete_position(self, test_id: int, symbol: str) -> None:
        """Borra la posicion abierta de una prueba en una moneda.

        Args:
            test_id: la prueba.
            symbol: la moneda.

        Efectos:
            Escribe `live_positions`.
        """
        with self._conn:
            self._conn.execute("DELETE FROM live_positions WHERE test_id = ? AND symbol = ?",
                               (test_id, symbol))

    def positions(self) -> dict[tuple[int, str], PaperPosition]:
        """Todas las posiciones abiertas guardadas.

        Returns:
            Un diccionario `(test_id, symbol) -> posicion`.

        Efectos:
            Lee `live_positions`.
        """
        out = {}
        for test_id, symbol, text in self._conn.execute(
                "SELECT test_id, symbol, position_json FROM live_positions"):
            out[(int(test_id), symbol)] = PaperPosition.from_dict(json.loads(text))
        return out

    # --- operaciones y eventos ---

    def record_trade(self, test_id: int, symbol: str, trade: ClosedTrade) -> None:
        """Guarda una operacion cerrada y borra su posicion, en una sola transaccion.

        Args:
            test_id: la prueba.
            symbol: la moneda.
            trade: la operacion.

        Efectos:
            Escribe `live_trades` y `live_positions`.
        """
        with self._conn:
            self._conn.execute(
                "INSERT INTO live_trades (test_id, symbol, side, entry_time_ms, exit_time_ms, "
                "entry_price, exit_price, pnl_net_usdt, close_reason, trade_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (test_id, symbol, trade.side, trade.entry_time_ms, trade.exit_time_ms,
                 trade.entry_price, trade.exit_price, trade.pnl_net_usdt, trade.close_reason,
                 json.dumps(asdict(trade))))
            self._conn.execute("DELETE FROM live_positions WHERE test_id = ? AND symbol = ?",
                               (test_id, symbol))

    def trades(self, test_id: int) -> list[tuple[str, ClosedTrade]]:
        """Las operaciones cerradas de una prueba, en orden de salida.

        Args:
            test_id: la prueba.

        Returns:
            Pares `(moneda, operacion)`.

        Efectos:
            Lee `live_trades`.
        """
        rows = self._conn.execute(
            "SELECT symbol, trade_json FROM live_trades WHERE test_id = ? "
            "ORDER BY exit_time_ms, id", (test_id,))
        return [(symbol, ClosedTrade(**json.loads(text))) for symbol, text in rows]

    def log(self, kind: str, detail: str, test_id: int | None = None,
            symbol: str | None = None, now_ms: int | None = None) -> None:
        """Anota un evento (senal, rechazo, aviso del feed).

        Args:
            kind: tipo corto, p. ej. `SIGNAL`, `REFUSED`, `STALE`, `OPEN`, `CLOSE`.
            detail: texto.
            test_id: la prueba, si aplica.
            symbol: la moneda, si aplica.
            now_ms: instante; por defecto, el reloj.

        Efectos:
            Escribe `live_events`.
        """
        now = int(time.time() * 1000) if now_ms is None else now_ms
        with self._conn:
            self._conn.execute(
                "INSERT INTO live_events (ts_ms, test_id, symbol, kind, detail) "
                "VALUES (?, ?, ?, ?, ?)", (now, test_id, symbol, kind, detail))

    def events(self, test_id: int | None = None, limit: int = 50) -> list[tuple]:
        """Los ultimos eventos, del mas nuevo al mas viejo.

        Args:
            test_id: solo los de esa prueba, o `None` para todos.
            limit: cuantos.

        Returns:
            Tuplas `(ts_ms, test_id, symbol, kind, detail)`.

        Efectos:
            Lee `live_events`.
        """
        if test_id is None:
            cur = self._conn.execute(
                "SELECT ts_ms, test_id, symbol, kind, detail FROM live_events "
                "ORDER BY id DESC LIMIT ?", (limit,))
        else:
            cur = self._conn.execute(
                "SELECT ts_ms, test_id, symbol, kind, detail FROM live_events "
                "WHERE test_id = ? ORDER BY id DESC LIMIT ?", (test_id, limit))
        return list(cur)

    def save_prices(self, prices: dict[str, tuple[float, int]]) -> None:
        """Guarda el ultimo precio conocido de cada moneda (v4, para ver las posiciones en vivo).

        Args:
            prices: `symbol -> (precio, hora del tick en ms)`.

        Efectos:
            Escribe `live_prices`.
        """
        if not prices:
            return
        with self._conn:
            self._conn.executemany(
                "INSERT OR REPLACE INTO live_prices VALUES (?, ?, ?)",
                [(s, float(p), int(ts)) for s, (p, ts) in prices.items()])

    def prices(self) -> dict[str, tuple[float, int]]:
        """El ultimo precio guardado de cada moneda (v4).

        Returns:
            `symbol -> (precio, hora del tick en ms)`; vacio en un archivo sin la tabla.

        Efectos:
            Lee `live_prices`.
        """
        if not self._has_prices:
            return {}
        return {r[0]: (float(r[1]), int(r[2])) for r in self._conn.execute(
            "SELECT symbol, price, ts_ms FROM live_prices")}

    def bot_on(self) -> bool:
        """Si el bot de pruebas en vivo esta encendido (interruptor del dashboard).

        Returns:
            `True` salvo que alguien lo haya apagado (encendido por defecto).

        Efectos:
            Lee `live_control`.
        """
        row = self._conn.execute(
            "SELECT value FROM live_control WHERE key = 'bot_on'").fetchone()
        return row is None or row[0] == "1"

    def set_bot_on(self, on: bool, now_ms: int | None = None) -> None:
        """Enciende o apaga el bot de pruebas en vivo.

        Args:
            on: el estado pedido.
            now_ms: instante; por defecto, el reloj.

        Efectos:
            Escribe `live_control` y `live_events`.
        """
        now = int(time.time() * 1000) if now_ms is None else now_ms
        with self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO live_control VALUES ('bot_on', ?, ?)",
                ("1" if on else "0", now))
        self.log("SWITCH", "bot encendido" if on else "bot apagado", now_ms=now)

    def bot_switch_changed_at(self) -> int | None:
        """Cuando se cambio el interruptor por ultima vez.

        Returns:
            El instante en ms, o `None` si nunca se toco.

        Efectos:
            Lee `live_control`.
        """
        row = self._conn.execute(
            "SELECT changed_at_ms FROM live_control WHERE key = 'bot_on'").fetchone()
        return None if row is None else int(row[0])

    def beat(self, detail: str, now_ms: int | None = None) -> None:
        """Anota que el corredor sigue vivo.

        Args:
            detail: resumen corto del estado.
            now_ms: instante; por defecto, el reloj.

        Efectos:
            Escribe `live_heartbeat`.
        """
        now = int(time.time() * 1000) if now_ms is None else now_ms
        with self._conn:
            self._conn.execute("INSERT OR REPLACE INTO live_heartbeat VALUES (1, ?, ?)",
                               (now, detail))

    def heartbeat(self) -> tuple[int, str] | None:
        """El ultimo latido del corredor.

        Returns:
            `(ts_ms, detalle)`, o `None` si nunca corrio.

        Efectos:
            Lee `live_heartbeat`.
        """
        row = self._conn.execute("SELECT ts_ms, detail FROM live_heartbeat WHERE id = 1").fetchone()
        return None if row is None else (int(row[0]), row[1])


class TickStore:
    """El archivo de ticks del canal `trade` (solo se agrega).

    Args:
        path: ruta del archivo; se crea con su esquema si no existe.
        read_only: abre en `mode=ro`.
    """

    def __init__(self, path: str | Path, read_only: bool = False) -> None:
        self.path = Path(path)
        self._conn = _connect(self.path, read_only, _TICK_SCHEMA)

    def close(self) -> None:
        """Cierra la conexion."""
        self._conn.close()

    def append(self, rows: list[tuple[str, int, float, float, str]]) -> int:
        """Agrega ticks en una transaccion.

        Args:
            rows: tuplas `(symbol, ts_ms, price, qty, side)`.

        Returns:
            Cuantos se escribieron.

        Efectos:
            Escribe `ticks`.
        """
        if not rows:
            return 0
        with self._conn:
            self._conn.executemany("INSERT INTO ticks VALUES (?, ?, ?, ?, ?)", rows)
        return len(rows)

    def prune(self, older_than_ms: int, batch: int = 50_000) -> int:
        """Borra los ticks mas viejos que un instante (en lotes, para no bloquear el archivo).

        El espacio liberado se reutiliza para los ticks nuevos: el archivo deja de crecer cuando
        llega al tamano de la retencion.

        Args:
            older_than_ms: se borran los ticks con `ts_ms` menor.
            batch: filas por transaccion.

        Returns:
            Cuantos se borraron.

        Efectos:
            Escribe `ticks`.
        """
        total = 0
        while True:
            with self._conn:
                cur = self._conn.execute(
                    "DELETE FROM ticks WHERE rowid IN (SELECT rowid FROM ticks WHERE ts_ms < ? "
                    "LIMIT ?)", (older_than_ms, batch))
            total += cur.rowcount
            if cur.rowcount < batch:
                return total

    def count(self, symbol: str | None = None) -> int:
        """Cuantos ticks hay guardados.

        Args:
            symbol: solo los de esa moneda, o `None` para todos.

        Returns:
            La cantidad.

        Efectos:
            Lee `ticks`.
        """
        if symbol is None:
            return int(self._conn.execute("SELECT COUNT(*) FROM ticks").fetchone()[0])
        return int(self._conn.execute("SELECT COUNT(*) FROM ticks WHERE symbol = ?",
                                      (symbol,)).fetchone()[0])
