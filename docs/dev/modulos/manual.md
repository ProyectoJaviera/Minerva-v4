# Guía del paquete `manual`

> Parte de la [documentación técnica](../README.md) · Referencia automática: [`api/app/manual.html`](../api/app/manual.html)

## Qué hace el paquete

**Sala de trading manual** de la v4: una cuenta paper que el dueño opera a mano desde la pestaña «Sala de trading»,
con el precio real del WebSocket público de Bitunix. Usa el mismo motor paper que las pruebas «En vivo» y guarda
todo en tablas propias del archivo de pruebas. No cambia ninguna prueba, su gemelo al azar ni la medición, y nunca
envía órdenes reales.

## Archivos

### `app/manual/__init__.py`

**Responsabilidad.** Marca el paquete de la Sala de trading manual (v4) y resume qué es.

**Funciones y clases clave.** Ninguna.

**Invariantes — no romper.** Ninguno propio.

**Trampas conocidas.** Ninguna conocida.

<!-- hechos:app/manual/__init__.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/app/manual.html`](../api/app/manual.html)
- **Importa (internos):** ninguno
- **Lo importan:** ninguno
- **Configuración que lee:** ninguno
- **Tablas que lee:** ninguno
- **Tablas que escribe:** ninguno
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** ninguno
<!-- /hechos:app/manual/__init__.py -->

### `app/manual/desk.py`

**Responsabilidad.** El escritorio de la cuenta manual: valida y guarda las órdenes, las llena tick a tick con el
motor paper de las pruebas (`app.live.paper`), aplica TP/SL, liquidación y funding, y hace los cierres manuales
(todo o el 50 %), «SL a la entrada», «cerrar todo» y el reinicio de la cuenta. Admite TP parciales (hasta 3
niveles, con SL a la entrada tras el TP1) y trailing stop en %.

**Funciones y clases clave.** `ManualDesk` (`place`, `cancel`, `close`, `breakeven`, `close_all`, `reset`, `watch`,
`symbols`, `on_tick`, `snapshot`) y `ManualError` (el motivo de un rechazo, para el dueño).

**Invariantes — no romper.**
- Nunca envía nada a Bitunix: solo recibe ticks del corredor.
- Orden a mercado: se llena con el primer tick **posterior** a la orden. Orden límite: al precio límite cuando un
  tick lo cruza, sin deslizamiento de entrada; si al crearla ya estaba cruzada, se llena con el primer tick.
- Sin SL, el stop interno queda en el 100 % del margen: la liquidación actúa antes. Un SL que perdería el 90 % del
  margen o más se rechaza.
- Liquidación en margen aislado: la pérdida se limita al margen más la comisión de entrada aunque el tick salte.
- El saldo cambia solo al cerrar. Disponible = saldo − margen abierto − margen reservado por órdenes.
- Un cierre manual exige un precio de menos de `PRICE_FRESH_SECONDS`.

**Trampas conocidas.** Vive dentro del corredor: con el bot apagado no recibe ticks y sus posiciones no se vigilan
hasta encenderlo (al volver, un SL saltado se llena al precio del primer tick). Las monedas «miradas» caducan a los
`WATCH_SECONDS` si el dashboard deja de pedirlas.

<!-- hechos:app/manual/desk.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/app/manual/desk.html`](../api/app/manual/desk.html)
- **Importa (internos):** `app.config`, `app.live.paper`, `app.live.report`, `app.manual.store`, `app.persistence.models`
- **Lo importan:** `app.api.routes.sala`, `app.live.runner`, `app.live.service`
- **Configuración que lee:** `BACKTEST_SLIPPAGE_BPS`, `TAKER_FEE_PCT`
- **Tablas que lee:** ninguno
- **Tablas que escribe:** ninguno
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** `tests/unit/test_manual_desk.py`, `tests/unit/test_sala_api.py`
<!-- /hechos:app/manual/desk.py -->

### `app/manual/store.py`

**Responsabilidad.** Las tablas `manual_*` dentro del archivo de pruebas en vivo (`LIVE_TESTS_DB`): cuenta (saldo y
último reinicio), posiciones abiertas (JSON de `PaperPosition` más su configuración), órdenes pendientes y
operaciones cerradas.

**Funciones y clases clave.** `ManualStore` (`balance`, `set_balance`, `next_id`, `save_position`, `positions`,
`save_order`, `orders`, `record_trade`, `trades`, `realized_since`, `reset`) y `DEFAULT_BALANCE_USDT` (1 000).

**Invariantes — no romper.** Esquema aditivo (`CREATE TABLE IF NOT EXISTS`): no toca las tablas `live_*` ni la base
del bot. El reinicio borra posiciones y órdenes pero conserva el historial.

**Trampas conocidas.** La conexión permite otro hilo (`check_same_thread=False`) porque la app y el corredor comparten
el bucle de eventos y las pruebas usan un hilo aparte; no la uses desde dos hilos a la vez.

<!-- hechos:app/manual/store.py -->
**Hechos derivados del código** (los escribe `scripts/build_dev_docs.py`; no se editan):

- **Referencia pdoc:** [`api/app/manual/store.html`](../api/app/manual/store.html)
- **Importa (internos):** `app.live.paper`
- **Lo importan:** `app.api.routes.sala`, `app.live.service`, `app.manual.desk`
- **Configuración que lee:** ninguno
- **Tablas que lee:** ninguno
- **Tablas que escribe:** ninguno
- **Tablas que crea o altera:** ninguno
- **Pruebas que lo importan o citan:** `tests/unit/test_manual_desk.py`, `tests/unit/test_sala_api.py`
<!-- /hechos:app/manual/store.py -->
