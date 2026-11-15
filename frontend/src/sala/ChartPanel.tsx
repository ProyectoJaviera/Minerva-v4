import {
  type CandlestickData,
  type IChartApi,
  type IPriceLine,
  type ISeriesApi,
  LineStyle,
  type UTCTimestamp,
  createChart,
} from "lightweight-charts";
import { useEffect, useRef } from "react";
import type { SalaCandle, SalaOrder, SalaPosition } from "../api";
import { ema, frostCloud, frostFibonacci } from "./indicators";

// Grafico de velas de la Sala con Lightweight Charts de TradingView (Apache 2.0; deja visible su
// logo de atribucion). Zoom con la rueda y desplazamiento arrastrando, como en un exchange.

export interface ChartOverlays {
  ema9: boolean;
  ema21: boolean;
  cloud: boolean;
  fib: boolean;
  positions: boolean;
}

const toTime = (ms: number) => Math.floor(ms / 1000) as UTCTimestamp;
const isDark = () => document.documentElement.classList.contains("dark");

function colors() {
  const dark = isDark();
  return {
    bg: dark ? "#111827" : "#ffffff",
    text: dark ? "#9ca3af" : "#4b5563",
    grid: dark ? "rgba(229,231,235,0.06)" : "rgba(17,24,39,0.06)",
    up: dark ? "#10b981" : "#059669",
    down: dark ? "#f43f5e" : "#dc2626",
  };
}

export function ChartPanel({
  candles,
  price,
  overlays,
  positions,
  orders,
  symbol,
}: {
  candles: SalaCandle[];
  price: number | null;
  overlays: ChartOverlays;
  positions: SalaPosition[];
  orders: SalaOrder[];
  symbol: string;
}) {
  const box = useRef<HTMLDivElement>(null);
  const chart = useRef<IChartApi | null>(null);
  const series = useRef<{
    candles: ISeriesApi<"Candlestick">;
    ema9: ISeriesApi<"Line">;
    ema21: ISeriesApi<"Line">;
    top: ISeriesApi<"Line">;
    bottom: ISeriesApi<"Line">;
  } | null>(null);
  const lines = useRef<IPriceLine[]>([]);
  const last = useRef<CandlestickData | null>(null);

  // crear el grafico una vez y seguir el tema claro/oscuro
  useEffect(() => {
    if (!box.current) return;
    const c = colors();
    const ch = createChart(box.current, {
      autoSize: true,
      layout: { background: { color: c.bg }, textColor: c.text, fontFamily: "Inter, system-ui, sans-serif" },
      grid: { vertLines: { color: c.grid }, horzLines: { color: c.grid } },
      timeScale: { timeVisible: true, secondsVisible: false, rightOffset: 6 },
      rightPriceScale: { borderVisible: false },
      crosshair: { mode: 0 },
    });
    const line = (color: string) => ch.addLineSeries({ color, lineWidth: 1, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false });
    series.current = {
      candles: ch.addCandlestickSeries({ upColor: c.up, downColor: c.down, borderVisible: false, wickUpColor: c.up, wickDownColor: c.down }),
      ema9: line("#f59e0b"),
      ema21: line("#a855f7"),
      top: line("rgba(100,116,139,0.85)"),
      bottom: line("rgba(100,116,139,0.85)"),
    };
    chart.current = ch;
    const obs = new MutationObserver(() => {
      const k = colors();
      ch.applyOptions({ layout: { background: { color: k.bg }, textColor: k.text }, grid: { vertLines: { color: k.grid }, horzLines: { color: k.grid } } });
      series.current?.candles.applyOptions({ upColor: k.up, downColor: k.down, wickUpColor: k.up, wickDownColor: k.down });
    });
    obs.observe(document.documentElement, { attributes: true, attributeFilter: ["class"] });
    return () => {
      obs.disconnect();
      ch.remove();
      chart.current = null;
      series.current = null;
    };
  }, []);

  // datos e indicadores
  useEffect(() => {
    const s = series.current;
    if (!s) return;
    const data = candles.map((b) => ({ time: toTime(b.t), open: b.o, high: b.h, low: b.l, close: b.c }));
    s.candles.setData(data);
    last.current = data.length ? { ...data[data.length - 1] } : null;
    const closes = candles.map((b) => b.c);
    const toLine = (vals: (number | null)[]) =>
      vals.flatMap((v, i) => (v === null ? [] : [{ time: toTime(candles[i].t), value: v }]));
    s.ema9.setData(overlays.ema9 ? toLine(ema(closes, 9)) : []);
    s.ema21.setData(overlays.ema21 ? toLine(ema(closes, 21)) : []);
    const cloud = frostCloud(candles, 50);
    s.top.setData(overlays.cloud ? toLine(cloud.top) : []);
    s.bottom.setData(overlays.cloud ? toLine(cloud.bottom) : []);
  }, [candles, overlays.ema9, overlays.ema21, overlays.cloud]);

  // al cambiar de moneda, encuadrar
  useEffect(() => {
    chart.current?.timeScale().fitContent();
  }, [symbol]);

  // el ultimo precio mueve la vela en curso
  useEffect(() => {
    const s = series.current;
    const l = last.current;
    if (!s || !l || price === null) return;
    const next = { ...l, close: price, high: Math.max(l.high, price), low: Math.min(l.low, price) };
    last.current = next;
    s.candles.update(next);
  }, [price]);

  // Fibonacci, posiciones y ordenes como lineas de precio
  useEffect(() => {
    const s = series.current;
    if (!s) return;
    lines.current.forEach((pl) => s.candles.removePriceLine(pl));
    lines.current = [];
    const add = (p: number, color: string, title: string, style = LineStyle.Dashed) =>
      lines.current.push(s.candles.createPriceLine({ price: p, color, lineWidth: 1, lineStyle: style, axisLabelVisible: true, title }));
    if (overlays.fib) {
      const f = frostFibonacci(candles);
      f?.levels.forEach(([r, p]) =>
        add(p, r > 1 ? "#06b6d4" : r === 0.618 ? "#10b981" : "#94a3b8", `${+(r * 100).toFixed(1)} %`, LineStyle.Dotted),
      );
    }
    if (overlays.positions) {
      positions.filter((p) => p.symbol === symbol).forEach((p) => {
        add(p.entry_price, "#3b82f6", `${p.side === "LONG" ? "Largo" : "Corto"} ${p.leverage}x`, LineStyle.Solid);
        if (p.tp) add(p.tp, "#10b981", "TP");
        (p.tp_levels ?? []).forEach(([lp, , hit], k) => {
          if (!hit) add(lp, "#10b981", `TP${k + 1}`);
        });
        if (!p.no_sl) add(p.stop, "#f43f5e", p.trailing ? "Trailing" : "SL");
        add(p.liq, "#f59e0b", "Liq.", LineStyle.Dotted);
      });
      orders.filter((o) => o.symbol === symbol && o.price).forEach((o) =>
        add(o.price as number, "#9ca3af", `Límite ${o.side === "LONG" ? "largo" : "corto"}`),
      );
    }
  }, [candles, overlays.fib, overlays.positions, positions, orders, symbol]);

  return <div ref={box} className="h-full min-h-0 w-full flex-1" aria-label={`Gráfico de velas de ${symbol}`} />;
}
