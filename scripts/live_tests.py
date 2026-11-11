"""Pruebas en vivo de la v3: paper trading hacia adelante con el precio en tiempo real.

Uso (desde la raiz del repo, con el venv):

    python scripts/live_tests.py indicators
    python scripts/live_tests.py add --name "RF + EMA 15m" --timeframe 15m --indicators rf ema \
        --symbols BTCUSDT ETHUSDT --mode intrabar --exit s2_tp2r
    python scripts/live_tests.py list
    python scripts/live_tests.py report            # todas; o: report 3
    python scripts/live_tests.py stop 3
    python scripts/live_tests.py run               # el proceso que queda corriendo (Ctrl+C sale)

`run` escucha el canal `trade` del WebSocket publico de Bitunix, graba cada operacion en
`history/ticks_v3.db` y opera en paper todas las pruebas activas de `history/live_v3.db`. Los demas
comandos solo leen o escriben ese archivo, asi que se pueden usar con `run` andando: el corredor ve
las pruebas nuevas y las detenidas en unos 10 segundos. Nunca envia ordenes reales.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.download_minutes import refuse_foreign_db  # noqa: E402

DEFAULT_LIVE_DB = REPO_ROOT / "history" / "live_v3.db"
DEFAULT_TICK_DB = REPO_ROOT / "history" / "ticks_v3.db"


def _fmt(ms: int | None) -> str:
    if ms is None:
        return "-"
    return datetime.fromtimestamp(ms / 1000, tz=UTC).strftime("%Y-%m-%d %H:%M:%S")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Lee los argumentos de la linea de comandos.

    Args:
        argv: argumentos, o `None` para los de `sys.argv`.

    Returns:
        Los argumentos (con `command`).
    """
    from app.explorer.confluence import DEFAULT_EXIT, DEFAULT_WINDOW
    from app.research.forward import SYMBOLS

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", type=Path, default=DEFAULT_LIVE_DB)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("indicators", help="lista los indicadores, filtros y salidas disponibles")

    add = sub.add_parser("add", help="crea una prueba (queda activa desde ahora)")
    add.add_argument("--name", required=True)
    add.add_argument("--timeframe", required=True, help="15m, 1h, 4h, 1d o 1w")
    add.add_argument("--indicators", nargs="+", required=True)
    add.add_argument("--symbols", nargs="+", default=list(SYMBOLS))
    add.add_argument("--mode", choices=("close", "intrabar"), default="close")
    add.add_argument("--exit", dest="exit_name", default=DEFAULT_EXIT)
    add.add_argument("--sl-pct", type=float, help="con --exit pct: SL en %% del precio")
    add.add_argument("--tp-pct", type=float)
    add.add_argument("--trail-pct", type=float)
    add.add_argument("--window", type=int, default=DEFAULT_WINDOW)
    add.add_argument("--min-agree", type=int)
    add.add_argument("--eval-seconds", type=int, default=15)

    sub.add_parser("list", help="lista las pruebas")
    rep = sub.add_parser("report", help="resultados de una prueba o de todas")
    rep.add_argument("test_id", type=int, nargs="?")
    stop = sub.add_parser("stop", help="detiene una prueba (cierra sus posiciones)")
    stop.add_argument("test_id", type=int)

    run = sub.add_parser("run", help="el corredor: queda escuchando y operando en paper")
    run.add_argument("--ticks-db", type=Path, default=DEFAULT_TICK_DB)
    run.add_argument("--no-ticks", action="store_true", help="no grabar los ticks")
    run.add_argument("--seconds", type=float, help="salir solo despues de N segundos (pruebas)")
    return parser.parse_args(argv)


def cmd_indicators() -> int:
    """Imprime los indicadores, filtros y salidas que se pueden elegir.

    Returns:
        El codigo de salida (0).
    """
    from app.explorer.confluence import EXITS, INDICATORS, PCT_EXIT, PCT_EXIT_LABEL

    print("Indicadores y filtros (--indicators):")
    for key, (label, kind, _, _) in INDICATORS.items():
        print(f"  {key:12s} [{kind}] {label}")
    print("Salidas (--exit):")
    for key, (label, *_rest) in EXITS.items():
        print(f"  {key:12s} {label}")
    print(f"  {PCT_EXIT:12s} {PCT_EXIT_LABEL}: --sl-pct, --tp-pct, --trail-pct")
    return 0


def cmd_add(args: argparse.Namespace) -> int:
    """Valida y guarda una prueba nueva.

    Args:
        args: argumentos de `add`.

    Returns:
        El codigo de salida (0 bien, 1 configuracion invalida).

    Efectos:
        Escribe `live_tests` en el archivo de pruebas.
    """
    from app.live.config import validate
    from app.live.store import LiveStore

    pct = (args.sl_pct, args.tp_pct, args.trail_pct) if args.exit_name == "pct" else None
    try:
        cfg = validate(name=args.name, symbols=args.symbols, timeframe=args.timeframe,
                       indicators=args.indicators, mode=args.mode, exit_name=args.exit_name,
                       pct_exit=pct, window=args.window, min_agree=args.min_agree,
                       eval_seconds=args.eval_seconds)
    except ValueError as exc:
        print(f"Prueba invalida: {exc}")
        return 1
    with LiveStore(args.db) as store:
        test_id = store.add_test(cfg)
    print(f"Prueba {test_id} creada: '{cfg.name}' en {cfg.timeframe}, modo {cfg.mode}, "
          f"indicadores {', '.join(cfg.indicators)}, {len(cfg.symbols)} monedas, "
          f"salida {cfg.exit_name}.")
    print("Empieza a operar cuando el corredor este andando: python scripts/live_tests.py run")
    return 0


def cmd_list_or_report(args: argparse.Namespace, detailed: bool) -> int:
    """Imprime las pruebas y, con `detailed`, sus resultados y ultimos eventos.

    Args:
        args: argumentos de `list` o `report`.
        detailed: incluye resultados, posiciones abiertas y eventos.

    Returns:
        El codigo de salida (0 bien, 1 sin archivo o prueba inexistente).

    Efectos:
        Lee el archivo de pruebas (solo lectura).
    """
    from app.live.report import summarize
    from app.live.store import LiveStore

    if not args.db.is_file():
        print(f"No existe {args.db}: crea una prueba con 'add'.")
        return 1
    with LiveStore(args.db, read_only=True) as store:
        beat = store.heartbeat()
        if beat is None:
            print("Corredor: nunca corrio.")
        else:
            age = time.time() - beat[0] / 1000
            state = "ANDANDO" if age < 60 else f"DETENIDO (ultimo latido hace {age / 60:.0f} min)"
            print(f"Corredor: {state} | {beat[1]}")
        wanted = getattr(args, "test_id", None)
        tests = [t for t in store.tests() if wanted in (None, t.id)]
        if wanted is not None and not tests:
            print(f"No existe la prueba {wanted}.")
            return 1
        open_positions = store.positions()
        for t in tests:
            cfg = t.config
            s = summarize([tr for _, tr in store.trades(t.id)])
            pf = "-" if s["pf"] is None else f"{s['pf']:.2f}"
            wr = "-" if s["winrate"] is None else f"{100 * s['winrate']:.0f} %"
            n_open = sum(1 for (tid, _s) in open_positions if tid == t.id)
            print(f"[{t.id}] {cfg.name} | {t.status} desde {_fmt(t.created_at_ms)} | "
                  f"{cfg.timeframe} {cfg.mode} | {'+'.join(cfg.indicators)} | {cfg.exit_name} | "
                  f"{s['trades']} operaciones, PF {pf}, ganadoras {wr}, neto {s['net']:+.2f} USDT"
                  f" | abiertas {n_open}")
            if not detailed:
                continue
            for (tid, symbol), pos in sorted(open_positions.items()):
                if tid == t.id:
                    print(f"    abierta {symbol} {pos.side} a {pos.entry_price} desde "
                          f"{_fmt(pos.entry_time_ms)}, SL {pos.stop}, TP {pos.tp}")
            for symbol, tr in store.trades(t.id)[-10:]:
                print(f"    {_fmt(tr.exit_time_ms)} {symbol} {tr.side} {tr.entry_price} -> "
                      f"{tr.exit_price} {tr.close_reason} neto {tr.pnl_net_usdt:+.4f}")
            for ts, _tid, symbol, kind, detail in reversed(store.events(t.id, limit=8)):
                print(f"    . {_fmt(ts)} {kind} {symbol or ''} {detail[:110]}")
    return 0


def cmd_stop(args: argparse.Namespace) -> int:
    """Detiene una prueba.

    Args:
        args: argumentos de `stop`.

    Returns:
        El codigo de salida (0 detenida, 1 no existe o ya estaba detenida).

    Efectos:
        Escribe `live_tests` en el archivo de pruebas.
    """
    from app.live.store import LiveStore

    with LiveStore(args.db) as store:
        ok = store.stop_test(args.test_id)
    print(f"Prueba {args.test_id} detenida: el corredor cierra sus posiciones en unos segundos."
          if ok else f"La prueba {args.test_id} no existe o ya estaba detenida.")
    return 0 if ok else 1


async def cmd_run(args: argparse.Namespace) -> int:
    """Corre el corredor hasta Ctrl+C (o `--seconds`).

    Args:
        args: argumentos de `run`.

    Returns:
        El codigo de salida (0).

    Efectos:
        Red: WebSocket publico y GET publicos de Bitunix. Escribe los archivos de pruebas y de
        ticks.
    """
    from app.config import settings
    from app.live.runner import LiveRunner
    from app.live.store import LiveStore, TickStore
    from app.market.bitunix_rest import BitunixRestClient
    from app.market.bitunix_ws import BitunixPublicWSClient

    store = LiveStore(args.db)
    ticks = None if args.no_ticks else TickStore(args.ticks_db)
    rest = BitunixRestClient(base_url=settings.bitunix_rest_base_url,
                             rate_limit_per_sec=settings.bitunix_rate_limit_per_sec)
    ws = BitunixPublicWSClient(settings.bitunix_ws_public_url, [], channel="trade")
    runner = LiveRunner(settings, store, ticks, rest, on_symbols=ws.set_symbols)
    ws.on_message = runner.on_message
    store.log("RUN", "corredor iniciado")
    ws_task = asyncio.create_task(ws.run_forever())
    deadline = None if args.seconds is None else time.monotonic() + args.seconds
    last_print = 0.0
    try:
        while deadline is None or time.monotonic() < deadline:
            await runner.housekeeping()
            if time.monotonic() - last_print >= 60:
                last_print = time.monotonic()
                print(f"{_fmt(int(time.time() * 1000))} | {len(runner.tests)} pruebas, "
                      f"{len(runner.positions)} posiciones abiertas, {runner.ticks_seen} ticks",
                      flush=True)
            await asyncio.sleep(1.0)
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        ws.stop()
        ws_task.cancel()
        runner.shutdown()
        await rest.aclose()
        store.close()
        if ticks is not None:
            ticks.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    """Punto de entrada: despacha el comando pedido.

    Args:
        argv: argumentos, o `None` para los de `sys.argv`.

    Returns:
        El codigo de salida.
    """
    args = parse_args(argv)
    for path in (args.db, getattr(args, "ticks_db", None)):
        refusal = refuse_foreign_db(path) if path is not None else None
        if refusal:
            print(refusal)
            return 2
    if args.command == "indicators":
        return cmd_indicators()
    if args.command == "add":
        return cmd_add(args)
    if args.command == "list":
        return cmd_list_or_report(args, detailed=False)
    if args.command == "report":
        return cmd_list_or_report(args, detailed=True)
    if args.command == "stop":
        return cmd_stop(args)
    try:
        return asyncio.run(cmd_run(args))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
