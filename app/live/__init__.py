"""Pruebas en vivo de la v3: paper trading hacia adelante con el precio en tiempo real.

Diseno en docs/v3/PRUEBAS_EN_VIVO.md.

- `config`: lo que el dueno elige para una prueba (monedas, marco, indicadores, salida, modo).
- `paper`: la posicion paper evaluada en cada operacion real del mercado (tick).
- `book`: las velas de cada moneda (cerradas por REST, la que esta en curso con los ticks).
- `store`: el archivo de pruebas y resultados (`history/live_v4.db`) y el de ticks.
- `runner`: el proceso que escucha el WebSocket publico y opera todas las pruebas activas.
"""
