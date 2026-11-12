"""El gemelo de control al azar de una prueba en vivo (v4).

Cada prueba en vivo tiene un gemelo que opera en paper **las mismas monedas, el mismo marco, el
mismo margen, apalancamiento, direccion, TP parciales, breakeven, tope de posiciones y la misma
salida**, pero entra **al azar**: en cada vela en la que podria entrar, entra con una probabilidad
igual a la frecuencia con la que la prueba real dio senales hasta ese momento, y con la direccion
sorteada (50/50, o la unica permitida si la prueba solo opera largos o cortos). Asi, si la prueba
no le gana a su gemelo, sus resultados no se distinguen de la suerte.

- **Frecuencia:** `senales / velas elegibles` de la prueba real, acumulada desde que se creo. Una
  vela es elegible para una moneda si al cerrar la anterior la prueba no tenia posicion ni entrada
  pendiente en ella y la moneda tenia ticks recientes. El sorteo del gemelo usa la frecuencia
  conocida ANTES de la vela (sin mirar adelante); mientras la prueba no da ninguna senal, el gemelo
  no entra.
- **Momento:** en modo `close`, al cierre de la vela (como la prueba). En modo `intrabar`, en un
  instante al azar dentro de la vela, con la vela todavia abierta.
- **Salida:** la misma cuenta de la prueba (`ConfluenceStrategy`): SL a k ATR(14) o al % pedido,
  TP en R o en %, trailing. Aqui se calcula solo el ATR, sin los indicadores.

Todo lo demas (llenado en el tick siguiente, comisiones, deslizamiento, funding, liquidacion, tope
de riesgo) es el mismo codigo de la prueba. No se ajusta nada de la estrategia: el gemelo solo mide.
"""

from __future__ import annotations

import math
import random
from dataclasses import replace

import numpy as np
import pandas as pd

from app.candidates.common import BAR_INDEX_COLUMN
from app.explorer.confluence import ConfluenceStrategy
from app.indicators._pine import pine_atr
from app.live.config import TestConfig
from app.persistence.models import Side

CONTROL_PREFIX = "Azar: "
# Por debajo de estas operaciones en cada lado no se emite un veredicto.
MIN_TRADES_FOR_VERDICT = 30
# Una cola de una t de Welch al 5 % (aproximacion normal; con 30 o mas operaciones por lado).
T_CRITICAL = 1.645
MS_BY_TIMEFRAME = {"15m": 900_000, "1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000,
                   "1w": 604_800_000}


def control_name(name: str) -> str:
    """El nombre del gemelo de una prueba (como mucho 60 caracteres).

    Args:
        name: nombre de la prueba.

    Returns:
        `Azar: <nombre>`, recortado a 60 caracteres.
    """
    return (CONTROL_PREFIX + name)[:60]


def control_config(cfg: TestConfig) -> TestConfig:
    """La configuracion del gemelo: identica a la de la prueba salvo el nombre.

    Args:
        cfg: la prueba.

    Returns:
        La configuracion del gemelo.
    """
    return replace(cfg, name=control_name(cfg.name))


def signal_rate(eligible: int, signals: int) -> float:
    """La frecuencia de senales de la prueba por vela elegible.

    Args:
        eligible: velas elegibles contadas.
        signals: senales de la prueba.

    Returns:
        Un numero entre 0 y 1 (0 sin velas contadas).
    """
    if eligible <= 0 or signals <= 0:
        return 0.0
    return min(1.0, signals / eligible)


def draw_entry(rng: random.Random, rate: float, direction: str) -> Side | None:
    """El sorteo del gemelo para una vela: entrar o no, y en que direccion.

    Args:
        rng: generador de numeros al azar.
        rate: probabilidad de entrar (la frecuencia de la prueba).
        direction: `both`, `long` o `short` (la de la prueba).

    Returns:
        La direccion de la entrada, o `None` si no entra.
    """
    if rate <= 0 or rng.random() >= rate:
        return None
    if direction == "long":
        return Side.LONG
    if direction == "short":
        return Side.SHORT
    return Side.LONG if rng.random() < 0.5 else Side.SHORT


def entry_delay_ms(rng: random.Random, cfg: TestConfig) -> int:
    """Cuanto espera el gemelo dentro de la vela antes de entrar.

    Args:
        rng: generador de numeros al azar.
        cfg: la prueba.

    Returns:
        0 en modo `close`; en modo `intrabar`, un instante al azar dentro de la vela.
    """
    if cfg.mode != "intrabar":
        return 0
    return int(rng.uniform(0, MS_BY_TIMEFRAME[cfg.timeframe]))


class RandomExitStrategy(ConfluenceStrategy):
    """La salida de una prueba (SL, TP y trailing) sin calcular sus indicadores.

    Usa los metodos de salida de `ConfluenceStrategy` (la misma cuenta que la prueba) con el
    ATR(14) de Pine, que es el mismo `x_atr14` de la confluencia.

    Args:
        cfg: la prueba.
        frame: velas hasta la de la entrada (se calcula su ATR de una vez).
    """

    def __init__(self, cfg: TestConfig, frame: pd.DataFrame) -> None:
        super().__init__(cfg.indicators, cfg.timeframe, window=cfg.window,
                         exit_name=cfg.exit_name, min_agree=cfg.min_agree,
                         pct_exit=cfg.pct_exit)
        self.precompute(frame)

    def precompute(self, df: pd.DataFrame) -> pd.DataFrame:
        """Calcula solo el ATR(14) y el cierre de la serie.

        Args:
            df: velas hasta la de la entrada.

        Returns:
            Copia de `df` con el indice de vela y `x_atr14`.
        """
        out = df.copy()
        out[BAR_INDEX_COLUMN] = np.arange(len(out), dtype="int64")
        out["x_atr14"] = pine_atr(out, 14).to_numpy()
        n = len(out)
        self._long = np.zeros(n, dtype=bool)
        self._short = np.zeros(n, dtype=bool)
        self._atr = out["x_atr14"].to_numpy(dtype=float)
        self._open_time = out["open_time"].to_numpy(dtype="int64")
        self._close = out["close"].to_numpy(dtype=float)
        return out


def _mean_var(values: list[float]) -> tuple[float, float]:
    n = len(values)
    mean = sum(values) / n
    var = sum((v - mean) ** 2 for v in values) / (n - 1) if n > 1 else 0.0
    return mean, var


def compare(strategy_pnls: list[float], control_pnls: list[float],
            min_trades: int = MIN_TRADES_FOR_VERDICT) -> dict:
    """Compara el resultado por operacion de la prueba con el de su gemelo al azar.

    Prueba de Welch de una cola sobre el PnL neto medio por operacion (los dos lados usan el mismo
    margen y apalancamiento, asi que los USDT son comparables).

    Args:
        strategy_pnls: PnL neto de cada operacion cerrada de la prueba.
        control_pnls: PnL neto de cada operacion cerrada del gemelo.
        min_trades: operaciones minimas por lado para emitir un veredicto.

    Returns:
        `verdict` (`pocas_operaciones`, `le_gana`, `pierde` o `no_se_distingue`),
        `strategy_mean`, `control_mean`, `diff` (prueba menos gemelo, por operacion), `t`
        (`None` si no se puede calcular) y `min_trades`.
    """
    out = {"verdict": "pocas_operaciones", "strategy_mean": None, "control_mean": None,
           "diff": None, "t": None, "min_trades": min_trades,
           "strategy_trades": len(strategy_pnls), "control_trades": len(control_pnls)}
    if strategy_pnls:
        out["strategy_mean"] = sum(strategy_pnls) / len(strategy_pnls)
    if control_pnls:
        out["control_mean"] = sum(control_pnls) / len(control_pnls)
    if out["strategy_mean"] is not None and out["control_mean"] is not None:
        out["diff"] = out["strategy_mean"] - out["control_mean"]
    if len(strategy_pnls) < 2 or len(control_pnls) < 2:
        return out
    ms, vs = _mean_var(strategy_pnls)
    mc, vc = _mean_var(control_pnls)
    se = math.sqrt(vs / len(strategy_pnls) + vc / len(control_pnls))
    if se > 0:
        out["t"] = (ms - mc) / se
    if len(strategy_pnls) < min_trades or len(control_pnls) < min_trades:
        return out
    t = out["t"]
    if t is None:
        out["verdict"] = "no_se_distingue"
    elif t > T_CRITICAL:
        out["verdict"] = "le_gana"
    elif t < -T_CRITICAL:
        out["verdict"] = "pierde"
    else:
        out["verdict"] = "no_se_distingue"
    return out
