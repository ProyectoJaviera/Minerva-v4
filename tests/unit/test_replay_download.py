"""Descarga incremental de minutos: paginado hacia atras, piso, huecos y otras versiones."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.replay.download import download_symbol
from app.replay.minute_store import MINUTE_MS, MinuteStore
from scripts.download_minutes import refuse_foreign_db

T0 = 1_767_571_200_000  # 2026-01-05 00:00 UTC


class FakeKlines:
    """Imita `get_kline` de Bitunix: hasta `limit` velas con open_time < end_time, mas nuevas."""

    def __init__(self, first: int, last: int, omit_in_pages: set[int] | None = None,
                 lost: set[int] | None = None) -> None:
        self.first, self.last = first, last
        self.omit_in_pages = omit_in_pages or set()  # faltan en paginas grandes, no en chicas
        self.lost = lost or set()  # no existen nunca
        self.calls: list[tuple[int | None, int]] = []

    async def get_kline(self, symbol, interval, start_time=None, end_time=None, limit=100,
                        price_type="LAST_PRICE"):
        assert interval == "1m" and limit <= 200
        self.calls.append((end_time, limit))
        top = min(end_time - MINUTE_MS, self.last)
        out = []
        t = top
        while t >= self.first and len(out) < limit:
            if t not in self.lost and not (limit == 200 and t in self.omit_in_pages):
                out.append({"time": t, "open": "1", "high": "2", "low": "0.5", "close": "1.5",
                            "baseVol": "1", "quoteVol": "2"})
            t -= MINUTE_MS
        return list(reversed(out))


@pytest.mark.asyncio
async def test_first_download_pages_back_and_never_saves_the_current_minute(tmp_path):
    src = FakeKlines(T0, T0 + 1000 * MINUTE_MS)
    now = T0 + 500 * MINUTE_MS + 30_000  # a mitad del minuto 500
    with MinuteStore(tmp_path / "m.db") as store:
        rep = await download_symbol(src, store, "BTCUSDT", T0, now)
        assert store.bounds("BTCUSDT") == (T0, T0 + 499 * MINUTE_MS)
    assert rep.saved == 500 and rep.gaps_after_repair == 0
    assert rep.requests == 3  # 200 + 200 + 100


@pytest.mark.asyncio
async def test_second_download_only_fetches_what_is_new(tmp_path):
    src = FakeKlines(T0, T0 + 10_000 * MINUTE_MS)
    with MinuteStore(tmp_path / "m.db") as store:
        await download_symbol(src, store, "BTCUSDT", T0, T0 + 400 * MINUTE_MS)
        src.calls.clear()
        rep = await download_symbol(src, store, "BTCUSDT", T0, T0 + 450 * MINUTE_MS)
        assert store.bounds("BTCUSDT") == (T0, T0 + 449 * MINUTE_MS)
    assert rep.saved == 50 and len(src.calls) == 1


@pytest.mark.asyncio
async def test_the_floor_is_recorded_when_the_api_has_no_older_data(tmp_path):
    src = FakeKlines(T0 + 100 * MINUTE_MS, T0 + 1000 * MINUTE_MS)
    with MinuteStore(tmp_path / "m.db") as store:
        rep = await download_symbol(src, store, "X", T0, T0 + 300 * MINUTE_MS)
        assert store.floor("X") == T0 + 100 * MINUTE_MS
        src.calls.clear()
        again = await download_symbol(src, store, "X", T0, T0 + 300 * MINUTE_MS)
    assert rep.gaps_after_repair == 0 and again.saved == 0
    assert len(src.calls) == 0  # ya sabe que antes del piso no hay nada


@pytest.mark.asyncio
async def test_gaps_are_repaired_with_narrow_requests_and_real_outages_are_counted(tmp_path):
    omitted = {T0 + 50 * MINUTE_MS, T0 + 51 * MINUTE_MS}
    lost = {T0 + 120 * MINUTE_MS}
    src = FakeKlines(T0, T0 + 1000 * MINUTE_MS, omit_in_pages=omitted, lost=lost)
    with MinuteStore(tmp_path / "m.db") as store:
        rep = await download_symbol(src, store, "X", T0, T0 + 300 * MINUTE_MS)
        assert store.missing_minutes("X", T0, T0 + 300 * MINUTE_MS) == sorted(lost)
    assert rep.gaps_before_repair == 3 and rep.gaps_after_repair == 1


@pytest.mark.parametrize("path, refused", [
    ("D:/x/Minerva-v2/history/history_v2.db", True),
    ("D:/x/Minerva/data/minerva.db", True),
    ("D:/x/Minerva-v3/history/market_v2.db", True),
    ("D:/x/Minerva-v3/history/minute_v3.db", False),
])
def test_the_script_refuses_files_of_other_versions(path, refused):
    assert (refuse_foreign_db(Path(path)) is not None) is refused
