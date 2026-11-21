"""Lista las aperturas y cierres que daria un bot de senales, para compararlos con TradingView (v4).

Usa las mismas velas que el corredor (las 800 ultimas cerradas de Bitunix, LAST_PRICE) y la misma
logica del bot (`app.live.signals.bot_signals`): abre cuando la entrada da su direccion y cierra
cuando la salida da la senal contraria. No simula SL, TP ni trailing: si una operacion real cerro
antes por su SL o su TP, desde ahi la lista puede no coincidir con el bot.

Ejemplo, el bot de largos con la Reversal RSI de Frost (X verde abre, X roja cierra) en 4h, con
las horas del grafico en UTC+1:

    python scripts/bot_signals_check.py --timeframe 4h --indicators wt_cross \
        --exit-indicators wt_cross --direction long --symbols BTCUSDT TRXUSDT --tz-offset 1

En TradingView: Frost con «Reversal RSI» encendido; la X aparece en la vela listada (la decision
es al cierre de esa vela, y la operacion se llena con el primer precio de la siguiente).

Es de solo lectura: no lee `.env`, no abre ninguna base y no escribe nada.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402

from app.config import Settings  # noqa: E402
from app.live.book import CandleBook  # noqa: E402
from app.live.config import validate  # noqa: E402
from app.live.runner import BTC, BTC_INDICATORS, PAGE, WARMUP_PAGES  # noqa: E402
from app.live.signals import bot_signals  # noqa: E402
from app.market.bitunix_rest import BitunixRestClient  # noqa: E402

BITUNIX_URL = Settings.model_fields["bitunix_rest_base_url"].default
ACTION = {"OPEN": "ABRE", "CLOSE": "CIERRA", "SKIP": "OMITE (salida en la misma vela)"}


async def closed_bars(client: BitunixRestClient, symbol: str, timeframe: str,
                      now_ms: int) -> pd.DataFrame:
    """Las velas cerradas que cargaria el corredor (mismas paginas y mismo filtro)."""
    raw: list[dict] = []
    cursor: int | None = None
    for _ in range(WARMUP_PAGES):
        page = await client.get_kline(symbol=symbol, interval=timeframe, end_time=cursor,
                                      limit=PAGE)
        if not page:
            break
        raw += page
        cursor = min(int(r["time"]) for r in page)
    book = CandleBook(symbol, timeframe)
    book.load(raw, now_ms)
    return book.frame(False)


def fmt(ms: int, offset_h: float) -> str:
    """Hora de la vela en UTC y, si se pide, en la zona del grafico."""
    utc = datetime.fromtimestamp(ms / 1000, UTC)
    text = f"{utc:%Y-%m-%d %H:%M} UTC"
    if offset_h:
        local = utc + timedelta(hours=offset_h)
        text += f" ({local:%d-%m %H:%M} UTC{offset_h:+g})"
    return text


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--symbols", nargs="+", default=["BTCUSDT"])
    parser.add_argument("--timeframe", default="4h", help="15m, 1h, 4h, 1d o 1w")
    parser.add_argument("--indicators", nargs="+", required=True, help="reglas de entrada")
    parser.add_argument("--exit-indicators", nargs="*", default=[], help="reglas de salida")
    parser.add_argument("--direction", choices=("long", "short"), required=True)
    parser.add_argument("--window", type=int, default=1)
    parser.add_argument("--min-agree", type=int)
    parser.add_argument("--exit-min-agree", type=int)
    parser.add_argument("--count", type=int, default=20, help="ultimas senales por moneda")
    parser.add_argument("--tz-offset", type=float, default=0.0,
                        help="horas del grafico respecto de UTC (p. ej. 1 para UTC+1)")
    args = parser.parse_args()
    try:
        cfg = validate(name="comparacion", symbols=args.symbols, timeframe=args.timeframe,
                       indicators=args.indicators, kind="bot", direction=args.direction,
                       exit_indicators=args.exit_indicators, window=args.window,
                       min_agree=args.min_agree, exit_min_agree=args.exit_min_agree,
                       exit_name="pct", pct_exit=(1.0, None, None))
    except ValueError as exc:
        parser.error(str(exc))
    now_ms = int(time.time() * 1000)

    async def run() -> list[str]:
        client = BitunixRestClient(BITUNIX_URL, rate_limit_per_sec=4)
        lines = [f"Bot {cfg.direction} {cfg.timeframe}: entra con {'+'.join(cfg.indicators)}, "
                 f"sale con {'+'.join(cfg.exit_indicators) or 'nada (solo SL/TP)'}; "
                 "hora = apertura de la vela de la decision (se decide a su cierre)."]
        try:
            btc = None
            if BTC_INDICATORS & set(cfg.all_indicators):
                btc = await closed_bars(client, BTC, cfg.timeframe, now_ms)
            for symbol in cfg.symbols:
                frame = await closed_bars(client, symbol, cfg.timeframe, now_ms)
                sigs = bot_signals(cfg, frame, btc)
                lines.append(f"\n== {symbol} ({len(frame)} velas cerradas, "
                             f"{len(sigs)} senales) ==")
                for s in sigs[-args.count:]:
                    lines.append(f"  {fmt(s.open_time, args.tz_offset)}  {ACTION[s.action]:6s} "
                                 f"{s.side:5s} cierre {s.close:g}")
        finally:
            await client.aclose()
        return lines

    print("\n".join(asyncio.run(run())))


if __name__ == "__main__":
    main()
