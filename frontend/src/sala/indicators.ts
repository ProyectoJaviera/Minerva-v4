// Indicadores del grafico de la Sala, con los mismos calculos que el backend de Minerva:
// - EMA de Pine (`pine_ema`): arranca con la media simple de los primeros `n` valores.
// - Cloud de Frost (`frost_cloud`): EMA del maximo (techo) y del minimo (piso).
// - Fibonacci de Frost (`frost_fibonacci`): zigzag de 23 velas y niveles entre los dos ultimos
//   pivotes fijos, `v1 + (v2 - v1) * r`.

import type { SalaCandle } from "../api";

export function ema(values: number[], n: number): (number | null)[] {
  const out: (number | null)[] = new Array(values.length).fill(null);
  const a = 2 / (n + 1);
  let sum = 0;
  let prev = 0;
  for (let i = 0; i < values.length; i++) {
    if (i < n - 1) {
      sum += values[i];
    } else if (i === n - 1) {
      sum += values[i];
      prev = sum / n;
      out[i] = prev;
    } else {
      prev = a * values[i] + (1 - a) * prev;
      out[i] = prev;
    }
  }
  return out;
}

export function frostCloud(candles: SalaCandle[], length = 50) {
  return { top: ema(candles.map((c) => c.h), length), bottom: ema(candles.map((c) => c.l), length) };
}

export const FIB_RATIOS = [0, 0.382, 0.5, 0.618, 0.812, 1, 1.25, 1.5];

export function frostFibonacci(candles: SalaCandle[], period = 23): { v1: number; v2: number; levels: [number, number][] } | null {
  const zz: [number, number][] = [];
  let dir = 0;
  let prev = 0;
  for (let i = period - 1; i < candles.length; i++) {
    let hh = -Infinity;
    let ll = Infinity;
    for (let j = i - period + 1; j <= i; j++) {
      hh = Math.max(hh, candles[j].h);
      ll = Math.min(ll, candles[j].l);
    }
    const ph = candles[i].h >= hh;
    const pl = candles[i].l <= ll;
    if (pl && !ph) dir = -1;
    if (ph && !pl) dir = 1;
    if (ph || pl) {
      const v = dir === 1 ? candles[i].h : candles[i].l;
      if (dir !== prev || zz.length === 0) {
        zz.unshift([v, i]);
        zz.length = Math.min(zz.length, 5);
      } else if (v * dir > zz[0][0] * dir) {
        zz[0] = [v, i];
      }
    }
    prev = dir;
  }
  if (zz.length < 3) return null;
  const v1 = zz[1][0];
  const v2 = zz[2][0];
  return { v1, v2, levels: FIB_RATIOS.map((r) => [r, v1 + (v2 - v1) * r]) };
}
