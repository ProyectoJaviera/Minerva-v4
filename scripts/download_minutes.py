"""Baja (o completa) las velas de 1 minuto de Bitunix al archivo de minutos de la v3.

Uso (desde la raiz del repo, con el venv):

    python scripts/download_minutes.py                      # universo de la v2, desde 2025-01-01
    python scripts/download_minutes.py --symbols BTCUSDT --start 2026-11-01
    python scripts/download_minutes.py --start 2023-01-01   # todo lo que hay (varias horas)

Es incremental: la segunda vez solo baja lo nuevo. Escribe en `history/minute_v4.db` (o
`--db`). Rechaza los archivos de la v1 y de la v2. Solo usa el endpoint publico de velas.
Cada ano son 525 600 minutos por moneda (unos 2 600 pedidos de 200 velas, unos 30 MB). Baja
cuatro monedas a la vez sin pasar el tope de pedidos por segundo: medido el 2026-11-11, cada
pedido tarda ~0,4 s, asi que una moneda sola avanza a ~2,5 pedidos por segundo.
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

DEFAULT_DB = REPO_ROOT / "history" / "minute_v4.db"
DEFAULT_START = "2025-01-01"
# Bitunix documenta 10 pedidos por segundo por IP; se deja margen.
MAX_RATE = 8


def refuse_foreign_db(path: Path) -> str | None:
    """El motivo para rechazar un archivo de otra version.

    Args:
        path: ruta pedida.

    Returns:
        El mensaje de rechazo, o `None` si es aceptable.
    """
    text = str(path.resolve()).replace("\\", "/").lower()
    if ("/minerva-v2/" in text or "/minerva-v3/" in text or "/minerva/" in text
            or "_v2.db" in text or "_v3.db" in text):
        return f"RECHAZADO: {path} parece un archivo de la v1, la v2 o la v3; la v4 usa el suyo."
    return None


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
    parser.add_argument("--symbols", nargs="+", default=list(SYMBOLS))
    parser.add_argument("--start", default=DEFAULT_START, help="AAAA-MM-DD (UTC)")
    parser.add_argument("--rate", type=int, default=6, help=f"pedidos por segundo (max {MAX_RATE})")
    return parser.parse_args(argv)


async def run(args: argparse.Namespace) -> int:
    """Baja los minutos de cada simbolo pedido.

    Args:
        args: argumentos de `parse_args`.

    Returns:
        El codigo de salida (0 bien, 2 archivo rechazado).

    Efectos:
        Red: GET `kline` a Bitunix. Escribe `minute_bars` y `minute_floor` en el archivo.
    """
    from app.config import settings
    from app.market.bitunix_rest import BitunixRestClient
    from app.replay.download import download_symbol
    from app.replay.minute_store import MinuteStore

    refusal = refuse_foreign_db(args.db)
    if refusal:
        print(refusal)
        return 2
    start_ms = int(datetime.strptime(args.start, "%Y-%m-%d").replace(tzinfo=UTC).timestamp()
                   * 1000)
    client = BitunixRestClient(base_url=settings.bitunix_rest_base_url,
                               rate_limit_per_sec=max(1, min(args.rate, MAX_RATE)))
    store = MinuteStore(args.db)
    # Varias monedas a la vez: cada pedido espera la respuesta (~0,3 s), asi que en serie no se
    # llega al limite. El limitador del cliente es compartido y mantiene el tope por segundo.
    sem = asyncio.Semaphore(4)
    done = 0

    async def one(symbol: str) -> None:
        nonlocal done
        async with sem:
            t0 = time.monotonic()
            rep = await download_symbol(client, store, symbol, start_ms,
                                        int(time.time() * 1000), progress=print)
            done += 1
            print(f"[{done}/{len(args.symbols)}] {symbol}: {rep.saved} minutos guardados, "
                  f"{rep.requests} pedidos, huecos {rep.gaps_before_repair} -> "
                  f"{rep.gaps_after_repair}, descartados {rep.rejected}, "
                  f"{time.monotonic() - t0:.0f} s", flush=True)

    try:
        await asyncio.gather(*(one(s) for s in args.symbols))
    finally:
        store.close()
        await client.aclose()
    return 0


def main() -> None:
    """Punto de entrada: corre `run` y sale con su codigo."""
    sys.exit(asyncio.run(run(parse_args())))


if __name__ == "__main__":
    main()
