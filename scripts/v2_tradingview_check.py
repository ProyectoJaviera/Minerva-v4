"""Imprime niveles Camarilla y senales SFP de la v2 para compararlos a mano con TradingView.

Requisito del preregistro de la v2 (`docs/v2/PREREGISTRO_V2.md`): antes de congelarlo, el dueno
compara contra el grafico de TradingView, para BTCUSDT y ETHUSDT:

1. Los niveles Camarilla R3, R4, S3 y S4 de tres dias UTC (por defecto 2026-11-05, 2026-11-06 y
   2026-11-07), calculados con el maximo, el minimo y el cierre del dia UTC anterior agregados
   desde velas de 1h. Tambien imprime la vela diaria de Bitunix de ese dia anterior, para ver si
   el agregado coincide con la vela diaria que usaria TradingView.
2. Las ultimas 10 senales SFP (pivote 5/1) en 1h (o 4h con `--interval 4h`), con la hora UTC de
   la vela de barrida (donde el original pone la etiqueta «SFP») y de la vela de confirmacion (la
   flecha, que es la senal), mas el ATR(14) de la vela de senal (el del colchon del SL).
3. Con `--rf-count N`, los ultimos N giros del Range Filter de Frost (el ▲/▼ de los «AI Tags»
   de Frost), con la EMA 144 y el ATR(14) de esa vela, que es lo que usa `range_filter_flip_ema144`.
   El modo por defecto es Intraday; `--rf-mode scalping` o `--rf-mode swing` usan los otros dos.
4. Con `--ema-cross RAPIDA LENTA`, los ultimos `--ema-count` cruces de esas dos EMAs del cierre
   (▲ = la rapida cruza hacia arriba, largo; ▼ = hacia abajo, corto), con el cierre y las dos
   EMAs de la vela del cruce: es el indicador `emax_<rapida>_<lenta>` de Indicadores y En vivo.
   En TradingView: dos indicadores «EMA» (fuente close) con esos largos; el cruce se ve al
   cierre de la vela listada. Con `--history-bars` alto (2000 por defecto) la EMA 200 ya no
   depende del arranque de la historia.
5. Con `--cloud LONGITUD`, el Cloud de Frost (techo = EMA del maximo, media = EMA del cierre,
   piso = EMA del minimo) de las ultimas 3 velas cerradas y los ultimos `--cloud-count`
   retesteos (rebote en el techo = LARGO, en el piso = CORTO), como `cloud_retest<n>`. En
   TradingView: Frost con «Cloud» encendido y la misma longitud.
6. Con `--fib`, el Fibonacci de Frost (zigzag de 23 velas) de la ultima vela cerrada: los
   pivotes v2 y v1 (hora UTC y precio) y los niveles 0 / 38,2 / 50 / 61,8 / 81,2 / 100 / 125 /
   150 %, mas las ultimas `--fib-count` senales de rebote de cada nivel de entrada (como
   `fib<n>`). En TradingView: Frost con «Show Fibonacci Targets» y «Show Fibonacci Levels».

Con `--end AAAA-MM-DD` todo se calcula como si «ahora» fuera esa fecha a las 00:00 UTC, para
comparar semanas pasadas; con `--skip-camarilla` se omiten los niveles.

Las velas son LAST_PRICE de Bitunix Futures (endpoint publico verificado en
`app/market/bitunix_rest.py`). Para comparar, abrir en TradingView el mismo par perpetuo de
Bitunix y poner el grafico en UTC: con otro exchange los precios difieren un poco.

Es de solo lectura: no lee `.env`, no abre ninguna base y no escribe nada.

    python scripts/v2_tradingview_check.py
    python scripts/v2_tradingview_check.py --symbols BTCUSDT --days 2026-11-06 --sfp-count 5
    python scripts/v2_tradingview_check.py --interval 4h --skip-camarilla --rf-count 10
    python scripts/v2_tradingview_check.py --skip-camarilla --sfp-count 0 --rf-mode swing
    python scripts/v2_tradingview_check.py --skip-camarilla --sfp-count 0 --ema-cross 9 21
    python scripts/v2_tradingview_check.py --skip-camarilla --sfp-count 0 --cloud 50
    python scripts/v2_tradingview_check.py --interval 1d --skip-camarilla --sfp-count 0 --fib
    python scripts/v2_tradingview_check.py --end 2025-06-15 --skip-camarilla --sfp-count 15
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from datetime import UTC, date, datetime
from typing import Any

import pandas as pd

sys.path.insert(0, ".")

from app.config import Settings  # noqa: E402
from app.explorer.confluence import EMA_LENGTHS, ema_cross_signals  # noqa: E402
from app.indicators._pine import pine_atr, pine_ema  # noqa: E402
from app.indicators.levels import (  # noqa: E402
    DAY_MS,
    FIB_LEVEL_COLUMNS,
    FROST_FIB_RATIOS,
    camarilla_levels,
    fib_bounce_signals,
    frost_fibonacci,
)
from app.indicators.structure import sfp_signals  # noqa: E402
from app.indicators.trend_filters import (  # noqa: E402
    RANGE_FILTER_SENSITIVITY,
    cloud_retest_signals,
    frost_cloud,
    range_filter,
)
from app.market.bitunix_rest import BitunixRestClient  # noqa: E402

HOUR_MS = 3_600_000
INTERVAL_MS = {"1h": HOUR_MS, "4h": 4 * HOUR_MS, "1d": 24 * HOUR_MS, "1w": 7 * 24 * HOUR_MS}
# Las semanas se agregan desde las velas de 1d, con inicio el lunes 00:00 UTC (como TradingView);
# no se usa un intervalo semanal de Bitunix porque no esta verificado en el repo. El 1970-01-01
# fue jueves: el primer lunes (1970-01-05) esta 4 dias despues del origen.
WEEK_OFFSET_MS = 4 * 24 * HOUR_MS
PAGE = 200
DEFAULT_SYMBOLS = ("BTCUSDT", "ETHUSDT")
DEFAULT_DAYS = ("2026-11-05", "2026-11-06", "2026-11-07")
# El URL por defecto de la configuracion, sin leer `.env` ni variables de entorno.
BITUNIX_URL = Settings.model_fields["bitunix_rest_base_url"].default


def _utc(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=UTC).strftime("%Y-%m-%d %H:%M")


def frame_from_raw(raw: list[dict[str, Any]], step_ms: int, now_ms: int) -> pd.DataFrame:
    """Velas crudas de Bitunix -> DataFrame ordenado, sin duplicados ni la vela en curso.

    Args:
        raw: diccionarios de `get_kline` (`time`, `open`, `high`, `low`, `close`).
        step_ms: duracion de la vela en ms.
        now_ms: instante actual en ms (descarta la vela que aun no cerro).

    Returns:
        DataFrame con `open_time`, `open`, `high`, `low` y `close`.
    """
    df = pd.DataFrame([{
        "open_time": int(r["time"]), "open": float(r["open"]), "high": float(r["high"]),
        "low": float(r["low"]), "close": float(r["close"]),
    } for r in raw], columns=["open_time", "open", "high", "low", "close"])
    df = df.drop_duplicates("open_time").sort_values("open_time")
    df = df[df["open_time"] + step_ms <= now_ms]
    return df.reset_index(drop=True)


async def fetch_bars(
    client: Any, symbol: str, interval: str, step_ms: int, bars: int, now_ms: int
) -> pd.DataFrame:
    """Descarga hacia atras las ultimas `bars` velas cerradas (paginas de 200).

    Args:
        client: cliente con `get_kline` (un `BitunixRestClient`).
        symbol: simbolo, p. ej. `BTCUSDT`.
        interval: `1h` o `1d`.
        step_ms: duracion de la vela en ms.
        bars: cuantas velas juntar como minimo.
        now_ms: instante actual en ms.

    Returns:
        Las velas cerradas, en orden cronologico.

    Efectos:
        Red: GET publicos a Bitunix (`/api/v1/futures/market/kline`).
    """
    raw: list[dict[str, Any]] = []
    cursor = now_ms
    while len(raw) < bars:
        page = await client.get_kline(symbol=symbol, interval=interval, end_time=cursor,
                                      limit=PAGE, price_type="LAST_PRICE")
        if not page:
            break
        raw.extend(page)
        oldest = min(int(r["time"]) for r in page)
        if oldest >= cursor:
            break
        cursor = oldest
    return frame_from_raw(raw, step_ms, now_ms)


def weekly_from_daily(daily: pd.DataFrame, now_ms: int) -> pd.DataFrame:
    """Agrega velas de 1d en semanas lunes-domingo UTC y descarta la semana en curso.

    Args:
        daily: velas de 1d con `open_time`, `open`, `high`, `low` y `close`.
        now_ms: instante actual en ms (una semana solo cuenta si ya cerro).

    Returns:
        DataFrame semanal con las mismas columnas; `open_time` es el lunes 00:00 UTC.
    """
    week = INTERVAL_MS["1w"]
    start = (daily["open_time"] - WEEK_OFFSET_MS) // week * week + WEEK_OFFSET_MS
    out = daily.assign(open_time=start).groupby("open_time", sort=True).agg(
        open=("open", "first"), high=("high", "max"), low=("low", "min"), close=("close", "last"),
    ).reset_index()
    out = out[out["open_time"] + week <= now_ms]
    return out.reset_index(drop=True)


async def fetch_interval(client: Any, symbol: str, interval: str, bars: int,
                         now_ms: int) -> pd.DataFrame:
    """Velas cerradas de `interval`; las semanales salen de agregar velas de 1d.

    Args:
        client: cliente con `get_kline`.
        symbol: simbolo.
        interval: `1h`, `4h`, `1d` o `1w`.
        bars: velas a juntar como minimo.
        now_ms: instante actual en ms.

    Returns:
        Las velas cerradas, en orden cronologico.

    Efectos:
        Red: GET publicos a Bitunix.
    """
    if interval == "1w":
        daily = await fetch_bars(client, symbol, "1d", INTERVAL_MS["1d"], bars * 7, now_ms)
        return weekly_from_daily(daily, now_ms)
    return await fetch_bars(client, symbol, interval, INTERVAL_MS[interval], bars, now_ms)


def bars_lines(symbol: str, bars: pd.DataFrame, count: int, interval: str) -> list[str]:
    """Lineas con el OHLC de las ultimas `count` velas, para compararlas una a una.

    Args:
        symbol: simbolo.
        bars: velas con `open_time` y OHLC.
        count: cuantas velas mostrar.
        interval: etiqueta de la temporalidad.

    Returns:
        Las lineas de texto.
    """
    lines = [f"Velas {symbol} {interval}: ultimas {count} (hora UTC de apertura)"]
    for row in bars.tail(count).itertuples():
        lines.append(f"  {_utc(int(row.open_time))}  O {row.open:.2f}  H {row.high:.2f}  "
                     f"L {row.low:.2f}  C {row.close:.2f}")
    return lines


def camarilla_lines(symbol: str, hourly: pd.DataFrame, daily: pd.DataFrame,
                    days: list[date]) -> list[str]:
    """Lineas con R3/R4/S3/S4 de cada dia pedido y la vela diaria de Bitunix del dia anterior.

    Args:
        symbol: simbolo.
        hourly: velas de 1h con `open_time`.
        daily: velas de 1d de Bitunix con `open_time`.
        days: dias UTC a informar.

    Returns:
        Las lineas de texto.
    """
    levels = camarilla_levels(hourly, interval_ms=HOUR_MS)
    day_of_bar = hourly["open_time"] // DAY_MS
    lines = [f"Camarilla {symbol} (dia UTC anterior agregado desde 1h)"]
    for d in days:
        day_index = int(datetime(d.year, d.month, d.day, tzinfo=UTC).timestamp() * 1000) // DAY_MS
        rows = levels[day_of_bar == day_index]
        if rows.empty or rows["cam_r3"].isna().all():
            lines.append(f"  {d}: sin datos completos del dia anterior")
            continue
        row = rows.iloc[0]
        prev = daily[daily["open_time"] // DAY_MS == day_index - 1]
        lines.append(
            f"  {d}: R4 {row['cam_r4']:.2f}  R3 {row['cam_r3']:.2f}  "
            f"S3 {row['cam_s3']:.2f}  S4 {row['cam_s4']:.2f}   "
            f"(dia anterior agregado: H {row['cam_prev_high']:.2f} L {row['cam_prev_low']:.2f} "
            f"C {row['cam_prev_close']:.2f})"
        )
        if prev.empty:
            lines.append("      vela diaria de Bitunix del dia anterior: no disponible")
        else:
            p = prev.iloc[0]
            same = (p["high"] == row["cam_prev_high"] and p["low"] == row["cam_prev_low"]
                    and p["close"] == row["cam_prev_close"])
            lines.append(
                f"      vela diaria de Bitunix del dia anterior: H {p['high']:.2f} "
                f"L {p['low']:.2f} C {p['close']:.2f} -> "
                f"{'coincide' if same else 'NO coincide con el agregado'}"
            )
    return lines


def sfp_lines(symbol: str, hourly: pd.DataFrame, count: int, interval: str = "1h") -> list[str]:
    """Lineas con las ultimas `count` senales SFP (5/1) y el ATR(14) de la vela de senal.

    Args:
        symbol: simbolo.
        hourly: velas de `interval` con `open_time`.
        count: cuantas senales mostrar (las mas recientes).
        interval: etiqueta de la temporalidad (`1h` o `4h`), solo para el texto.

    Returns:
        Las lineas de texto.
    """
    sfp = sfp_signals(hourly)
    atr = pine_atr(hourly, 14)
    events = []
    for side, label in (("bear", "BAJISTA (corto)"), ("bull", "ALCISTA (largo)")):
        for i in sfp.index[sfp[f"sfp_{side}_signal"]]:
            sweep_i = i - int(sfp.at[i, f"sfp_{side}_bars_since_sweep"])
            events.append((i, label, sweep_i, sfp.at[i, f"sfp_{side}_swing"],
                           sfp.at[i, f"sfp_{side}_opposite"],
                           sfp.at[i, f"sfp_{side}_sweep_extreme"]))
    events.sort()
    lines = [f"SFP {symbol} {interval}: ultimas {count} senales (horas UTC de apertura de la vela)"]
    if not events:
        lines.append("  ninguna en el historial descargado")
    for i, label, sweep_i, swing, opposite, extreme in events[-count:]:
        lines.append(
            f"  confirmacion {_utc(int(hourly.at[i, 'open_time']))}  {label}  "
            f"barrida {_utc(int(hourly.at[sweep_i, 'open_time']))}  pivote {swing:.2f}  "
            f"extremo opuesto {opposite:.2f}  mecha {extreme:.2f}  ATR14 {atr.iat[i]:.2f}"
        )
    return lines


def rf_lines(symbol: str, bars: pd.DataFrame, count: int, interval: str,
             mode: str = "intraday") -> list[str]:
    """Lineas con los ultimos `count` giros del Range Filter, EMA 144 y ATR(14).

    Usa las mismas funciones que `range_filter_flip_ema144` (`range_filter`, `pine_ema`,
    `pine_atr`). Un giro solo es senal del candidato si el cierre esta del lado de la EMA.

    Args:
        symbol: simbolo.
        bars: velas con `open_time`, `high`, `low` y `close`.
        count: cuantos giros mostrar (los mas recientes).
        interval: etiqueta de la temporalidad, solo para el texto.
        mode: modo de Frost (`intraday`, `scalping` o `swing`; ver `RANGE_FILTER_SENSITIVITY`).

    Returns:
        Las lineas de texto.
    """
    rf = range_filter(bars["close"], slow_mult=RANGE_FILTER_SENSITIVITY[mode])
    ema = pine_ema(bars["close"], 144)
    atr = pine_atr(bars, 14)
    flips = [(i, bool(rf.at[i, "rf_buy"])) for i in rf.index[rf["rf_buy"] | rf["rf_sell"]]]
    lines = [f"Range Filter {symbol} {interval} modo {mode}: ultimos {count} giros "
             "(hora UTC de apertura)"]
    if not flips:
        lines.append("  ninguno en el historial descargado")
    for i, buy in flips[-count:]:
        close, e = float(bars.at[i, "close"]), float(ema.iat[i])
        candidate = (buy and close > e) or (not buy and close < e)
        tag = "-> senal del candidato" if candidate else "(filtrada por EMA)"
        lines.append(
            f"  {_utc(int(bars.at[i, 'open_time']))}  {'COMPRA (▲)' if buy else 'VENTA (▼)'}  "
            f"cierre {close:.2f}  filtro {rf.at[i, 'rf_filter']:.2f}  EMA144 {e:.2f}  "
            f"ATR14 {atr.iat[i]:.2f}  {tag}"
        )
    return lines


def ema_cross_lines(symbol: str, bars: pd.DataFrame, fast: int, slow: int, count: int,
                    interval: str) -> list[str]:
    """Lineas con los ultimos `count` cruces de la EMA `fast` y la EMA `slow` del cierre.

    Usa las mismas funciones que el indicador `emax_<fast>_<slow>` (`pine_ema` y
    `ema_cross_signals`): ▲ es largo (la rapida cruza hacia arriba) y ▼ corto.

    Args:
        symbol: simbolo.
        bars: velas con `open_time` y `close`.
        fast: largo de la EMA rapida.
        slow: largo de la EMA lenta (mayor que `fast`).
        count: cuantos cruces mostrar (los mas recientes).
        interval: etiqueta de la temporalidad, solo para el texto.

    Returns:
        Las lineas de texto.
    """
    ema_f, ema_s = pine_ema(bars["close"], fast), pine_ema(bars["close"], slow)
    up, down = ema_cross_signals(ema_f, ema_s)
    idx = [i for i in range(len(bars)) if up[i] or down[i]]
    lines = [f"Cruces EMA {fast} / EMA {slow} {symbol} {interval}: ultimos {count} "
             "(hora UTC de apertura de la vela del cruce)"]
    if not idx:
        lines.append("  ninguno en el historial descargado")
    for i in idx[-count:]:
        lines.append(
            f"  {_utc(int(bars['open_time'].iat[i]))}  "
            f"{'▲ LARGO (cruza hacia arriba)' if up[i] else '▼ CORTO (cruza hacia abajo)'}  "
            f"cierre {float(bars['close'].iat[i]):.2f}  EMA{fast} {float(ema_f.iat[i]):.2f}  "
            f"EMA{slow} {float(ema_s.iat[i]):.2f}"
        )
    return lines


def cloud_lines(symbol: str, bars: pd.DataFrame, length: int, count: int,
                interval: str) -> list[str]:
    """Lineas con el Cloud de Frost de las ultimas 3 velas y sus ultimos `count` retesteos.

    Usa las mismas funciones que los indicadores `cloud<n>` y `cloud_retest<n>` (`frost_cloud`
    y `cloud_retest_signals`).

    Args:
        symbol: simbolo.
        bars: velas con `open_time`, `open`, `high`, `low` y `close`.
        length: longitud de las tres EMAs.
        count: cuantos retesteos mostrar (los mas recientes).
        interval: etiqueta de la temporalidad, solo para el texto.

    Returns:
        Las lineas de texto.
    """
    cloud = frost_cloud(bars, length)
    long, short = cloud_retest_signals(bars, cloud)

    def levels(i: int) -> str:
        return (f"techo {cloud['cloud_top'].iat[i]:.2f}  media {cloud['cloud_mid'].iat[i]:.2f}  "
                f"piso {cloud['cloud_bottom'].iat[i]:.2f}")

    lines = [f"Cloud de Frost {length} {symbol} {interval}: ultimas 3 velas cerradas "
             "(hora UTC de apertura)"]
    for i in range(max(0, len(bars) - 3), len(bars)):
        lines.append(f"  {_utc(int(bars['open_time'].iat[i]))}  cierre "
                     f"{float(bars['close'].iat[i]):.2f}  {levels(i)}")
    idx = [i for i in range(len(bars)) if long[i] or short[i]]
    lines.append(f"Retesteos del Cloud {length} {symbol} {interval}: ultimos {count} "
                 "(LARGO = rebote en el techo, CORTO = rebote en el piso)")
    if not idx:
        lines.append("  ninguno en el historial descargado")
    for i in idx[-count:]:
        o, h, lo, c = (float(bars[k].iat[i]) for k in ("open", "high", "low", "close"))
        lines.append(f"  {_utc(int(bars['open_time'].iat[i]))}  {'LARGO' if long[i] else 'CORTO'}  "
                     f"O {o:.2f}  H {h:.2f}  L {lo:.2f}  C {c:.2f}  {levels(i)}")
    return lines


def fib_lines(symbol: str, bars: pd.DataFrame, count: int, interval: str) -> list[str]:
    """Lineas con el Fibonacci de Frost de la ultima vela y las ultimas senales de rebote.

    Usa las mismas funciones que los indicadores `fib<n>` (`frost_fibonacci` y
    `fib_bounce_signals`).

    Args:
        symbol: simbolo.
        bars: velas con `open_time`, `open`, `high`, `low` y `close`.
        count: senales de rebote a mostrar por nivel (las mas recientes).
        interval: etiqueta de la temporalidad, solo para el texto.

    Returns:
        Las lineas de texto.
    """
    fib = frost_fibonacci(bars)
    last = fib.iloc[-1]
    lines = [f"Fibonacci de Frost {symbol} {interval} en la ultima vela cerrada "
             f"({_utc(int(bars['open_time'].iat[-1]))} UTC)"]
    if int(last["fib_leg"]) == 0:
        return lines + ["  todavia sin tres pivotes en el historial descargado"]
    for name in ("v2", "v1"):
        pos = int(last[f"fib_{name}_pos"])
        lines.append(f"  pivote {name}: {float(last[f'fib_{name}']):.2f} "
                     f"({_utc(int(bars['open_time'].iat[pos]))} UTC)")
    leg = ("subida (largos en los rebotes)" if last["fib_leg"] > 0
           else "bajada (cortos en los rebotes)")
    lines.append(f"  ultima pierna fija: {leg}")
    lines += [f"  {r * 100:g} % = {float(last[col]):.2f}"
              for r, col in zip(FROST_FIB_RATIOS, FIB_LEVEL_COLUMNS, strict=True)]
    for ratio in (0.0, 0.382, 0.5, 0.618, 0.812):
        long, short = fib_bounce_signals(bars, fib, ratio)
        idx = [i for i in range(len(bars)) if long[i] or short[i]][-count:]
        lines.append(f"Rebotes en el {ratio * 100:g} % {symbol} {interval}: ultimos {count}")
        if not idx:
            lines.append("  ninguno en el historial descargado")
        for i in idx:
            col = FIB_LEVEL_COLUMNS[FROST_FIB_RATIOS.index(ratio)]
            o, h, lo, c = (float(bars[k].iat[i]) for k in ("open", "high", "low", "close"))
            side = "LARGO" if long[i] else "CORTO"
            lines.append(f"  {_utc(int(bars['open_time'].iat[i]))}  {side}  "
                         f"nivel {float(fib[col].iat[i - 1]):.2f}  "
                         f"O {o:.2f}  H {h:.2f}  L {lo:.2f}  C {c:.2f}")
    return lines


async def build_report(client: Any, symbols: list[str], days: list[date], sfp_count: int,
                       history_bars: int, now_ms: int, interval: str = "1h",
                       rf_count: int = 0, bar_count: int = 0,
                       rf_mode: str = "intraday", ema_cross: tuple[int, int] | None = None,
                       ema_count: int = 10, cloud: int | None = None,
                       cloud_count: int = 8, fib: bool = False,
                       fib_count: int = 3) -> list[str]:
    """Descarga las velas y arma el informe completo.

    Args:
        client: cliente con `get_kline`.
        symbols: simbolos a informar.
        days: dias UTC para Camarilla.
        sfp_count: senales SFP a mostrar por simbolo (0 = ninguna).
        history_bars: velas de 1h a descargar (el SFP necesita historia para su estado).
        now_ms: instante actual en ms (o el corte de `--end`).
        interval: temporalidad de SFP y Range Filter (`1h` o `4h`).
        rf_count: giros del Range Filter a mostrar (0 = ninguno).
        bar_count: velas OHLC a listar (0 = ninguna).
        rf_mode: modo del Range Filter (`intraday`, `scalping` o `swing`).
        ema_cross: `(rapida, lenta)` para listar sus cruces, o `None`.
        ema_count: cruces de EMAs a mostrar.
        cloud: longitud del Cloud de Frost a listar, o `None`.
        cloud_count: retesteos del Cloud a mostrar.
        fib: si lista el Fibonacci de Frost.
        fib_count: senales de rebote a mostrar por nivel.

    Returns:
        Las lineas del informe.

    Efectos:
        Red: GET publicos a Bitunix.
    """
    lines = [f"Velas LAST_PRICE de Bitunix Futures; generado {_utc(now_ms)} UTC.",
             "En TradingView: mismo par perpetuo de Bitunix y zona horaria UTC.", ""]
    for symbol in symbols:
        if days:
            hourly = await fetch_bars(client, symbol, "1h", HOUR_MS, history_bars, now_ms)
            daily = await fetch_bars(client, symbol, "1d", DAY_MS, 120, now_ms)
            lines += camarilla_lines(symbol, hourly, daily, days)
        bars = await fetch_interval(client, symbol, interval, history_bars, now_ms)
        if bars.empty:
            lines += [f"{symbol} {interval}: sin velas hasta {_utc(now_ms)} UTC", ""]
            continue
        lines.append(f"{symbol}: {len(bars)} velas de {interval} desde "
                     f"{_utc(int(bars['open_time'].iat[0]))} hasta "
                     f"{_utc(int(bars['open_time'].iat[-1]))} UTC")
        if bar_count:
            lines += bars_lines(symbol, bars, bar_count, interval)
        if sfp_count:
            lines += sfp_lines(symbol, bars, sfp_count, interval)
        if rf_count:
            lines += rf_lines(symbol, bars, rf_count, interval, rf_mode)
        if ema_cross and ema_count:
            lines += ema_cross_lines(symbol, bars, ema_cross[0], ema_cross[1], ema_count, interval)
        if cloud:
            lines += cloud_lines(symbol, bars, cloud, cloud_count, interval)
        if fib:
            lines += fib_lines(symbol, bars, fib_count, interval)
        lines.append("")
    return lines


def main() -> None:
    """Punto de entrada: imprime el informe.

    Efectos:
        Red: GET publicos a Bitunix. No escribe nada.
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--symbols", nargs="+", default=list(DEFAULT_SYMBOLS))
    parser.add_argument("--days", nargs="+", default=list(DEFAULT_DAYS),
                        help="dias UTC (AAAA-MM-DD) para los niveles Camarilla")
    parser.add_argument("--sfp-count", type=int, default=10)
    parser.add_argument("--history-bars", type=int, default=2000)
    parser.add_argument("--interval", choices=sorted(INTERVAL_MS), default="1h",
                        help="temporalidad de SFP y Range Filter")
    parser.add_argument("--rf-count", type=int, default=0,
                        help="giros del Range Filter a mostrar (0 = ninguno)")
    parser.add_argument("--rf-mode", choices=sorted(RANGE_FILTER_SENSITIVITY),
                        default="intraday", help="modo del Range Filter de Frost")
    parser.add_argument("--ema-cross", nargs=2, type=int, metavar=("RAPIDA", "LENTA"),
                        help=f"lista los cruces de dos EMAs (p. ej. 9 21; largos de {EMA_LENGTHS})")
    parser.add_argument("--ema-count", type=int, default=10,
                        help="cruces de EMAs a mostrar con --ema-cross")
    parser.add_argument("--cloud", type=int, metavar="LONGITUD",
                        help="lista el Cloud de Frost y sus retesteos (p. ej. 36 o 50)")
    parser.add_argument("--cloud-count", type=int, default=8,
                        help="retesteos del Cloud a mostrar con --cloud")
    parser.add_argument("--fib", action="store_true",
                        help="lista el Fibonacci de Frost y sus rebotes")
    parser.add_argument("--fib-count", type=int, default=3,
                        help="rebotes por nivel a mostrar con --fib")
    parser.add_argument("--bars", type=int, default=0,
                        help="velas OHLC a listar antes del corte (0 = ninguna)")
    parser.add_argument("--end", help="corte AAAA-MM-DD (00:00 UTC) para mirar semanas pasadas")
    parser.add_argument("--skip-camarilla", action="store_true")
    args = parser.parse_args()
    if args.ema_cross and not 1 <= args.ema_cross[0] < args.ema_cross[1]:
        parser.error("--ema-cross: la EMA rapida debe ser mas corta que la lenta (p. ej. 9 21)")
    if args.cloud is not None and args.cloud < 1:
        parser.error("--cloud: la longitud debe ser 1 o mas")
    days = [] if args.skip_camarilla else [date.fromisoformat(d) for d in args.days]
    now_ms = int(time.time() * 1000)
    if args.end:
        end = date.fromisoformat(args.end)
        end_ms = int(datetime(end.year, end.month, end.day, tzinfo=UTC).timestamp() * 1000)
        now_ms = min(now_ms, end_ms)

    async def run() -> list[str]:
        client = BitunixRestClient(BITUNIX_URL, rate_limit_per_sec=4)
        try:
            return await build_report(client, args.symbols, days, args.sfp_count,
                                      args.history_bars, now_ms, args.interval, args.rf_count,
                                      args.bars, args.rf_mode,
                                      tuple(args.ema_cross) if args.ema_cross else None,
                                      args.ema_count, args.cloud, args.cloud_count,
                                      args.fib, args.fib_count)
        finally:
            await client.aclose()

    print("\n".join(asyncio.run(run())))


if __name__ == "__main__":
    main()
