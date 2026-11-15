# Sala de trading manual (v4)

La pestaña **«Sala de trading»** permite operar a mano, como en un exchange, pero en **paper**: el precio es el real
de Bitunix (WebSocket público) y ninguna orden sale de Minerva. La cuenta manual está **separada** de las pruebas
«En vivo», de su gemelo al azar y de la medición: lo que hagas aquí no cambia ninguna de ellas.

## Requisitos

- El corredor de «En vivo» encendido en la instancia (`LIVE_TESTS_ENABLED=true`, como en el contenedor de la v4) y el
  bot encendido desde la pestaña «En vivo». Con el bot apagado la sala muestra lo guardado pero no recibe precios ni
  opera; las posiciones manuales siguen abiertas y se vuelven a vigilar al encenderlo.

## La pantalla

En la posición se ven los TP parciales (los ya tocados, tachados y con ✓), el SL (o «Trailing» y su nivel actual
si tiene trailing, o «SL en entrada» tras el TP1) y en el gráfico las líneas TP1/TP2/TP3 pendientes y la del stop.

Siete paneles flotantes: **Gráfico**, **Libro de órdenes**, **Operaciones del mercado**, **Monedas**, **Crear
operación**, **Cuenta paper manual** y **Posiciones y órdenes**.

- **Mover:** arrastra la barra del título. **Cambiar el tamaño:** tira de un borde o de una esquina.
- **Ocultar:** la × de cada panel. **Volver a mostrarlo:** botón «Paneles ▾» (también «Restablecer disposición» y
  «Mostrar todos los paneles»).
- La disposición queda guardada en tu navegador. En el celular los paneles se apilan en una columna.
- El gráfico (Lightweight Charts de TradingView) admite zoom con la rueda y desplazamiento arrastrando, y muestra a
  pedido EMA 9, EMA 21, Cloud 50, Fibonacci y tus posiciones (entrada, TP, SL y liquidación).

## Cómo opera

| Qué | Regla |
|---|---|
| Orden a mercado | Se llena con el primer precio posterior a la orden, con comisión taker (0,06 %) y deslizamiento (5 pb). |
| Orden límite | Un largo se llena cuando el precio baja hasta el límite (un corto, cuando sube), al precio límite y sin deslizamiento. Si ya estaba cruzada al crearla, se llena con el primer precio. |
| TP y SL | En % del precio de entrada, opcionales. Un SL más lejos que la liquidación se rechaza. |
| TP parciales | Hasta 3 niveles: distancia en % y % de la cantidad inicial que cierra cada uno (los cierres no pueden sumar más del 100 %). Si suman menos, el resto sigue con el SL o el trailing. Con «Mover el SL a la entrada al tocarse el TP1», el stop pasa al precio de entrada cuando se toca el primero. Cada cierre parcial paga su comisión y deslizamiento. |
| Trailing stop | En % del precio de entrada (como distancia fija en precio): el stop sigue al mejor precio desde la entrada y nunca retrocede; cierra al precio del tick que lo toca. Se puede combinar con SL fijo y TP. Más lejos que la liquidación se rechaza. |
| Sin SL | Solo sale por TP, a mano o por liquidación. |
| Liquidación | Margen aislado: se pierde a lo sumo el margen (más la comisión de entrada), aunque el precio salte. |
| Funding | Se cobra o paga con la tasa vigente de Bitunix, como en las pruebas. |
| Cerrar / Cerrar 50 % | Con el último precio (necesita uno de menos de 2 minutos), con comisión y deslizamiento. |
| SL a entrada | Mueve el stop al precio de entrada; solo si la posición va ganando. |
| Cerrar todo | Pide escribir `CERRAR`. |
| Reiniciar | Vuelve a 1 000 USDT (pide `REINICIAR`); borra posiciones y órdenes sin cerrarlas y conserva el historial. |

La cuenta se guarda en las tablas `manual_*` del archivo de pruebas (`history/live_v4.db`). El libro de órdenes sale
del REST público de profundidad de Bitunix (50 niveles, actualizado cada 1 a 2 s).
