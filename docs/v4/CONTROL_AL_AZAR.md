# Minerva v4: el gemelo de control al azar de cada prueba en vivo

**Para qué sirve.** Una prueba «En vivo» puede ganar o perder por pura suerte. Para saber si una estrategia aporta algo,
cada prueba tiene un **gemelo** que hace lo mismo **menos elegir cuándo entrar**: entra al azar. Si la prueba no le gana
a su gemelo, sus resultados no se distinguen de la suerte. Todo es paper, hacia adelante y sin tocar la estrategia.

## 1. Qué comparte el gemelo con la prueba

| Igual que la prueba | Distinto |
|---|---|
| monedas, marco, modo (`close` o `intrabar`) | **cuándo** entra: sorteado |
| margen, apalancamiento, tope de riesgo por operación | **la dirección**: sorteada 50/50 (o la única permitida si la prueba solo opera largos o cortos) |
| salida: SL, TP y trailing (en ATR(14) o en %), TP parciales, SL a la entrada tras el TP1 | |
| máximo de posiciones abiertas, una entrada por vela y por moneda | |
| llenado en el tick siguiente, comisiones, deslizamiento, funding, liquidación | |

Se llama `Azar: <nombre de la prueba>`, nace con la prueba (desde el dashboard o con `live_tests.py add`) y se detiene
con ella. No se puede detener solo.

## 2. Cómo sortea

1. Cada vez que cierra una vela de una moneda, el gemelo mira la **frecuencia** de la prueba real hasta ese momento:
   `señales / velas elegibles`. Una vela es elegible si la prueba podía entrar en esa moneda (sin posición ni entrada
   pendiente y con ticks recientes).
2. Entra con esa probabilidad. Mientras la prueba no dio ninguna señal, el gemelo no entra. El sorteo usa solo lo que se
   sabía **antes** de esa vela.
3. En modo `close` entra al cierre de la vela, como la prueba. En modo `intrabar` espera un instante al azar dentro de la
   vela y entra con la vela todavía abierta.
4. Después, todo sigue el mismo camino que una entrada de la prueba (tick siguiente, mismos costos y salidas).

## 3. Cómo se lee

La tarjeta **«Contra el azar»** de cada prueba (pestaña «En vivo») muestra las operaciones, el PF y el resultado del
gemelo, la diferencia de resultado medio por operación (prueba menos gemelo) y un veredicto:

| Veredicto | Cuándo |
|---|---|
| Faltan operaciones para decir algo | menos de 30 operaciones cerradas en la prueba o en el gemelo |
| Le gana al azar | prueba de Welch de una cola, t > 1,645 (5 %) |
| Pierde contra el azar | t < −1,645 |
| No se distingue del azar | lo demás |

Lo mismo sale en `python scripts/live_tests.py report`.

## 4. Límites honestos

- **Es una sola tirada de la suerte.** Un gemelo es un sorteo; con muchas pruebas a la vez, alguna le ganará a su gemelo
  por azar (5 % cada una). Para decidir algo en serio: fijar la prueba y el número de operaciones antes de mirar.
- **La frecuencia se aprende sobre la marcha.** Al principio el gemelo puede entrar más o menos que la prueba; con el
  tiempo se acercan. Las operaciones de ambos se cuentan igual.
- **Velas elegibles aproximadas en `intrabar`.** La elegibilidad se cuenta al empezar la vela; si la prueba sale y vuelve
  a entrar dentro de la misma vela, la frecuencia sube un poco.
- **La entrada pendiente del gemelo no sobrevive a un reinicio** (igual que la de la prueba); sus posiciones sí.
- No es una ronda preregistrada ni cambia ninguna estrategia: solo mide.

## 5. Dónde está

- Lógica pura: `app/live/control.py` (sorteo, salida, comparación).
- Corredor: `app/live/runner.py` (`_control_step`, `_decide_control`).
- Archivo: `app/live/store.py` (`live_tests.control_of`, tabla `live_control_rates`).
- Resumen y API: `app/live/report.py` (`control`, `rate`, `vs_control`), `GET /api/live/tests`.
- Pantalla: `frontend/src/LiveTab.tsx` (`ControlPanel`).
- Pruebas: `tests/unit/test_live_control.py`, `tests/unit/test_v3_isolation.py`.
