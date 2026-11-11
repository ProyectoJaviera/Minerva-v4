"""Backtest en tiempo real de la v3: repeticion minuto a minuto.

Diseno en docs/v3/BACKTEST_TIEMPO_REAL.md.

- `minute_store`: archivo SQLite con las velas de 1 minuto de Bitunix (`history/minute_v3.db`).
- `candles`: arma las velas del marco de la estrategia (cerradas y en curso) con los minutos.
- `engine`: recorre los minutos y deja que la estrategia entre dentro de la vela.
"""
