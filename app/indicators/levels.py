"""Niveles Camarilla del dia UTC anterior (inspirados en el modulo de niveles de Frost).

Inspirado en indicadores publicos de TradingView (autoria y licencia por verificar). La
especificacion exacta esta en `docs/v2/ESPECIFICACION_INDICADORES.md` (seccion E).

Cada vela del dia UTC `D` recibe los niveles calculados con el maximo, el minimo y el cierre del
dia UTC `D - 1` completo, agregado desde las propias velas de la serie. Es causal: todas las velas
de `D - 1` cierran antes de que empiece `D`.

No esta conectado a ninguna estrategia (Fase 0 de la v2).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from app.indicators._pine import require_columns, require_positive

DAY_MS = 86_400_000

# Fraccion del rango del dia anterior que suma (R) o resta (S) cada nivel al cierre: 1.1 / k.
CAMARILLA_DIVISORS = {"r4": 2, "r3": 4, "r2": 6, "r1": 12, "s1": 12, "s2": 6, "s3": 4, "s4": 2}

CAMARILLA_COLUMNS = (
    "cam_prev_high", "cam_prev_low", "cam_prev_close",
    "cam_r4", "cam_r3", "cam_r2", "cam_r1", "cam_s1", "cam_s2", "cam_s3", "cam_s4",
)


def camarilla_from_hlc(
    high: float | pd.Series, low: float | pd.Series, close: float | pd.Series
) -> dict[str, float | pd.Series]:
    """Los ocho niveles Camarilla a partir del maximo, el minimo y el cierre de un dia.

    `R_k = C + (H - L) * 1.1 / d_k` y `S_k = C - (H - L) * 1.1 / d_k`, con `d` = 12, 6, 4, 2 para
    k = 1..4.

    Args:
        high: maximo del dia (o una serie de maximos).
        low: minimo del dia (o una serie de minimos).
        close: cierre del dia (o una serie de cierres).

    Returns:
        Diccionario `r4, r3, r2, r1, s1, s2, s3, s4` -> precio (o serie de precios).
    """
    span = (high - low) * 1.1
    return {
        name: close + span / div if name.startswith("r") else close - span / div
        for name, div in CAMARILLA_DIVISORS.items()
    }


def camarilla_levels(
    df: pd.DataFrame, interval_ms: int | None = None, require_complete: bool = True
) -> pd.DataFrame:
    """Niveles Camarilla del dia UTC anterior para cada vela.

    Args:
        df: velas con `open_time` (milisegundos UTC de apertura, estrictamente creciente),
            `high`, `low` y `close`.
        interval_ms: duracion de una vela en milisegundos; si es `None` se toma la menor
            diferencia positiva entre aperturas consecutivas. Debe dividir exactamente un dia.
        require_complete: si es `True`, un dia anterior con velas faltantes no da niveles (NaN);
            si es `False`, se usan las velas que haya.

    Returns:
        DataFrame con el mismo indice y las columnas `cam_prev_high`, `cam_prev_low`,
        `cam_prev_close` y `cam_r4` ... `cam_s4`. NaN cuando el dia anterior no esta en la serie
        (o esta incompleto con `require_complete`).

    Raises:
        ValueError: si falta una columna, `open_time` no es estrictamente creciente o la
            duracion de la vela no divide un dia.
    """
    require_columns(df, ("open_time", "high", "low", "close"))
    out = pd.DataFrame(np.nan, index=df.index, columns=list(CAMARILLA_COLUMNS))
    if df.empty:
        return out
    open_time = df["open_time"].to_numpy(dtype="int64")
    diffs = np.diff(open_time)
    if (diffs <= 0).any():
        raise ValueError("open_time debe ser estrictamente creciente")
    if interval_ms is None:
        interval_ms = int(diffs.min()) if diffs.size else DAY_MS
    if interval_ms <= 0 or interval_ms > DAY_MS or DAY_MS % interval_ms != 0:
        raise ValueError(f"La duracion de la vela ({interval_ms} ms) debe dividir un dia")
    expected = DAY_MS // interval_ms

    day = open_time // DAY_MS
    frame = pd.DataFrame({
        "day": day,
        "high": df["high"].to_numpy(dtype=float),
        "low": df["low"].to_numpy(dtype=float),
        "close": df["close"].to_numpy(dtype=float),
    })
    daily = frame.groupby("day").agg(
        high=("high", "max"), low=("low", "min"), close=("close", "last"),
        count=("close", "count"),
    )
    if require_complete:
        daily = daily[daily["count"] == expected]
    prev = daily.reindex(day - 1)
    out["cam_prev_high"] = prev["high"].to_numpy()
    out["cam_prev_low"] = prev["low"].to_numpy()
    out["cam_prev_close"] = prev["close"].to_numpy()
    levels = camarilla_from_hlc(
        out["cam_prev_high"], out["cam_prev_low"], out["cam_prev_close"]
    )
    for name, values in levels.items():
        out[f"cam_{name}"] = values
    return out


# --- Fibonacci de Frost -----------------------------------------------------------------------

# Velas de la ventana del zigzag de Frost (fija en su codigo).
FROST_FIB_PERIOD = 23
# Niveles que dibuja Frost: retrocesos (0,382 / 0,5 / 0,618 / 0,812; el «81,2 %» es suyo, no el
# 78,6 % clasico) y objetivos (1, 1,25 y 1,5). El 0 es el ultimo pivote fijo (pedido del dueno).
FROST_FIB_RATIOS = (0.0, 0.382, 0.5, 0.618, 0.812, 1.0, 1.25, 1.5)
FIB_LEVEL_COLUMNS = tuple(f"fib_{round(r * 1000):03d}" for r in FROST_FIB_RATIOS)


def frost_fibonacci(df: pd.DataFrame, period: int = FROST_FIB_PERIOD) -> pd.DataFrame:
    """Zigzag y niveles Fibonacci de Frost, vela a vela y causales.

    Un maximo de zigzag es una vela cuyo `high` es el mayor de las ultimas `period` velas
    (incluida ella; un empate cuenta) y un minimo, el espejo con `low`. La direccion pasa a 1 con
    un maximo sin minimo en la misma vela y a -1 con un minimo sin maximo. En cada vela con maximo
    o minimo: si la direccion cambio respecto de la vela anterior se agrega un pivote nuevo; si no,
    el pivote en curso se corre solo si es mas extremo. Los niveles usan los dos pivotes ya fijos
    anteriores al pivote en curso: `v1` (el ultimo fijo) y `v2` (el anterior), con
    `nivel(r) = v1 + (v2 - v1) * r`, como Frost.

    Args:
        df: velas con `high` y `low`.
        period: ventana del zigzag (>= 1; Frost usa 23).

    Returns:
        DataFrame con el indice de `df`: `fib_v1`, `fib_v2` (precios), `fib_v1_pos`, `fib_v2_pos`
        (posiciones 0..n-1 de esas velas), `fib_leg` (+1 si `v1 > v2`, es decir la ultima pierna
        fija fue de subida y termino en un maximo; -1 al reves) y una columna por nivel de
        `FIB_LEVEL_COLUMNS` (`fib_000`, `fib_382`, ..., `fib_1500`). NaN (y `fib_leg` 0) hasta
        tener tres pivotes.

    Raises:
        ValueError: si faltan columnas o `period` es menor que 1.
    """
    require_columns(df, ("high", "low"))
    require_positive(period=period)
    high = df["high"].to_numpy(dtype=float)
    low = df["low"].to_numpy(dtype=float)
    n = len(df)
    hh = pd.Series(high).rolling(period, min_periods=period).max().to_numpy()
    ll = pd.Series(low).rolling(period, min_periods=period).min().to_numpy()
    v1 = np.full(n, np.nan)
    v2 = np.full(n, np.nan)
    p1 = np.full(n, -1, dtype=np.int64)
    p2 = np.full(n, -1, dtype=np.int64)
    zigzag: list[tuple[float, int]] = []  # el mas reciente primero (como el arreglo de Frost)
    direction = prev_direction = 0
    for i in range(n):
        is_ph = not np.isnan(hh[i]) and high[i] >= hh[i]
        is_pl = not np.isnan(ll[i]) and low[i] <= ll[i]
        if is_pl and not is_ph:
            direction = -1
        if is_ph and not is_pl:
            direction = 1
        if is_ph or is_pl:
            value = high[i] if direction == 1 else low[i]
            if direction != prev_direction or not zigzag:
                zigzag.insert(0, (value, i))
                del zigzag[5:]
            elif value * direction > zigzag[0][0] * direction:  # mas extremo en su direccion
                zigzag[0] = (value, i)
        prev_direction = direction
        if len(zigzag) >= 3:
            (v1[i], p1[i]), (v2[i], p2[i]) = zigzag[1], zigzag[2]
    out = pd.DataFrame({"fib_v1": v1, "fib_v2": v2, "fib_v1_pos": p1, "fib_v2_pos": p2,
                        "fib_leg": np.sign(np.nan_to_num(v1 - v2)).astype(int)}, index=df.index)
    for ratio, col in zip(FROST_FIB_RATIOS, FIB_LEVEL_COLUMNS, strict=True):
        out[col] = v1 + (v2 - v1) * ratio
    return out


def fib_bounce_signals(df: pd.DataFrame, fib: pd.DataFrame,
                       ratio: float) -> tuple[np.ndarray, np.ndarray]:
    """Rebotes en un nivel Fibonacci de Frost a favor de la ultima pierna fija.

    Se usan los niveles de la vela anterior (los que ya estaban dibujados cuando abrio la vela):

    - Largo en la vela `i`: la ultima pierna fue de subida (`v1 > v2`), la vela toca el nivel con
      su minimo (`low <= nivel`) y cierra sobre el (`close > nivel`), y desde el pivote `v1` hasta
      esta vela ningun minimo perforo `v2` (el 100 %: si lo perforo, la pierna ya no es un
      retroceso).
    - Corto: espejo (pierna de bajada, `high >= nivel`, `close < nivel`, ningun maximo sobre `v2`).

    Con el nivel 0 (`v1`) la regla es la misma: en una pierna de subida, largo si la vela pasa el
    maximo `v1` y cierra sobre el.

    Args:
        df: velas con `high`, `low` y `close` (mismo orden que `fib`).
        fib: salida de `frost_fibonacci`.
        ratio: uno de `FROST_FIB_RATIOS`.

    Returns:
        `(largo, corto)`: arrays booleanos; sin tres pivotes no hay senal.

    Raises:
        ValueError: si `ratio` no es uno de `FROST_FIB_RATIOS`.
    """
    if ratio not in FROST_FIB_RATIOS:
        raise ValueError(f"nivel Fibonacci desconocido: {ratio}")
    col = FIB_LEVEL_COLUMNS[FROST_FIB_RATIOS.index(ratio)]
    high = df["high"].to_numpy(dtype=float)
    low = df["low"].to_numpy(dtype=float)
    close = df["close"].to_numpy(dtype=float)
    level = fib[col].to_numpy(dtype=float)
    leg = fib["fib_leg"].to_numpy()
    v2 = fib["fib_v2"].to_numpy(dtype=float)
    p1 = fib["fib_v1_pos"].to_numpy()
    n = len(df)
    long = np.zeros(n, dtype=bool)
    short = np.zeros(n, dtype=bool)
    pivot = -1
    lowest = highest = np.nan
    for i in range(1, n):
        j = i - 1  # niveles de la vela anterior
        if leg[j] == 0:
            continue
        if p1[j] != pivot:  # pierna nueva: extremos desde su pivote hasta la vela anterior
            pivot = int(p1[j])
            lowest, highest = low[pivot:i].min(), high[pivot:i].max()
        lowest, highest = min(lowest, low[i]), max(highest, high[i])
        if leg[j] == 1:
            long[i] = low[i] <= level[j] < close[i] and lowest >= v2[j]
        else:
            short[i] = high[i] >= level[j] > close[i] and highest <= v2[j]
    return long, short


def fib_break_signals(df: pd.DataFrame, fib: pd.DataFrame,
                      ratio: float) -> tuple[np.ndarray, np.ndarray]:
    """Rupturas de un nivel Fibonacci de Frost: el cierre cruza el nivel.

    Se usa el nivel de la vela anterior (el que estaba dibujado cuando abrio la vela), igual para
    las dos velas que se comparan:

    - Largo en la vela `i`: `close[i-1] <= nivel < close[i]` (cierra por encima viniendo de abajo).
    - Corto: `close[i-1] >= nivel > close[i]` (cierra por debajo viniendo de arriba).

    No mira la direccion de la ultima pierna ni si se rompio el 100 %: es la ruptura del nivel en
    cualquiera de los dos sentidos. Un pinchazo que no cierra del otro lado no es ruptura.

    Args:
        df: velas con `close` (mismo orden que `fib`).
        fib: salida de `frost_fibonacci`.
        ratio: uno de `FROST_FIB_RATIOS`.

    Returns:
        `(largo, corto)`: arrays booleanos; sin tres pivotes no hay senal.

    Raises:
        ValueError: si `ratio` no es uno de `FROST_FIB_RATIOS`.
    """
    if ratio not in FROST_FIB_RATIOS:
        raise ValueError(f"nivel Fibonacci desconocido: {ratio}")
    level = fib[FIB_LEVEL_COLUMNS[FROST_FIB_RATIOS.index(ratio)]].to_numpy(dtype=float)
    close = df["close"].to_numpy(dtype=float)
    n = len(df)
    long = np.zeros(n, dtype=bool)
    short = np.zeros(n, dtype=bool)
    prev_level, prev_close, cur = level[:-1], close[:-1], close[1:]  # vela anterior a cada i >= 1
    with np.errstate(invalid="ignore"):
        long[1:] = (prev_close <= prev_level) & (cur > prev_level)
        short[1:] = (prev_close >= prev_level) & (cur < prev_level)
    return long, short
