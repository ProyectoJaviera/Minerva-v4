"""Archivo de la sala de trading manual: tablas `manual_*` dentro del archivo de pruebas en vivo.

El esquema es aditivo (`CREATE TABLE IF NOT EXISTS`): no toca las tablas `live_*` ni la base del
bot. Una sola fila de cuenta (`manual_account`, id 1) con el saldo realizado; las posiciones
abiertas se guardan como JSON de `PaperPosition` (mas su configuracion manual), las ordenes limite
pendientes y las operaciones cerradas con su JSON completo.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

from app.live.paper import ClosedTrade, PaperPosition

DEFAULT_BALANCE_USDT = 1000.0

_SCHEMA = """
CREATE TABLE IF NOT EXISTS manual_account (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    balance_usdt REAL NOT NULL,
    reset_at_ms INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS manual_positions (
    id INTEGER PRIMARY KEY,
    symbol TEXT NOT NULL,
    position_json TEXT NOT NULL,
    meta_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS manual_orders (
    id INTEGER PRIMARY KEY,
    symbol TEXT NOT NULL,
    order_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS manual_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    position_id INTEGER NOT NULL,
    symbol TEXT NOT NULL,
    exit_time_ms INTEGER NOT NULL,
    pnl_net_usdt REAL NOT NULL,
    close_reason TEXT NOT NULL,
    trade_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_manual_trades_exit ON manual_trades (exit_time_ms);
CREATE TABLE IF NOT EXISTS manual_seq (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    next_id INTEGER NOT NULL
);
"""


class ManualStore:
    """Lee y escribe las tablas `manual_*`.

    Args:
        path: archivo de pruebas en vivo (`LIVE_TESTS_DB`).
        read_only: abre en solo lectura (para el dashboard sin corredor).
    """

    def __init__(self, path: str | Path, read_only: bool = False) -> None:
        self.path = Path(path)
        if read_only:
            self.conn = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True, timeout=10,
                                        check_same_thread=False)
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            # Lo usa un solo hilo a la vez (el bucle del corredor y las rutas async de la app
            # corren en el mismo bucle); se permite otro hilo para las pruebas y el cierre.
            self.conn = sqlite3.connect(self.path, timeout=10, check_same_thread=False)
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.executescript(_SCHEMA)
            self.conn.commit()

    def close(self) -> None:
        """Cierra la conexion."""
        self.conn.close()

    # --- cuenta ---

    def balance(self) -> tuple[float, int]:
        """El saldo realizado y cuando se reinicio por ultima vez.

        Returns:
            `(saldo en USDT, ms Unix del ultimo reinicio)`; sin fila, el saldo inicial y 0.

        Efectos:
            Lee `manual_account`.
        """
        row = self.conn.execute("SELECT balance_usdt, reset_at_ms FROM manual_account "
                                "WHERE id = 1").fetchone()
        return (float(row[0]), int(row[1])) if row else (DEFAULT_BALANCE_USDT, 0)

    def set_balance(self, balance: float, reset_at_ms: int | None = None) -> None:
        """Guarda el saldo (y, si se da, la hora de un reinicio).

        Args:
            balance: saldo en USDT.
            reset_at_ms: hora del reinicio, o `None` para conservar la anterior.

        Efectos:
            Escribe `manual_account`.
        """
        _, prev = self.balance()
        self.conn.execute(
            "INSERT INTO manual_account (id, balance_usdt, reset_at_ms) VALUES (1, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET balance_usdt = excluded.balance_usdt, "
            "reset_at_ms = excluded.reset_at_ms",
            (balance, prev if reset_at_ms is None else reset_at_ms))
        self.conn.commit()

    def next_id(self) -> int:
        """Un id nuevo para una orden o posicion (unico entre las dos).

        Returns:
            El id.

        Efectos:
            Lee y escribe `manual_seq`.
        """
        row = self.conn.execute("SELECT next_id FROM manual_seq WHERE id = 1").fetchone()
        nid = int(row[0]) if row else 1
        self.conn.execute("INSERT INTO manual_seq (id, next_id) VALUES (1, ?) "
                          "ON CONFLICT(id) DO UPDATE SET next_id = excluded.next_id", (nid + 1,))
        self.conn.commit()
        return nid

    # --- posiciones ---

    def save_position(self, pos_id: int, symbol: str, pos: PaperPosition, meta: dict) -> None:
        """Guarda (o reemplaza) una posicion abierta.

        Args:
            pos_id: id de la posicion.
            symbol: moneda.
            pos: la posicion.
            meta: configuracion manual (sin SL, sin TP, etc.).

        Efectos:
            Escribe `manual_positions`.
        """
        self.conn.execute(
            "INSERT OR REPLACE INTO manual_positions (id, symbol, position_json, meta_json) "
            "VALUES (?, ?, ?, ?)", (pos_id, symbol, json.dumps(pos.to_dict()), json.dumps(meta)))
        self.conn.commit()

    def delete_position(self, pos_id: int) -> None:
        """Borra una posicion cerrada.

        Args:
            pos_id: id de la posicion.

        Efectos:
            Borra de `manual_positions`.
        """
        self.conn.execute("DELETE FROM manual_positions WHERE id = ?", (pos_id,))
        self.conn.commit()

    def positions(self) -> dict[int, tuple[str, PaperPosition, dict]]:
        """Las posiciones abiertas.

        Returns:
            `{id: (moneda, posicion, meta)}`.

        Efectos:
            Lee `manual_positions`.
        """
        rows = self.conn.execute("SELECT id, symbol, position_json, meta_json "
                                 "FROM manual_positions ORDER BY id").fetchall()
        return {int(r[0]): (r[1], PaperPosition.from_dict(json.loads(r[2])), json.loads(r[3]))
                for r in rows}

    # --- ordenes limite ---

    def save_order(self, order_id: int, symbol: str, order: dict) -> None:
        """Guarda una orden limite pendiente.

        Args:
            order_id: id de la orden.
            symbol: moneda.
            order: la orden (diccionario apto para JSON).

        Efectos:
            Escribe `manual_orders`.
        """
        self.conn.execute("INSERT OR REPLACE INTO manual_orders (id, symbol, order_json) "
                          "VALUES (?, ?, ?)", (order_id, symbol, json.dumps(order)))
        self.conn.commit()

    def delete_order(self, order_id: int) -> None:
        """Borra una orden llenada o cancelada.

        Args:
            order_id: id de la orden.

        Efectos:
            Borra de `manual_orders`.
        """
        self.conn.execute("DELETE FROM manual_orders WHERE id = ?", (order_id,))
        self.conn.commit()

    def orders(self) -> dict[int, dict]:
        """Las ordenes limite pendientes.

        Returns:
            `{id: orden}`.

        Efectos:
            Lee `manual_orders`.
        """
        rows = self.conn.execute("SELECT id, order_json FROM manual_orders ORDER BY id").fetchall()
        return {int(r[0]): json.loads(r[1]) for r in rows}

    # --- operaciones cerradas ---

    def record_trade(self, pos_id: int, symbol: str, trade: ClosedTrade) -> None:
        """Guarda una operacion cerrada.

        Args:
            pos_id: id de la posicion que cerro.
            symbol: moneda.
            trade: la operacion.

        Efectos:
            Escribe `manual_trades`.
        """
        self.conn.execute(
            "INSERT INTO manual_trades (position_id, symbol, exit_time_ms, pnl_net_usdt, "
            "close_reason, trade_json) VALUES (?, ?, ?, ?, ?, ?)",
            (pos_id, symbol, trade.exit_time_ms, trade.pnl_net_usdt, trade.close_reason,
             json.dumps(trade.__dict__)))
        self.conn.commit()

    def trades(self, limit: int = 100, since_ms: int = 0) -> list[dict]:
        """Las ultimas operaciones cerradas (la mas reciente primero).

        Args:
            limit: cuantas.
            since_ms: solo las cerradas desde este instante.

        Returns:
            Diccionarios con `id`, `position_id`, `symbol` y los campos de `ClosedTrade`.

        Efectos:
            Lee `manual_trades`.
        """
        rows = self.conn.execute(
            "SELECT id, position_id, symbol, trade_json FROM manual_trades "
            "WHERE exit_time_ms >= ? ORDER BY exit_time_ms DESC, id DESC LIMIT ?",
            (since_ms, limit)).fetchall()
        return [{"id": r[0], "position_id": r[1], "symbol": r[2], **json.loads(r[3])}
                for r in rows]

    def realized_since(self, since_ms: int) -> float:
        """Suma del resultado neto de las operaciones cerradas desde un instante.

        Args:
            since_ms: instante, ms Unix.

        Returns:
            USDT.

        Efectos:
            Lee `manual_trades`.
        """
        row = self.conn.execute("SELECT COALESCE(SUM(pnl_net_usdt), 0) FROM manual_trades "
                                "WHERE exit_time_ms >= ?", (since_ms,)).fetchone()
        return float(row[0])

    def reset(self, balance: float = DEFAULT_BALANCE_USDT, now_ms: int | None = None) -> None:
        """Reinicia la cuenta: borra posiciones y ordenes y vuelve al saldo inicial.

        El historial de operaciones se conserva (el dashboard muestra solo lo posterior al
        reinicio).

        Args:
            balance: saldo inicial.
            now_ms: hora del reinicio.

        Efectos:
            Borra `manual_positions` y `manual_orders` y escribe `manual_account`.
        """
        now_ms = int(time.time() * 1000) if now_ms is None else now_ms
        self.conn.execute("DELETE FROM manual_positions")
        self.conn.execute("DELETE FROM manual_orders")
        self.conn.commit()
        self.set_balance(balance, reset_at_ms=now_ms)
