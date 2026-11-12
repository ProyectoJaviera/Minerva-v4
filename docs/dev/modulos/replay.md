# Guía del paquete `replay`

> Parte de la [documentación técnica](../README.md) · Referencia automática: [`api/app/replay.html`](../api/app/replay.html)

## Qué hace el paquete

**Backtest en tiempo real** de la v3: recorre la historia minuto a minuto con las velas de 1 minuto de Bitunix y deja
que la estrategia entre dentro de la vela (con órdenes de stop, límite o a mercado) y mire la vela en curso. Diseño y
reglas en [`docs/v3/BACKTEST_TIEMPO_REAL.md`](../../v3/BACKTEST_TIEMPO_REAL.md). No participa del bot ni toca su base:
lee y escribe solo `history/minute_v4.db`.

## Archivos

### `app/replay/__init__.py`

**Responsabilidad.** Marca el paquete y resume sus módulos.

**Funciones y clases clave.** Ninguna.

**Invariantes — no romper.** Ninguno propio.

**Trampas conocidas.** Ninguna conocida.

<!-- hechos:app/replay/__init__.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/app/replay.html`](../api/app/replay.html)
- **Importa (internos):** ninguno
- **Lo importan:** ninguno
- **Configuración que lee:** ninguno
- **Tablas que lee:** ninguno
- **Tablas que escribe:** ninguno
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** ninguno
<!-- /hechos:app/replay/__init__.py -->

### `app/replay/candles.py`

**Responsabilidad.** Arma las velas del marco de la estrategia con los minutos.

**Funciones y clases clave.** `bucket_start()` (cubo de cada minuto; los de `1w` empiezan el lunes 00:00 UTC),
`aggregate()` (todas las velas, con los minutos faltantes contados en `missing`) y `Candle` (también la vela en curso).

**Invariantes — no romper.** Los cubos de 1m a 1d se alinean con la época Unix como los de Bitunix; cambiar la
alineación cambia todas las velas armadas.

**Trampas conocidas.** Una vela con minutos faltantes se arma igual con los que hay; el motor los cuenta en `candles_with_missing_minutes`.

<!-- hechos:app/replay/candles.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/app/replay/candles.html`](../api/app/replay/candles.html)
- **Importa (internos):** `app.replay.minute_store`
- **Lo importan:** `app.live.book`, `app.replay.engine`, `scripts.replay_backtest`
- **Configuración que lee:** ninguno
- **Tablas que lee:** ninguno
- **Tablas que escribe:** ninguno
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** `tests/unit/test_replay.py`
<!-- /hechos:app/replay/candles.py -->

### `app/replay/download.py`

**Responsabilidad.** Descarga incremental de minutos desde el endpoint público de velas de Bitunix
(`/api/v1/futures/market/kline`, `interval=1m`).

**Invariantes — no romper.** Nunca guarda el minuto en curso; solo usa el endpoint público.

**Funciones y clases clave.** `download_symbol()` (pagina hacia atrás, nunca guarda el minuto en curso, anota el piso y
repara huecos con pedidos chicos) y `DownloadReport`.

**Trampas conocidas.** Una página vacía suelta no es el piso: se reintenta `EMPTY_PAGE_RETRIES` veces antes de anotarlo.
La reparación de huecos tiene un tope de 500 tramos por corrida.

<!-- hechos:app/replay/download.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/app/replay/download.html`](../api/app/replay/download.html)
- **Importa (internos):** `app.replay.minute_store`
- **Lo importan:** `scripts.download_minutes`
- **Configuración que lee:** ninguno
- **Tablas que lee:** ninguno
- **Tablas que escribe:** ninguno
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** `tests/unit/test_replay_download.py`
<!-- /hechos:app/replay/download.py -->

### `app/replay/engine.py`

**Responsabilidad.** El motor de repetición minuto a minuto (puro: sin base ni red).

**Funciones y clases clave.** `run_replay()`, `ReplayStrategy` (base de las estrategias: `precompute`, `on_bar_close`,
`on_minute`, `stop_price`, `take_profit_price`, `trailing_distance`, `max_hold_ms`), `EntryOrder` / `EntryKind`,
`MarketView` (con `with_forming()`) y `ReplayStats`.

**Invariantes — no romper.** La estrategia nunca ve un precio futuro: decide con el último minuto terminado y una
entrada a mercado se llena en la apertura del minuto siguiente. Dentro del minuto se elige la lectura menos favorable
(SL antes que TP). Devuelve `RawTrade`/`RawSkip` del motor de velas para reusar las métricas.

**Trampas conocidas.** La liquidación usa el último precio (el archivo no trae el de marca). `on_minute` solo se llama
sin posición y si la estrategia declara `uses_minutes = True`.

<!-- hechos:app/replay/engine.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/app/replay/engine.html`](../api/app/replay/engine.html)
- **Importa (internos):** `app.backtesting.engine`, `app.backtesting.funding`, `app.backtesting.liquidation`, `app.config`, `app.execution.pnl`, `app.persistence.models`, `app.replay.candles`, `app.replay.minute_store`, `app.strategies.base`, `app.trading.sl_calc`, `app.trading.slippage`
- **Lo importan:** `app.replay.examples`, `scripts.replay_backtest`
- **Configuración que lee:** `BACKTEST_SLIPPAGE_BPS`, `DEFAULT_MARGIN_USDT`, `LEVERAGE`, `MAKER_FEE_PCT`, `MAX_SL_MARGIN_LOSS_PCT`, `TAKER_FEE_PCT`
- **Tablas que lee:** ninguno
- **Tablas que escribe:** ninguno
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** `tests/unit/test_replay.py`
<!-- /hechos:app/replay/engine.py -->

### `app/replay/examples.py`

**Responsabilidad.** Estrategias de ejemplo para probar el motor (plantillas, no candidatas).

**Funciones y clases clave.** `DonchianStopEntry` (órdenes de stop en el canal de N velas) y `FormingCandleBreakout`
(decide en cada minuto con la vela en curso).

**Invariantes — no romper.** Ninguno propio.

**Trampas conocidas.** No pasaron por un preregistro: sus resultados no significan nada.

<!-- hechos:app/replay/examples.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/app/replay/examples.html`](../api/app/replay/examples.html)
- **Importa (internos):** `app.indicators.engine`, `app.persistence.models`, `app.replay.engine`
- **Lo importan:** `scripts.replay_backtest`
- **Configuración que lee:** ninguno
- **Tablas que lee:** ninguno
- **Tablas que escribe:** ninguno
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** ninguno
<!-- /hechos:app/replay/examples.py -->

### `app/replay/minute_store.py`

**Responsabilidad.** El archivo SQLite de minutos (`minute_bars`, `minute_floor`).

**Invariantes — no romper.** Vive aparte de la base del bot; el motor y los scripts de lectura lo abren en solo lectura.

**Funciones y clases clave.** `MinuteStore` (`upsert`, `load`, `bounds`, `missing_minutes`, `floor`), `MinuteBar` y
`bar_from_raw()`.

**Trampas conocidas.** En Bitunix el `open` de un minuto es el cierre del anterior y puede quedar fuera de
`[low, high]`: `bar_from_raw()` amplía el rango en vez de descartar la vela.

<!-- hechos:app/replay/minute_store.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/app/replay/minute_store.html`](../api/app/replay/minute_store.html)
- **Importa (internos):** ninguno
- **Lo importan:** `app.replay.candles`, `app.replay.download`, `app.replay.engine`, `scripts.download_minutes`, `scripts.replay_backtest`
- **Configuración que lee:** ninguno
- **Tablas que lee:** ninguno
- **Tablas que escribe:** ninguno
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** `tests/unit/test_replay.py`, `tests/unit/test_replay_download.py`
<!-- /hechos:app/replay/minute_store.py -->
