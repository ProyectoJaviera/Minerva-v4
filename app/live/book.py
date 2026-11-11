"""Las velas de una moneda y un marco para las pruebas en vivo.

Las velas **cerradas** vienen de REST (`GET /api/v1/futures/market/kline`, la misma fuente del
backtest) al arrancar y se reconcilian despues de cada cierre. La vela **en curso** se arma con los
ticks del canal `trade`, con la convencion de Bitunix: su apertura es el cierre de la anterior.
Cuando llega un tick de un cubo nuevo (o pasa la hora de cierre sin ticks) la vela en curso se
cierra y pasa a la lista: asi la decision al cierre no espera a REST.
"""

from __future__ import annotations

import pandas as pd

from app.backtesting.funding import build_funding_series
from app.replay.candles import bucket_start, timeframe_ms

MAX_CLOSED = 800


class CandleBook:
    """Velas cerradas y vela en curso de una moneda en un marco.

    Args:
        symbol: moneda.
        timeframe: marco (`15m`, `1h`, `4h`, `1d` o `1w`).
    """

    def __init__(self, symbol: str, timeframe: str) -> None:
        self.symbol = symbol
        self.timeframe = timeframe
        self.step = timeframe_ms(timeframe)
        self.closed: list[dict] = []
        self.forming: dict | None = None
        self.funding_events: list[tuple[int, float]] = []

    def load(self, raw_bars: list[dict], now_ms: int) -> int:
        """Reemplaza las velas cerradas con las de REST (descarta la que no cerro).

        Si ya habia velas cerradas armadas con ticks, las de REST mandan donde coinciden y se
        conservan las locales mas nuevas que la ultima de REST.

        Args:
            raw_bars: velas crudas de Bitunix (`time`, `open`, `high`, `low`, `close`,
                `quoteVol`).
            now_ms: instante actual, ms Unix.

        Returns:
            Cuantas velas cerradas quedaron.
        """
        rows = {}
        for r in raw_bars:
            try:
                t = int(r["time"])
                o, h, lo, c = (float(r[k]) for k in ("open", "high", "low", "close"))
            except (KeyError, TypeError, ValueError):
                continue
            if t + self.step > now_ms or min(o, h, lo, c) <= 0:
                continue
            rows[t] = {"open_time": t, "open": o, "high": max(h, o, c), "low": min(lo, o, c),
                       "close": c, "volume": float(r.get("quoteVol") or 0.0)}
        if rows:
            newest = max(rows)
            for bar in self.closed:
                if bar["open_time"] > newest:
                    rows[bar["open_time"]] = bar
        else:
            rows = {b["open_time"]: b for b in self.closed}
        self.closed = [rows[t] for t in sorted(rows)][-MAX_CLOSED:]
        return len(self.closed)

    def _close_forming(self) -> None:
        if self.forming is None:
            return
        if not self.closed or self.forming["open_time"] > self.closed[-1]["open_time"]:
            self.closed.append(self.forming)
            del self.closed[:-MAX_CLOSED]
        self.forming = None

    def roll(self, now_ms: int) -> bool:
        """Cierra la vela en curso si ya paso su hora de cierre (aunque no haya ticks nuevos).

        Args:
            now_ms: instante actual, ms Unix.

        Returns:
            `True` si se cerro una vela.
        """
        if self.forming is not None and now_ms >= self.forming["open_time"] + self.step:
            self._close_forming()
            return True
        return False

    def on_tick(self, price: float, qty: float, ts_ms: int) -> bool:
        """Suma un tick a la vela en curso (o abre una nueva).

        Args:
            price: precio del tick.
            qty: cantidad en la moneda base.
            ts_ms: hora del tick, ms Unix.

        Returns:
            `True` si este tick cerro la vela anterior.
        """
        bucket = bucket_start(ts_ms, self.timeframe)
        closed_now = False
        if self.forming is not None and bucket > self.forming["open_time"]:
            self._close_forming()
            closed_now = True
        if self.forming is None:
            if self.closed and bucket <= self.closed[-1]["open_time"]:
                return closed_now  # tick atrasado de una vela ya cerrada
            prev = self.closed[-1]["close"] if self.closed else price
            self.forming = {"open_time": bucket, "open": prev, "high": max(prev, price),
                            "low": min(prev, price), "close": price, "volume": price * qty}
            return closed_now
        if bucket < self.forming["open_time"]:
            return closed_now
        f = self.forming
        f["high"] = max(f["high"], price)
        f["low"] = min(f["low"], price)
        f["close"] = price
        f["volume"] += price * qty
        return closed_now

    def current_bucket(self, now_ms: int) -> int:
        """La apertura de la vela que contiene `now_ms`.

        Args:
            now_ms: instante, ms Unix.

        Returns:
            La apertura del cubo, ms Unix.
        """
        return bucket_start(now_ms, self.timeframe)

    def frame(self, include_forming: bool) -> pd.DataFrame:
        """Las velas como DataFrame para calcular indicadores.

        Args:
            include_forming: agrega la vela en curso como ultima fila.

        Returns:
            Un DataFrame con `open`, `high`, `low`, `close`, `funding_rate`,
            `funding_is_approximated`, `volume` y `open_time` (el mismo formato del backtest).
        """
        bars = list(self.closed)
        if include_forming and self.forming is not None:
            bars.append(self.forming)
        times = [b["open_time"] for b in bars]
        rates, approx = build_funding_series(times, self.funding_events)
        return pd.DataFrame({
            "open": [b["open"] for b in bars], "high": [b["high"] for b in bars],
            "low": [b["low"] for b in bars], "close": [b["close"] for b in bars],
            "funding_rate": rates, "funding_is_approximated": approx,
            "volume": [b["volume"] for b in bars], "open_time": times,
        })
