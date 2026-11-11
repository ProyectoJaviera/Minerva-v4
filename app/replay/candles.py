"""Velas del marco de la estrategia armadas con minutos: las cerradas y la que esta en curso.

Una vela del marco `tf` agrupa los minutos cuyo `open_time` cae en el mismo cubo. Los cubos de
1m a 1d se alinean con la epoca Unix (00:00 UTC), como los de Bitunix. Los de `1w` empiezan el
lunes 00:00 UTC (verificado contra la API publica el 2026-11-11, ver PROGRESS de la v2), que es la
epoca mas 4 dias. Si faltan minutos dentro de un cubo la vela se arma con los que hay y se
cuentan los ausentes (`missing`), para que el motor pueda avisar.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.replay.minute_store import MINUTE_MS, MinuteBar

TIMEFRAME_MS: dict[str, int] = {
    "1m": MINUTE_MS,
    "5m": 5 * MINUTE_MS,
    "15m": 15 * MINUTE_MS,
    "30m": 30 * MINUTE_MS,
    "1h": 60 * MINUTE_MS,
    "2h": 120 * MINUTE_MS,
    "4h": 240 * MINUTE_MS,
    "6h": 360 * MINUTE_MS,
    "12h": 720 * MINUTE_MS,
    "1d": 1440 * MINUTE_MS,
    "1w": 7 * 1440 * MINUTE_MS,
}
# El lunes 1970-01-05 00:00 UTC: los cubos semanales se cuentan desde ahi.
WEEK_OFFSET_MS = 4 * 1440 * MINUTE_MS


def timeframe_ms(tf: str) -> int:
    """Duracion de un marco en ms.

    Args:
        tf: marco (`1m` ... `1d`, `1w`).

    Returns:
        La duracion en ms.

    Raises:
        ValueError: si el marco no esta en `TIMEFRAME_MS`.
    """
    try:
        return TIMEFRAME_MS[tf]
    except KeyError:
        raise ValueError(f"marco desconocido para la repeticion: {tf}") from None


def bucket_start(open_time_ms: int, tf: str) -> int:
    """El `open_time` de la vela del marco `tf` que contiene ese minuto.

    Args:
        open_time_ms: apertura del minuto, ms Unix.
        tf: marco (`1m` ... `1d`, `1w`).

    Returns:
        La apertura del cubo, ms Unix.
    """
    step = timeframe_ms(tf)
    offset = WEEK_OFFSET_MS if tf == "1w" else 0
    return (open_time_ms - offset) // step * step + offset


@dataclass(slots=True)
class Candle:
    """Una vela del marco de la estrategia (cerrada o en curso).

    Attributes:
        open_time: apertura del cubo, ms Unix.
        open, high, low, close: precios con los minutos vistos hasta ahora.
        volume: volumen en USDT acumulado.
        minutes: cuantos minutos entraron.
        missing: cuantos minutos del cubo faltaron (solo se sabe al cerrarla).
    """
    open_time: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    minutes: int = 1
    missing: int = 0

    @classmethod
    def start(cls, bucket: int, bar: MinuteBar) -> Candle:
        """Abre una vela con su primer minuto.

        Args:
            bucket: apertura del cubo, ms Unix.
            bar: el primer minuto.

        Returns:
            La vela nueva.
        """
        return cls(bucket, bar.open, bar.high, bar.low, bar.close, bar.volume)

    def add(self, bar: MinuteBar) -> None:
        """Suma un minuto a la vela.

        Args:
            bar: el minuto siguiente del mismo cubo.
        """
        self.high = max(self.high, bar.high)
        self.low = min(self.low, bar.low)
        self.close = bar.close
        self.volume += bar.volume
        self.minutes += 1


def aggregate(minutes: list[MinuteBar], tf: str) -> list[Candle]:
    """Arma todas las velas del marco `tf` con una lista ordenada de minutos.

    La ultima vela puede estar incompleta (se devuelve igual, con sus minutos contados); quien
    la use decide si esta cerrada comparando con el instante actual.

    Args:
        minutes: minutos ordenados por `open_time`.
        tf: marco.

    Returns:
        Las velas en orden, con `missing` = minutos del cubo que no estaban.
    """
    step_minutes = timeframe_ms(tf) // MINUTE_MS
    out: list[Candle] = []
    current: Candle | None = None
    for bar in minutes:
        b = bucket_start(bar.open_time, tf)
        if current is None or b != current.open_time:
            if current is not None:
                current.missing = max(0, step_minutes - current.minutes)
            current = Candle.start(b, bar)
            out.append(current)
        else:
            current.add(bar)
    if current is not None:
        current.missing = max(0, step_minutes - current.minutes)
    return out
