"""Estrategias de ejemplo para la repeticion minuto a minuto (plantillas, no candidatas).

- `DonchianStopEntry`: al cierre de cada vela deja ordenes de stop en el maximo y el minimo de las
  ultimas N velas; entra en el minuto en que el precio cruza, sin esperar el cierre. El nivel se
  conoce antes de que empiece la vela.
- `FormingCandleBreakout`: decide en cada minuto mirando la vela en curso (el cierre parcial
  supera el maximo de las N velas anteriores) y entra a mercado en el minuto siguiente.

Ninguna paso por un preregistro: sirven para probar el motor y como modelo para escribir otras.
"""

from __future__ import annotations

import pandas as pd

from app.indicators.engine import atr
from app.persistence.models import Side
from app.replay.engine import EntryKind, EntryOrder, MarketView, ReplayStrategy


class DonchianStopEntry(ReplayStrategy):
    """Ruptura del canal de N velas con ordenes de stop (OCO simple: la primera que se llena)."""

    uses_minutes = True  # para revisar la orden contraria mientras la vela avanza

    def __init__(self, timeframe: str = "1h", period: int = 20, atr_mult: float = 2.0) -> None:
        self.timeframe = timeframe
        self.period = period
        self.atr_mult = atr_mult
        self.lookback = max(60, 3 * period)
        self._levels: tuple[float, float] | None = None

    def precompute(self, df: pd.DataFrame) -> pd.DataFrame:
        """Canal de N velas y ATR(14).

        Args:
            df: velas cerradas.

        Returns:
            Las velas con `upper`, `lower` y `atr`.
        """
        df = df.copy()
        df["upper"] = df["high"].rolling(self.period).max()
        df["lower"] = df["low"].rolling(self.period).min()
        df["atr"] = atr(df, 14)
        return df

    def on_bar_close(self, view: MarketView) -> EntryOrder | None:
        """Fija los niveles del canal y deja el stop del lado mas cercano.

        Args:
            view: lo que se sabe al cierre.

        Returns:
            La orden de stop, o `None` sin indicadores.
        """
        last = view.closed.iloc[-1]
        if pd.isna(last["upper"]) or pd.isna(last["atr"]):
            self._levels = None
            return None
        self._levels = (float(last["upper"]), float(last["lower"]))
        # Deja primero el lado mas cercano al precio; el otro se coloca desde `on_minute`.
        up, lo = self._levels
        if up - view.price <= view.price - lo:
            return EntryOrder(Side.LONG, EntryKind.STOP, up)
        return EntryOrder(Side.SHORT, EntryKind.STOP, lo)

    def on_minute(self, view: MarketView) -> EntryOrder | None:
        """Mueve la orden de stop al lado del canal mas cercano al precio.

        Args:
            view: lo que se sabe al terminar el minuto.

        Returns:
            La orden de stop, o `None`.
        """
        if self._levels is None:
            return None
        up, lo = self._levels
        if view.price >= up:
            return None  # ya se cruzo: la orden de stop se encarga (o vencio)
        if up - view.price <= view.price - lo:
            return EntryOrder(Side.LONG, EntryKind.STOP, up)
        return EntryOrder(Side.SHORT, EntryKind.STOP, lo)

    def stop_price(self, view: MarketView, side: Side, entry_price: float) -> float | None:
        """SL a `atr_mult` ATR de la entrada.

        Args:
            view: lo que se sabia al decidir.
            side: direccion.
            entry_price: precio de llenado.

        Returns:
            El precio del SL.
        """
        a = float(view.closed["atr"].iloc[-1])
        d = self.atr_mult * a
        return entry_price - d if side == Side.LONG else entry_price + d

    def trailing_distance(self, view: MarketView) -> float | None:
        """Trailing a `atr_mult` ATR.

        Args:
            view: lo que se sabia al decidir.

        Returns:
            La distancia en precio.
        """
        return self.atr_mult * float(view.closed["atr"].iloc[-1])


class FormingCandleBreakout(ReplayStrategy):
    """Entra cuando el precio de la vela en curso supera el maximo de las N velas cerradas."""

    uses_minutes = True

    def __init__(self, timeframe: str = "1h", period: int = 20, atr_mult: float = 2.0) -> None:
        self.timeframe = timeframe
        self.period = period
        self.atr_mult = atr_mult
        self.lookback = max(60, 3 * period)

    def precompute(self, df: pd.DataFrame) -> pd.DataFrame:
        """Maximo de N velas y ATR(14).

        Args:
            df: velas cerradas.

        Returns:
            Las velas con `upper` y `atr`.
        """
        df = df.copy()
        df["upper"] = df["high"].rolling(self.period).max()
        df["atr"] = atr(df, 14)
        return df

    def on_minute(self, view: MarketView) -> EntryOrder | None:
        """Largo a mercado si el precio de la vela en curso supera el maximo previo.

        Args:
            view: lo que se sabe al terminar el minuto.

        Returns:
            La orden a mercado, o `None`.
        """
        last = view.closed.iloc[-1]
        if pd.isna(last["upper"]) or pd.isna(last["atr"]):
            return None
        return EntryOrder(Side.LONG) if view.price > float(last["upper"]) else None

    def stop_price(self, view: MarketView, side: Side, entry_price: float) -> float | None:
        """SL a `atr_mult` ATR de la entrada.

        Args:
            view: lo que se sabia al decidir.
            side: direccion.
            entry_price: precio de llenado.

        Returns:
            El precio del SL.
        """
        return entry_price - self.atr_mult * float(view.closed["atr"].iloc[-1])

    def take_profit_price(self, view: MarketView, side: Side, entry_price: float) -> float | None:
        """TP al doble de la distancia del SL.

        Args:
            view: lo que se sabia al decidir.
            side: direccion.
            entry_price: precio de llenado.

        Returns:
            El precio del TP.
        """
        return entry_price + 2 * self.atr_mult * float(view.closed["atr"].iloc[-1])
