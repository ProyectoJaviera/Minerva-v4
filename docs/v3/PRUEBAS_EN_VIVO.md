# Minerva v3: pruebas en vivo (paper hacia adelante, en tiempo real)

**Para qué sirve.** Dejar corriendo, a criterio del dueño, pruebas de paper trading que operan con el
precio real de Bitunix **desde ahora hacia adelante**: cada prueba combina los indicadores y filtros
que él elige, sobre las monedas y el marco que quiera. No usa el pasado para decidir si algo sirve:
solo cuentan las operaciones abiertas después de crear la prueba.

No es el bot heredado de la v1: es un proceso aparte, sin LLM, sin Telegram y sin la base del bot.
Nunca envía órdenes reales.

## 1. Qué se puede configurar

| Qué | Opciones |
|---|---|
| Monedas | cualquier contrato `...USDT` de Bitunix (por defecto, el universo de la v2) |
| Marco | `15m`, `1h`, `4h`, `1d`, `1w` |
| Indicadores y filtros | los 14 de la pestaña Indicadores: SFP, Camarilla, FVG, zona de liquidez, Liquidity Grab, BB%B, cruce de WaveTrend, Range Filter, VIDYA, WaveTrend, EMA 144, funding, régimen de BTC y fuerza relativa contra BTC |
| Cuántos deben coincidir | todos (por defecto) o al menos N (`--min-agree`) |
| Salida | las 6 fijas en ATR (por ejemplo SL 2 ATR y TP 2R) o personalizada en % (SL, TP y trailing) |
| Modo | `close`: decide al cierre de cada vela. `intrabar`: reevalúa cada pocos segundos con la vela todavía abierta y entra apenas todos coinciden |

`python scripts/live_tests.py indicators` lista las claves exactas.

## 2. Cómo se usa

```bash
python scripts/live_tests.py add --name "RF + EMA 15m" --timeframe 15m --indicators rf ema --mode intrabar
python scripts/live_tests.py run
python scripts/live_tests.py report
python scripts/live_tests.py stop 1
```

- `run` es el proceso que queda corriendo (una ventana de PowerShell abierta; `Ctrl+C` sale).
  Atiende todas las pruebas activas a la vez.
- `add`, `list`, `report` y `stop` se pueden usar con `run` andando: el corredor ve las pruebas
  nuevas y las detenidas en unos 10 segundos.
- `report` muestra por prueba: operaciones, PF, % de ganadoras, PnL neto, posiciones abiertas, las
  últimas operaciones y los últimos eventos (señales, entradas, rechazos), y si el corredor está
  andando.

## 3. Cómo opera

1. El corredor se suscribe al canal `trade` del WebSocket público de Bitunix
   (`wss://fapi.bitunix.com/public/`): cada operación del mercado con su precio y su hora.
2. Las velas **cerradas** vienen de REST (la misma fuente del backtest) y se reconcilian después de
   cada cierre. La vela **en curso** se arma con los ticks.
3. **Decisión:** la confluencia de `app/explorer/confluence.py`, el mismo código de la pestaña
   Indicadores. En modo `intrabar` la última fila es la vela abierta, así que los indicadores se
   evalúan en tiempo real. Como mucho una entrada por vela y por moneda.
4. **Entrada:** a mercado, con el precio del **tick siguiente** a la decision (nunca el de la
   decisión). Se guardan los dos precios para medir cuánto se pierde entre decidir y entrar.
5. **Salida, tick a tick:** el SL, el trailing y la liquidación se llenan al precio del tick que
   los cruza (si el mercado saltó, se sale donde se transó); el TP, en su nivel. Aquí no hay que
   suponer nada dentro del minuto: los ticks llegan en orden.
6. Comisiones, deslizamiento, margen, apalancamiento y tope de riesgo por operación son los de la
   configuración (los mismos del backtest). El funding se acumula con la tasa vigente.

## 4. Protecciones

- **Datos viejos:** si una moneda lleva más de 120 segundos sin ticks no se abren entradas en ella.
- **Reinicios:** las posiciones abiertas se guardan; al volver, el primer tick decide si el SL saltó
  durante la caída (y se sale a ese precio, no al del SL).
- **Detener una prueba** cierra sus posiciones con el último precio (motivo `MANUAL`).
- **Aislamiento:** archivos propios (`history/live_v3.db` y `history/ticks_v3.db`); los comandos
  rechazan archivos de la v1 y de la v2.

## 5. Grabación segundo a segundo

Mientras `run` está andando, cada operación del canal `trade` queda en `history/ticks_v3.db`
(hora en milisegundos, precio, cantidad y lado). Es la historia fina que Bitunix no entrega gratis
hacia atrás. Medido el 2026-11-11: unas 2 operaciones por segundo en BTC por este canal, del orden
de decenas de MB por día para 10 monedas. Con eso se podrá repetir más adelante cualquier
estrategia tick a tick sobre lo grabado. `run --no-ticks` no graba.

## 6. Límites honestos

- **Solo cuenta mientras el corredor está andando.** Si el PC se apaga, esas horas no se operan.
- **No es una ronda preregistrada.** Si se crean muchas pruebas y se mira solo la que ganó, es el
  mismo sobreajuste de siempre, hecho hacia adelante. Para decidir algo en serio: fijar de antemano
  cuántas operaciones se esperan y compararla con un control al azar (pendiente).
- **El canal `trade` no trae todas las operaciones** (llegan unas pocas por segundo): el precio
  puede tocar un nivel entre dos ticks sin que se vea.
- **Las velas en curso se arman con ticks**, así que el volumen de la vela abierta es parcial.
- **El LLM todavía no participa.** Se puede sumar como filtro en el momento de la señal (con su
  demora medida); conviene esperar el veredicto de la v1.

## 7. Lo que sigue

- Una pestaña del dashboard para crear, ver y detener las pruebas sin la línea de comandos.
- Una tarea programada `MinervaV3LiveTests` para que el corredor arranque solo con el PC.
- El control al azar por prueba y un criterio de revisión fijado al crearla.
- Repetición tick a tick sobre `history/ticks_v3.db`.
