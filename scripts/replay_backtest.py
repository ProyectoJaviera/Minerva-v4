"""Corre una estrategia de ejemplo con el backtest en tiempo real (repeticion minuto a minuto).

Uso (desde la raiz del repo, con el venv, despues de `scripts/download_minutes.py`):

    python scripts/replay_backtest.py --strategy donchian --timeframe 1h --start 2026-10-01
    python scripts/replay_backtest.py --strategy forming --symbols BTCUSDT ETHUSDT

Lee solo el archivo de minutos (`history/minute_v3.db`, en solo lectura) y no usa la red ni la
base del bot. El funding no se incluye todavia (las operaciones salen marcadas como aproximadas
con tasa 0). Imprime, por moneda y en total, operaciones, PF, PnL neto y los contadores del motor
(minutos con SL y TP a la vez, ordenes vencidas, velas con minutos faltantes).
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.download_minutes import DEFAULT_DB, refuse_foreign_db  # noqa: E402

WARMUP_BARS = 200


def _ms(day: str) -> int:
    return int(datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=UTC).timestamp() * 1000)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Lee los argumentos de la linea de comandos.

    Args:
        argv: argumentos, o `None` para los de `sys.argv`.

    Returns:
        Los argumentos.
    """
    from app.research.forward import SYMBOLS

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--strategy", choices=("donchian", "forming"), default="donchian")
    parser.add_argument("--timeframe", default="1h")
    parser.add_argument("--symbols", nargs="+", default=list(SYMBOLS))
    parser.add_argument("--start", default="2025-01-01", help="AAAA-MM-DD (UTC)")
    parser.add_argument("--end", default=None, help="AAAA-MM-DD (UTC), exclusivo; por defecto hoy")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Corre la estrategia de ejemplo sobre cada simbolo e imprime el resumen.

    Args:
        argv: argumentos, o `None` para los de `sys.argv`.

    Returns:
        El codigo de salida (0 bien, 1 sin archivo, 2 archivo rechazado).

    Efectos:
        Lee `minute_bars` del archivo de minutos (solo lectura).
    """
    from app.backtesting.metrics import compute_metrics, profit_factor_only
    from app.config import settings
    from app.replay.candles import timeframe_ms
    from app.replay.engine import run_replay
    from app.replay.examples import DonchianStopEntry, FormingCandleBreakout
    from app.replay.minute_store import MinuteStore

    args = parse_args(argv)
    refusal = refuse_foreign_db(args.db)
    if refusal:
        print(refusal)
        return 2
    if not args.db.is_file():
        print(f"No existe {args.db}: corre antes scripts/download_minutes.py")
        return 1
    start = _ms(args.start)
    end = _ms(args.end) if args.end else int(time.time() * 1000)
    warmup = WARMUP_BARS * timeframe_ms(args.timeframe)

    all_trades = []
    with MinuteStore(args.db, read_only=True) as store:
        for symbol in args.symbols:
            strat = (DonchianStopEntry if args.strategy == "donchian"
                     else FormingCandleBreakout)(timeframe=args.timeframe)
            t0 = time.monotonic()
            minutes = store.load(symbol, start - warmup, end)
            if not minutes:
                print(f"{symbol}: sin minutos en el archivo")
                continue
            res = run_replay(minutes, strat, args.strategy, symbol, settings, data_end_ms=end)
            trades = [t for t in res.trades if t.entry_time.timestamp() * 1000 >= start]
            all_trades += trades
            pf = profit_factor_only(trades)
            net = sum(t.pnl_net_usdt for t in trades)
            s = res.stats
            print(f"{symbol}: {len(trades)} operaciones, PF {pf if pf is None else round(pf, 2)}, "
                  f"neto {net:+.2f} USDT | {s.minutes} minutos, SL y TP en el mismo minuto "
                  f"{s.same_minute_sl_and_tp}, ordenes vencidas {s.orders_expired}, velas con "
                  f"minutos faltantes {s.candles_with_missing_minutes} | "
                  f"{time.monotonic() - t0:.1f} s", flush=True)
    m = compute_metrics(all_trades, settings.backtest_initial_capital)
    print(f"TOTAL: {m.total_trades} operaciones, PF {m.profit_factor}, neto "
          f"{m.pnl_net_total_usdt:+.2f} USDT, winrate {m.winrate}, comisiones "
          f"{m.fees_total_usdt:.2f} USDT. Funding NO incluido.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
