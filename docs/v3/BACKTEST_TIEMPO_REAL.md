# Minerva v3: backtest en tiempo real (repetición minuto a minuto)

**Para qué sirve.** Probar estrategias que entran apenas se cumple la condición, sin esperar el
cierre de la vela, con la misma información que tendrían en vivo en cada momento. Es la pieza
central de la v3 y la base de todo lo que venga después (la cartera de fuerza relativa incluida).

## 1. Cómo funciona

El backtest de la v1 y la v2 (`app/backtesting/engine.py`) salta de cierre en cierre: evalúa la
señal con la vela cerrada y entra en la apertura de la vela siguiente. El de la v3
(`app/replay/engine.py`) **recorre la historia minuto a minuto** con las velas de 1 minuto de
Bitunix:

1. Con los minutos arma las velas del marco de la estrategia (por ejemplo 1h o 4h). Las cerradas
   se calculan una sola vez con sus indicadores. La vela en curso se arma con los minutos que van.
2. **En cada cierre de vela** la estrategia ve las velas cerradas y puede dejar una orden: a
   mercado, de **stop** (entrar si el precio cruza un nivel) o **límite**.
3. **En cada minuto** (si la estrategia lo pide con `uses_minutes = True`) ve además la vela en
   curso y el precio del último minuto terminado. Si decide entrar a mercado, se llena en la
   **apertura del minuto siguiente**: nunca con un precio que todavía no existía.
4. Las posiciones se vigilan minuto a minuto: SL, TP, trailing, liquidación, funding y salida por
   tiempo.

Una estrategia puede **mirar la vela todavía abierta** y recalcular sus indicadores con ella
(`MarketView.with_forming()`): eso se mide bien aquí, porque el motor solo le muestra lo que se
sabía en cada minuto. El problema de la señal que «desaparece» antes del cierre (repintado) solo
existe cuando se prueba con velas cerradas.

## 2. Reglas dentro del minuto

El archivo tiene una vela por minuto: se sabe dónde abrió, el máximo, el mínimo y dónde cerró,
pero no en qué orden pasaron. El motor siempre elige la lectura **menos favorable**:

| Situación | Regla |
|---|---|
| Orden de stop | Se dispara si el minuto toca el nivel. Se llena en el nivel, o en la apertura si el minuto ya abrió más allá (hueco). |
| Orden límite | Se llena solo si el minuto **atraviesa** el nivel, al nivel, con comisión maker y sin deslizamiento. |
| Salida | Primero el hueco en la apertura (sale a la apertura), después el lado adverso (SL o liquidación) y al final el TP. |
| SL y TP en el mismo minuto | **Se supone que vino primero el SL.** Se cuenta en `same_minute_sl_and_tp` para saber cuánto pesa. |
| Minuto de la entrada | Si entró a la apertura, se revisa el minuto completo. Si entró a mitad de minuto (stop o límite), solo el lado adverso. |
| Trailing | Se mueve con el extremo del minuto y dispara desde el minuto siguiente. |
| Orden sin vencimiento | Vale hasta el cierre de la vela siguiente; ahí la estrategia decide de nuevo. |
| Funding | Prorrateado por minuto con la tasa vigente. |
| Liquidación | Con el último precio (el archivo no trae el precio de marca); con el tope de riesgo el SL siempre queda antes. |

Comisiones, deslizamiento, tope de riesgo por operación y SL/TP de respaldo son los mismos de la
configuración y del motor de velas. Las operaciones salen como `RawTrade`, así que sirven todas las
métricas de la v2 (PF, PF estresado, concentración, folds).

## 3. Los datos

- **Fuente:** `GET /api/v1/futures/market/kline` con `interval=1m`, gratis y sin clave. Hay
  minutos desde **enero de 2023** (sondeado el 2026-11-11 con BTCUSDT y TRXUSDT).
- **Archivo:** `history/minute_v3.db`, aparte de la base del bot (unos 30 MB por moneda y por año).
- **Descarga:** `scripts/download_minutes.py`, incremental (solo baja lo que falta), cuatro monedas
  a la vez sin pasar el tope de pedidos de Bitunix, y repara los huecos con pedidos chicos. Medido:
  tres monedas, 41 días, 300 pedidos cada una, **0 huecos**, 2 minutos en total. Todo el universo
  desde 2025-01-01 son unas 2 horas la primera vez.
- **Hallazgo:** en Bitunix el `open` de un minuto es el cierre del anterior y en ~10 % de los
  minutos queda fuera del rango del minuto. Se amplía el máximo o el mínimo para incluirlo (como lo
  dibuja un gráfico de velas), en vez de descartar la vela.

## 4. Uso

```bash
python scripts/download_minutes.py --start 2025-01-01
python scripts/replay_backtest.py --strategy donchian --timeframe 1h --start 2025-06-01
```

`scripts/replay_backtest.py` corre las estrategias de ejemplo de `app/replay/examples.py`
(plantillas, no candidatas): `donchian` (órdenes de stop en el canal de 20 velas) y `forming`
(entra cuando el precio de la vela en curso supera el máximo previo). Velocidad medida: ~60 000
minutos en 0,5 s (`donchian`) y en 3 s (`forming`, que decide en cada minuto).

Para escribir una estrategia nueva: heredar de `ReplayStrategy` y definir `precompute`,
`on_bar_close` y/o `on_minute`, y `stop_price` / `take_profit_price` / `trailing_distance`.

## 5. Límites honestos y lo que sigue

- **Resolución:** el backtest ve el precio cada 1 minuto; en vivo el WebSocket lo entrega cada
  medio segundo. La diferencia se nota cuando el SL y el TP están muy cerca o en mercados muy
  rápidos. El contador `same_minute_sl_and_tp` dice cuántas veces el motor tuvo que suponer.
- **Pendiente:** grabar las operaciones del canal `trade` del WebSocket para comparar, desde ahora,
  lo que el motor supone dentro del minuto con lo que pasó de verdad.
- **Pendiente:** el funding real en `scripts/replay_backtest.py` (hoy sale con tasa 0, marcado como
  aproximado) y las métricas completas de la Fase 2 sobre la repetición.
- **Pendiente:** el mismo código en vivo. La estrategia ya recibe el precio como eventos; falta el
  conector que arme los minutos con el WebSocket (`market_kline_1min`) y llame a la misma
  estrategia en el bot paper.
