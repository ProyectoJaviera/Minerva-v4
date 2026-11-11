"""El corredor de las pruebas en vivo dentro de la app (contenedor `minerva-v3`).

`maybe_start_live_tests` lo arranca en el `lifespan` solo si `LIVE_TESTS_ENABLED` esta encendido,
en su propia tarea (un bucle de un segundo) que abre el WebSocket publico del canal `trade` cuando
hay monedas que escuchar y lo cierra cuando no (sin pruebas activas o con el bot apagado desde el
dashboard). Un fallo suyo se registra y nunca impide arrancar ni detiene al resto de la app. Usa su
propio cliente REST (publico) para no competir con el limitador del bot heredado.
"""

from __future__ import annotations

import asyncio

from app.config import Settings
from app.core.logging import get_logger
from app.live.runner import LiveRunner
from app.live.store import LiveStore, TickStore
from app.market.bitunix_rest import BitunixRestClient
from app.market.bitunix_ws import BitunixPublicWSClient

logger = get_logger(__name__)


class LiveTestsService:
    """Dueno del corredor, su WebSocket, sus archivos y su cliente REST.

    Args:
        settings: configuracion.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.store = LiveStore(settings.live_tests_db)
        self.ticks = TickStore(settings.live_ticks_db) if settings.live_record_ticks else None
        self.rest = BitunixRestClient(base_url=settings.bitunix_rest_base_url,
                                      rate_limit_per_sec=settings.bitunix_rate_limit_per_sec)
        self.ws: BitunixPublicWSClient | None = None
        self._ws_task: asyncio.Task | None = None
        self.runner = LiveRunner(settings, self.store, self.ticks, self.rest,
                                 on_symbols=self.sync_ws,
                                 max_evals_per_cycle=settings.live_max_evals_per_second)
        self._stop = asyncio.Event()
        self.last_error: str | None = None

    def stop(self) -> None:
        """Pide que el bucle termine.

        Efectos:
            Cierra el WebSocket del corredor (sale de su bucle de reconexion).
        """
        self._stop.set()
        if self.ws is not None:
            self.ws.stop()

    async def sync_ws(self, symbols: list[str]) -> None:
        """Abre, ajusta o cierra el WebSocket segun las monedas que hay que escuchar.

        Sin monedas (bot apagado o sin pruebas activas) no queda ninguna conexion abierta.

        Args:
            symbols: monedas a escuchar.

        Efectos:
            Red: abre o cierra la conexion WebSocket publica y envia suscripciones.
        """
        if not symbols:
            if self.ws is not None:
                self.ws.stop()
            if self._ws_task is not None:
                self._ws_task.cancel()
            self.ws, self._ws_task = None, None
            return
        if self.ws is None or self._ws_task is None or self._ws_task.done():
            self.ws = BitunixPublicWSClient(self.settings.bitunix_ws_public_url, symbols,
                                            channel="trade", on_message=self.runner.on_message)
            self._ws_task = asyncio.create_task(self.ws.run_forever())
            return
        await self.ws.set_symbols(symbols)

    async def run_forever(self) -> None:
        """El bucle de un segundo del corredor; un error de una vuelta se registra y se sigue.

        Efectos:
            Red: GET publicos de Bitunix. Escribe los archivos de pruebas y de ticks.
        """
        self.store.log("RUN", "corredor iniciado dentro de la app")
        while not self._stop.is_set():
            try:
                await self.runner.housekeeping()
                self.last_error = None
            except Exception as exc:  # noqa: BLE001 - el corredor nunca tira abajo la app
                self.last_error = f"{type(exc).__name__}: {exc}"
                logger.exception("fallo una vuelta del corredor de pruebas en vivo")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=1.0)
            except TimeoutError:
                pass

    async def aclose(self) -> None:
        """Guarda lo pendiente y cierra archivos y cliente.

        Efectos:
            Escribe los archivos de pruebas y de ticks.
        """
        if self._ws_task is not None:
            self._ws_task.cancel()
            try:
                await self._ws_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001 - ya se esta cerrando
                pass
        try:
            self.runner.shutdown()
        finally:
            await self.rest.aclose()
            self.store.close()
            if self.ticks is not None:
                self.ticks.close()


def maybe_start_live_tests(settings: Settings, tasks: list) -> LiveTestsService | None:
    """Arranca el corredor si `LIVE_TESTS_ENABLED` esta encendido.

    Args:
        settings: configuracion.
        tasks: lista de tareas del `lifespan`, a la que se agregan las del corredor.

    Returns:
        El servicio, o `None` si esta apagado o no pudo arrancar.

    Efectos:
        Crea la tarea del bucle (que abre el WebSocket cuando hay monedas que escuchar) y abre los
        archivos de pruebas y de ticks.
    """
    if not settings.live_tests_enabled:
        return None
    try:
        service = LiveTestsService(settings)
    except Exception:  # noqa: BLE001 - sin corredor, la app sigue
        logger.critical("No se pudo arrancar el corredor de pruebas en vivo", exc_info=True)
        return None
    tasks.append(asyncio.create_task(service.run_forever()))
    logger.info("Pruebas en vivo encendidas (archivo %s)", settings.live_tests_db)
    return service
