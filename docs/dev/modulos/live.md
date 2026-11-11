# Guía del paquete `live`

> Parte de la [documentación técnica](../README.md) · Referencia automática: [`api/app/live.html`](../api/app/live.html)

## Qué hace el paquete

**Pruebas en vivo** de la v3: paper trading hacia adelante con el precio en tiempo real del WebSocket público de
Bitunix. El dueño define pruebas (monedas, marco, indicadores, salida y modo) y un proceso aparte las opera tick a
tick. Diseño en [`docs/v3/PRUEBAS_EN_VIVO.md`](../../v3/PRUEBAS_EN_VIVO.md). No participa del bot ni toca su base: usa
`history/live_v3.db` y `history/ticks_v3.db`.

## Archivos

### `app/live/__init__.py`

**Responsabilidad.** Marca el paquete y resume sus módulos.

**Funciones y clases clave.** Ninguna.

**Invariantes — no romper.** Ninguno propio.

**Trampas conocidas.** Ninguna conocida.

<!-- hechos:app/live/__init__.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/app/live.html`](../api/app/live.html)
- **Importa (internos):** ninguno
- **Lo importan:** ninguno
- **Configuración que lee:** ninguno
- **Tablas que lee:** ninguno
- **Tablas que escribe:** ninguno
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** ninguno
<!-- /hechos:app/live/__init__.py -->

### `app/live/book.py`

**Responsabilidad.** Las velas de una moneda y un marco: cerradas (de REST) y en curso (armada con ticks).

**Funciones y clases clave.** `CandleBook` (`load`, `on_tick`, `roll`, `frame`).

**Invariantes — no romper.** La apertura de la vela en curso es el cierre de la anterior (convención de Bitunix). Al reconciliar, REST manda sobre las velas armadas con ticks.

**Trampas conocidas.** El volumen de la vela en curso es parcial (solo los ticks recibidos).

<!-- hechos:app/live/book.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/app/live/book.html`](../api/app/live/book.html)
- **Importa (internos):** `app.backtesting.funding`, `app.replay.candles`
- **Lo importan:** `app.live.runner`
- **Configuración que lee:** ninguno
- **Tablas que lee:** ninguno
- **Tablas que escribe:** ninguno
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** `tests/unit/test_live.py`
<!-- /hechos:app/live/book.py -->

### `app/live/config.py`

**Responsabilidad.** La configuración de una prueba y su validación.

**Funciones y clases clave.** `TestConfig` y `validate()`.

**Invariantes — no romper.** Los indicadores y las salidas son los de `app/explorer/confluence.py`; una configuración guardada tiene que seguir siendo válida al releerla.

**Trampas conocidas.** `TestConfig` lleva `__test__ = False` para que pytest no la tome por una clase de pruebas.

<!-- hechos:app/live/config.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/app/live/config.html`](../api/app/live/config.html)
- **Importa (internos):** `app.explorer.confluence`
- **Lo importan:** `app.live.runner`, `app.live.store`, `scripts.live_tests`
- **Configuración que lee:** ninguno
- **Tablas que lee:** ninguno
- **Tablas que escribe:** ninguno
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** `tests/unit/test_live.py`
<!-- /hechos:app/live/config.py -->

### `app/live/paper.py`

**Responsabilidad.** La posición paper evaluada en cada tick: apertura, salidas, trailing y funding.

**Funciones y clases clave.** `open_position()`, `on_tick()`, `close_position()`, `PaperPosition`, `ClosedTrade` y `OpenRefusal`.

**Invariantes — no romper.** El SL, el trailing y la liquidación se llenan al precio del tick que los cruza (nunca mejor que el nivel); el TP, en su nivel. Puro y serializable.

**Trampas conocidas.** La liquidación usa el último precio, no el de marca.

<!-- hechos:app/live/paper.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/app/live/paper.html`](../api/app/live/paper.html)
- **Importa (internos):** `app.backtesting.liquidation`, `app.config`, `app.execution.pnl`, `app.persistence.models`, `app.strategies.base`, `app.trading.sl_calc`, `app.trading.slippage`
- **Lo importan:** `app.live.runner`, `app.live.store`
- **Configuración que lee:** `BACKTEST_SLIPPAGE_BPS`, `DEFAULT_MARGIN_USDT`, `LEVERAGE`, `MAX_SL_MARGIN_LOSS_PCT`, `TAKER_FEE_PCT`
- **Tablas que lee:** ninguno
- **Tablas que escribe:** ninguno
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** `tests/unit/test_live.py`
<!-- /hechos:app/live/paper.py -->

### `app/live/runner.py`

**Responsabilidad.** El corredor: recibe los mensajes del WebSocket, decide con la confluencia y opera todas las pruebas activas.

**Funciones y clases clave.** `LiveRunner` (`on_message`, `on_tick`, `housekeeping`, `evaluate`, `reload_tests`, `shutdown`) y `parse_trade_message()`.

**Invariantes — no romper.** Una entrada se llena en un tick POSTERIOR a la decisión. Sin ticks recientes (`STALE_SECONDS`) no se abren entradas. Como mucho una entrada por vela y por moneda. Solo endpoints públicos; nunca envía órdenes.

**Trampas conocidas.** Vacía las cachés de indicadores de `combos` y `confluence` cada minuto para no acumular memoria. Un fallo al evaluar una prueba se anota y no detiene a las demás.

<!-- hechos:app/live/runner.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/app/live/runner.html`](../api/app/live/runner.html)
- **Importa (internos):** `app.config`, `app.core.logging`, `app.explorer.confluence`, `app.live.book`, `app.live.config`, `app.live.paper`, `app.live.store`, `app.persistence.models`, `app.research.combos`, `app.strategies.base`
- **Lo importan:** `scripts.live_tests`
- **Configuración que lee:** ninguno
- **Tablas que lee:** ninguno
- **Tablas que escribe:** ninguno
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** `tests/unit/test_live.py`
<!-- /hechos:app/live/runner.py -->

### `app/live/store.py`

**Responsabilidad.** Los archivos SQLite de las pruebas (`live_tests`, `live_positions`, `live_trades`, `live_events`, `live_heartbeat`) y de ticks (`ticks`).

**Funciones y clases clave.** `LiveStore`, `TickStore` y `StoredTest`.

**Invariantes — no romper.** Viven aparte de la base del bot. `record_trade()` guarda la operación y borra la posición en una sola transacción.

**Trampas conocidas.** Los comandos y el corredor abren el mismo archivo a la vez (WAL, transacciones cortas).

<!-- hechos:app/live/store.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/app/live/store.html`](../api/app/live/store.html)
- **Importa (internos):** `app.live.config`, `app.live.paper`
- **Lo importan:** `app.live.runner`, `scripts.live_tests`
- **Configuración que lee:** ninguno
- **Tablas que lee:** ninguno
- **Tablas que escribe:** ninguno
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** `tests/unit/test_live.py`
<!-- /hechos:app/live/store.py -->
