# Minerva v3: plan (2026-11-11)

**Estado: borrador para revisión del dueño.** Nada de lo que hay aquí está preregistrado. Las decisiones que
cambian el resultado de una corrida se fijarán en un preregistro aparte antes de mirar datos fuera de muestra.

## 1. Decisiones ya tomadas por el dueño (2026-11-11)

- **Repositorio nuevo y aislado (Minerva-v3)**, nacido del commit `c242f2b` de la v2. La v2 sigue con su prueba
  hacia adelante y su captura de datos sin cambios; la v1 sigue midiendo en el netbook.
- **Pregunta de investigación: opción C**, una cartera de fuerza relativa entre monedas.
- **Datos en tiempo real de Bitunix** para poder entrar apenas se cumplan las condiciones, sin esperar siempre el
  cierre de la vela.

## 2. Por qué la cartera de fuerza relativa

En las cuatro rondas de la v2 (unas 400 señales) nada pasó los criterios de la Fase 2 con selección honesta. La única
idea que se comportó igual en los dos periodos fue la **fuerza relativa semanal top 3 / bottom 3** de la ronda 4:
PF 1.11 en IS y 1.25 en OOS, ganadora en 6 de 10 monedas y en el 67 % de los folds, y por encima de su control al
azar (1.06). Falló solo por **concentración**: pocas operaciones explicaban el 57 % de la ganancia.

La v2 la trató como señales sueltas, con SL de 2.5 ATR por operación y el tope de riesgo del 5 %, que dejaba operar
casi solo en las monedas grandes. Una **cartera** reparte el capital entre varias monedas a la vez y rebalancea con
reglas fijas: ataca justo la concentración y deja que la ventaja (si existe) venga de muchas apuestas pequeñas.

Advertencias honestas:
- La v2 ya miró el periodo hasta el 2026-10-01 con esta idea y los datos reservados (2026-10-01 a 2026-11-10) se
  usaron en la ronda 3. **El examen final de la v3 solo puede ser hacia adelante**, con datos posteriores al
  preregistro.
- Con 10 monedas la cartera es chica. Ampliar el universo (p. ej. top 20 o top 30 de Bitunix por volumen) reduce la
  concentración, pero hay que verificar que el historial de velas exista y sea gratis para cada símbolo.

## 3. La cartera (a definir en el preregistro)

| Pieza | Propuesta inicial | Alternativas a explorar solo en IS |
|---|---|---|
| Universo | top 10 de la v2 | top 20 / top 30 por volumen de Bitunix |
| Señal | retorno de 1 semana (42 velas de 4h) | 2 y 4 semanas; retorno ajustado por volatilidad |
| Lados | largo en los 3 más fuertes, corto en los 3 más débiles | solo largos; neutral al mercado (BTC como cobertura) |
| Pesos | parejos | inversos a la volatilidad |
| Rebalanceo | semanal, lunes 00:00 UTC | cada 3 días; banda de no-cambio para ahorrar comisiones |
| Riesgo | apalancamiento total acotado; stop de cartera por drawdown | stop por posición en ATR |
| Costos | comisiones taker/maker de Bitunix, deslizamiento estresado y funding real cada 8 h | — |
| Control | la misma cartera con ranking al azar (varias semillas) | — |

Criterios: los de la Fase 2 sin relajar (PF OOS ≥ 1.2, PF estresado, concentración, folds), calculados sobre
**períodos de rebalanceo** además de por operación, y la cartera tiene que superar a su control al azar.

## 4. Datos en tiempo real (verificado en la documentación oficial el 2026-11-11)

WebSocket público `wss://fapi.bitunix.com/public/`. Lo que ya usa el código heredado: el canal `tickers`, que el
monitor de posiciones aprovecha para evaluar SL, TP, trailing y liquidación en cada tick. Canales documentados que
suma la v3:

| Canal | Qué trae | Uso en la v3 |
|---|---|---|
| `market_kline_1min` (y otros intervalos, también `mark_kline_*`) | vela en curso o/h/l/c y volumen, cada 500 ms | precio vivo y armado local de velas |
| `trade` | cada operación ejecutada: hora, precio, cantidad y lado | disparo exacto al cruzar un nivel; flujo comprador/vendedor |
| `tickers` | último precio, mejor compra y venta con sus cantidades | spread y llenado realista de órdenes límite |
| `price` | precio de marca, índice, funding vigente y próximo cobro | liquidación y funding con el precio de marca |
| `depth_book1/5/15`, `depth_books` | libro de órdenes | opcional (ya lo graba la v2 cada 5 min por REST) |

Límites documentados: **5 mensajes por segundo** por conexión (incluidos ping y suscripciones; pasarse corta la
conexión y repetirlo puede bloquear la IP) y **300 suscripciones por conexión**. No documentados (hay que medirlos en
vivo antes de depender de ellos): intervalo de ping exigido, confirmación de suscripción, si la vela de 1 minuto trae
una marca de cierre y la frecuencia de `trade` y `depth`.

### Backtest en tiempo real

El dueño pidió (2026-11-11) que la v3 pueda hacer backtest con el precio en tiempo real, sin esperar
los cierres de vela, y que sea su foco. Está en [BACKTEST_TIEMPO_REAL.md](BACKTEST_TIEMPO_REAL.md):
la historia se recorre minuto a minuto con las velas de 1 minuto de Bitunix (gratis desde enero de
2023), la estrategia puede decidir dentro de la vela (también mirando la vela abierta, porque solo
ve lo que se sabía en cada minuto) y el motor elige siempre la lectura menos favorable dentro del
minuto.

## 5. Fases

1. **Fase 0, aislamiento (hecha):** nombres propios, puerto 8002, candados contra la v1 y la v2.
2. **Fase 1, backtest en tiempo real (hecha la base):** archivo de minutos, descarga incremental y
   motor de repetición minuto a minuto (`app/replay/`), con pruebas y ejemplos.
3. **Fase 1b, feed en tiempo real:** cliente WebSocket multicanal (sobre `app/market/bitunix_ws.py`), con
   reconexión, salud por canal y bloqueo de nuevas entradas si el feed está viejo; velas de 1 minuto armadas
   localmente y comparadas con las de REST; grabación opcional de `trade` y `tickers`.
4. **Fase 2, motor de cartera en backtest:** ranking, pesos, rebalanceo, costos y funding; sobre el motor de
   repetición minuto a minuto; exploración solo en IS para elegir la variante.
5. **Fase 3, preregistro:** reglas, universo, criterios y control congelados en un commit propio, con la
   aprobación del dueño.
6. **Fase 4, paper hacia adelante en tiempo real:** el bot de la v3 opera la cartera congelada con el feed vivo
   desde la fecha del preregistro, con una sola revisión al llegar al tamaño de muestra fijado.

## 6. Pendientes del dueño

- Crear el repositorio `ProyectoJaviera/Minerva-v3` en GitHub (o autorizar que se cree) y decidir si es público,
  como la v1 y la v2, o privado.
- Revisar este plan, sobre todo el universo (¿ampliar más allá del top 10?) y los lados (¿largo y corto, o solo
  largo?).
