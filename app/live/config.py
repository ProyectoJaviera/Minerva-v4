"""La configuracion de una prueba en vivo: lo que el dueno elige a su criterio.

Una prueba combina indicadores y filtros de `app.explorer.confluence.INDICATORS` (los mismos de la
pestana Indicadores) con una salida de `EXITS` (o personalizada en %), sobre un marco y un grupo de
monedas. Dos modos:

- `close`: evalua la confluencia al cierre de cada vela del marco (como el backtest de velas) y
  entra en la operacion siguiente del mercado.
- `intrabar`: reevalua cada `eval_seconds` con la vela todavia abierta y entra apenas todos los
  indicadores coinciden, sin esperar el cierre (como mucho una entrada por vela y por moneda).
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass

from app.explorer.confluence import (
    DEFAULT_EXIT,
    DEFAULT_WINDOW,
    EXITS,
    INDICATORS,
    MAX_TP_PCT,
    MAX_WINDOW,
    PCT_EXIT,
    WARMUP_BY_TIMEFRAME,
    check_pct_exit,
)

MODES = ("close", "intrabar")
MIN_EVAL_SECONDS = 5
MAX_EVAL_SECONDS = 3600
MAX_SYMBOLS = 30
DIRECTIONS = ("both", "long", "short")
MIN_MARGIN_USDT = 1.0
MAX_MARGIN_USDT = 100_000.0
# Bitunix ofrece hasta 125x en algunos contratos y menos en otros; el maximo real de cada simbolo no
# esta verificado aqui, asi que se acota a 100x.
MAX_LEVERAGE = 100
MAX_TP_LEVELS = 3


@dataclass(frozen=True)
class TestConfig:
    """Una prueba en vivo.

    Attributes:
        name: nombre que le pone el dueno.
        symbols: monedas (p. ej. `BTCUSDT`).
        timeframe: marco de las velas (`15m`, `1h`, `4h`, `1d` o `1w`).
        indicators: ids de `INDICATORS`; se entra cuando coinciden.
        mode: `close` o `intrabar`.
        exit_name: clave de `EXITS`, o `pct` con `pct_exit`.
        pct_exit: `(sl, tp, trailing)` en % del precio de entrada (solo con `exit_name = pct`).
        window: velas que dura el voto de un indicador de evento.
        min_agree: cuantos indicadores deben coincidir (`None` = todos).
        eval_seconds: cada cuanto se reevalua en modo `intrabar`.
        margin_usdt: margen por operacion en USDT (`None` = `DEFAULT_MARGIN_USDT`).
        leverage: apalancamiento (`None` = `LEVERAGE`).
        direction: `both`, `long` (solo largos) o `short` (solo cortos).
        take_profits: TP parciales `((distancia % desde la entrada, % de la cantidad inicial que
            cierra), ...)`, hasta 3, de menor a mayor distancia; reemplazan al TP de la salida.
        breakeven: tras el primer TP parcial el SL pasa al precio de entrada.
        max_open: maximo de posiciones abiertas a la vez en la prueba (`None` = sin tope).
    """
    __test__ = False  # no es una clase de pruebas de pytest

    name: str
    symbols: tuple[str, ...]
    timeframe: str
    indicators: tuple[str, ...]
    mode: str = "close"
    exit_name: str = DEFAULT_EXIT
    pct_exit: tuple[float, float | None, float | None] | None = None
    window: int = DEFAULT_WINDOW
    min_agree: int | None = None
    eval_seconds: int = 15
    margin_usdt: float | None = None
    leverage: int | None = None
    direction: str = "both"
    take_profits: tuple[tuple[float, float], ...] = ()
    breakeven: bool = False
    max_open: int | None = None

    def to_json(self) -> str:
        """La configuracion como JSON (para guardarla).

        Returns:
            El texto JSON.
        """
        return json.dumps(asdict(self), ensure_ascii=False, sort_keys=True)

    @classmethod
    def from_json(cls, text: str) -> TestConfig:
        """Reconstruye y valida una configuracion guardada.

        Args:
            text: JSON de `to_json`.

        Returns:
            La configuracion validada.

        Raises:
            ValueError: si el JSON no describe una prueba valida.
        """
        raw = json.loads(text)
        return validate(
            name=raw.get("name", ""), symbols=raw.get("symbols", ()),
            timeframe=raw.get("timeframe", ""), indicators=raw.get("indicators", ()),
            mode=raw.get("mode", "close"), exit_name=raw.get("exit_name", DEFAULT_EXIT),
            pct_exit=raw.get("pct_exit"), window=raw.get("window", DEFAULT_WINDOW),
            min_agree=raw.get("min_agree"), eval_seconds=raw.get("eval_seconds", 15),
            margin_usdt=raw.get("margin_usdt"), leverage=raw.get("leverage"),
            direction=raw.get("direction", "both"), take_profits=raw.get("take_profits") or (),
            breakeven=bool(raw.get("breakeven", False)), max_open=raw.get("max_open"),
        )


def validate(*, name: str, symbols, timeframe: str, indicators, mode: str = "close",
             exit_name: str = DEFAULT_EXIT, pct_exit=None, window: int = DEFAULT_WINDOW,
             min_agree: int | None = None, eval_seconds: int = 15,
             margin_usdt: float | None = None, leverage: int | None = None,
             direction: str = "both", take_profits=(), breakeven: bool = False,
             max_open: int | None = None) -> TestConfig:
    """Valida lo que eligio el dueno y devuelve la prueba.

    Args:
        name: nombre (1 a 60 caracteres).
        symbols: monedas terminadas en `USDT` (1 a `MAX_SYMBOLS`).
        timeframe: marco.
        indicators: ids de `INDICATORS` (al menos uno).
        mode: `close` o `intrabar`.
        exit_name: clave de `EXITS` o `pct`.
        pct_exit: `(sl, tp, trailing)` en % para `pct`.
        window: 1 a `MAX_WINDOW`.
        min_agree: 1 a la cantidad de indicadores, o `None`.
        eval_seconds: `MIN_EVAL_SECONDS` a `MAX_EVAL_SECONDS`.
        margin_usdt: `MIN_MARGIN_USDT` a `MAX_MARGIN_USDT`, o `None`.
        leverage: 1 a `MAX_LEVERAGE`, o `None`.
        direction: uno de `DIRECTIONS`.
        take_profits: hasta `MAX_TP_LEVELS` pares `(distancia %, % que cierra)`; distancias
            crecientes entre 0 y `MAX_TP_PCT`, y los porcentajes suman como mucho 100.
        breakeven: solo tiene sentido con TP parciales.
        max_open: 1 a `MAX_SYMBOLS`, o `None`.

    Returns:
        La configuracion normalizada (monedas en mayusculas y sin repetir, indicadores en el
        orden de `INDICATORS`).

    Raises:
        ValueError: con el motivo, si algo no es valido.
    """
    name = (name or "").strip()
    if not 1 <= len(name) <= 60:
        raise ValueError("el nombre debe tener entre 1 y 60 caracteres")
    syms = tuple(dict.fromkeys(str(s).strip().upper() for s in symbols))
    if not syms or len(syms) > MAX_SYMBOLS:
        raise ValueError(f"elige entre 1 y {MAX_SYMBOLS} monedas")
    bad = [s for s in syms if not (s.endswith("USDT") and s[:-4].isalnum() and len(s) <= 20)]
    if bad:
        raise ValueError(f"monedas invalidas: {bad}")
    if timeframe not in WARMUP_BY_TIMEFRAME:
        raise ValueError(f"marco invalido: {timeframe} (usa {', '.join(WARMUP_BY_TIMEFRAME)})")
    chosen = set(indicators)
    unknown = sorted(chosen - set(INDICATORS))
    if not chosen or unknown:
        raise ValueError(f"indicadores invalidos: {unknown or 'ninguno elegido'}")
    inds = tuple(i for i in INDICATORS if i in chosen)
    if mode not in MODES:
        raise ValueError(f"modo invalido: {mode} (usa close o intrabar)")
    pct = None
    if exit_name == PCT_EXIT:
        pct = check_pct_exit(*(pct_exit or (None, None, None)))
    elif exit_name not in EXITS:
        raise ValueError(f"salida desconocida: {exit_name}")
    if not 1 <= int(window) <= MAX_WINDOW:
        raise ValueError(f"ventana fuera de rango: {window}")
    if min_agree is not None and not 1 <= int(min_agree) <= len(inds):
        raise ValueError(f"minimo de coincidencias fuera de rango: {min_agree}")
    if not MIN_EVAL_SECONDS <= int(eval_seconds) <= MAX_EVAL_SECONDS:
        raise ValueError(f"eval_seconds debe estar entre {MIN_EVAL_SECONDS} y {MAX_EVAL_SECONDS}")
    if margin_usdt is not None and not MIN_MARGIN_USDT <= float(margin_usdt) <= MAX_MARGIN_USDT:
        raise ValueError(
            f"el margen debe estar entre {MIN_MARGIN_USDT:g} y {MAX_MARGIN_USDT:g} USDT")
    if leverage is not None and not 1 <= int(leverage) <= MAX_LEVERAGE:
        raise ValueError(f"el apalancamiento debe estar entre 1 y {MAX_LEVERAGE}")
    if direction not in DIRECTIONS:
        raise ValueError(f"direccion invalida: {direction} (usa both, long o short)")
    tps = tuple((float(d), float(c)) for d, c in (take_profits or ()))
    if len(tps) > MAX_TP_LEVELS:
        raise ValueError(f"como mucho {MAX_TP_LEVELS} niveles de TP")
    last = 0.0
    for dist, close in tps:
        if not last < dist <= MAX_TP_PCT:
            raise ValueError("las distancias de los TP deben ser positivas y crecientes")
        if not 0 < close <= 100:
            raise ValueError("el % que cierra cada TP debe estar entre 0 y 100")
        last = dist
    if sum(c for _, c in tps) > 100 + 1e-9:
        raise ValueError("los % que cierran los TP suman mas de 100")
    if breakeven and not tps:
        raise ValueError("mover el SL a la entrada necesita al menos un TP parcial")
    if max_open is not None and not 1 <= int(max_open) <= MAX_SYMBOLS:
        raise ValueError(f"el maximo de posiciones debe estar entre 1 y {MAX_SYMBOLS}")
    return TestConfig(
        name=name, symbols=syms, timeframe=timeframe, indicators=inds, mode=mode,
        exit_name=exit_name, pct_exit=pct, window=int(window),
        min_agree=None if min_agree is None else int(min_agree), eval_seconds=int(eval_seconds),
        margin_usdt=None if margin_usdt is None else float(margin_usdt),
        leverage=None if leverage is None else int(leverage), direction=direction,
        take_profits=tps, breakeven=bool(breakeven),
        max_open=None if max_open is None else int(max_open),
    )
