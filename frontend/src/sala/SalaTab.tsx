import { useMemo, useState } from "react";
import {
  ApiError,
  type SalaPosition,
  type SalaState,
  useSalaBook,
  useSalaBreakeven,
  useSalaCancelOrder,
  useSalaCandles,
  useSalaCloseAll,
  useSalaClosePosition,
  useSalaPlaceOrder,
  useSalaReset,
  useSalaState,
} from "../api";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { ChartPanel, type ChartOverlays } from "./ChartPanel";
import { FloatingDesk, PanelsMenu } from "./FloatingDesk";
import { type Layout, loadLayout, saveLayout } from "./layout";

// Pestana «Sala de trading» (v4): trading manual en paper con el precio real de Bitunix, en un
// escritorio de paneles flotantes. La cuenta manual esta separada de las pruebas «En vivo» y de la
// medicion. Nunca se envian ordenes reales.

export const SALA_SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT", "BNBUSDT",
  "HYPEUSDT", "TRXUSDT", "XMRUSDT", "ZECUSDT"];
const INTERVALS = ["1m", "5m", "15m", "1h", "4h", "1d"];

function decimals(p: number | null | undefined): number {
  if (!p) return 2;
  if (p >= 1000) return 1;
  if (p >= 10) return 2;
  if (p >= 1) return 4;
  return 5;
}
const fp = (v: number | null | undefined, ref?: number | null) =>
  v === null || v === undefined || !Number.isFinite(v) ? "-" : v.toLocaleString("en-US", { minimumFractionDigits: decimals(ref ?? v), maximumFractionDigits: decimals(ref ?? v) });
const fs = (v: number | null | undefined, d = 2, unit = "") =>
  v === null || v === undefined ? "-" : `${v >= 0 ? "+" : ""}${v.toFixed(d)}${unit}`;
const pnl = (v: number | null | undefined) =>
  v === null || v === undefined || v === 0 ? "text-gray-800 dark:text-gray-200" : v > 0 ? "text-green-700 dark:text-green-400" : "text-red-700 dark:text-red-400";
const num = (v: unknown): number | null => {
  const n = typeof v === "number" ? v : typeof v === "string" ? Number(v) : NaN;
  return Number.isFinite(n) ? n : null;
};
function errorText(e: unknown): string {
  if (e instanceof ApiError && e.detail) return e.detail;
  return "No se pudo completar (error del servidor).";
}
function age(ms: number, now: number): string {
  const s = Math.max(0, Math.round((now - ms) / 1000));
  if (s < 60) return `${s} s`;
  const m = Math.floor(s / 60);
  return m < 60 ? `${m} min` : `${Math.floor(m / 60)} h ${m % 60} min`;
}

const field = "w-full rounded border border-gray-300 bg-gray-50 px-2 py-1.5 font-mono text-sm dark:border-gray-600 dark:bg-gray-800 dark:text-gray-100";

function OrderForm({ symbol, price, state }: { symbol: string; price: number | null; state: SalaState | undefined }) {
  const place = useSalaPlaceOrder();
  const [kind, setKind] = useState<"market" | "limit">("market");
  const [leverage, setLeverage] = useState(10);
  const [margin, setMargin] = useState("10");
  const [limit, setLimit] = useState("");
  const [tpMode, setTpMode] = useState<"single" | "partial" | "none">("single");
  const [tp, setTp] = useState("2");
  const [levels, setLevels] = useState<{ dist: string; close: string }[]>([
    { dist: "1.5", close: "50" },
    { dist: "3", close: "50" },
  ]);
  const [breakeven, setBreakeven] = useState(true);
  const [sl, setSl] = useState("1");
  const [trail, setTrail] = useState("");
  const [done, setDone] = useState<string | null>(null);
  const n = (t: string) => {
    const v = Number(t.replace(",", "."));
    return t.trim() === "" || !Number.isFinite(v) ? null : v;
  };
  const m = n(margin) ?? 0;
  const ref = kind === "limit" ? n(limit) ?? price : price;
  const fee = state?.fees.taker_pct ?? 0.0006;
  const slN = n(sl);
  const trailN = n(trail);
  const liq = (side: 1 | -1) => (ref ? ref * (1 - side * (1 / leverage - 0.005)) : null);
  const available = state?.account.available ?? null;
  const closeSum = levels.reduce((a, lv) => a + (n(lv.close) ?? 0), 0);

  function setLevel(i: number, key: "dist" | "close", value: string) {
    setLevels((cur) => cur.map((lv, k) => (k === i ? { ...lv, [key]: value } : lv)));
  }

  function submit(side: "LONG" | "SHORT") {
    setDone(null);
    const partial = tpMode === "partial";
    place.mutate(
      {
        symbol, side, kind, margin_usdt: m, leverage, price: kind === "limit" ? n(limit) : null,
        tp_pct: tpMode === "single" ? n(tp) : null,
        sl_pct: slN,
        take_profits: partial ? levels.map((lv) => [n(lv.dist) ?? 0, n(lv.close) ?? 0] as [number, number]) : [],
        breakeven: partial && breakeven,
        trail_pct: trailN,
      },
      { onSuccess: () => setDone(kind === "market" ? `Orden enviada: ${side === "LONG" ? "largo" : "corto"} ${symbol} a mercado (se llena con el próximo precio).` : `Orden límite ${side === "LONG" ? "larga" : "corta"} creada.`) },
    );
  }

  const label = "flex flex-col gap-1 text-xs text-gray-500 dark:text-gray-400";
  return (
    <form className="flex flex-col gap-2.5 p-3 text-sm" onSubmit={(e) => e.preventDefault()} aria-label="Crear operación">
      <div className="flex items-center justify-between text-xs">
        <span className="rounded border border-gray-300 px-2 py-0.5 dark:border-gray-600">Aislado</span>
        <label htmlFor="sala-lev" className="text-gray-500 dark:text-gray-400">Apalancamiento <b className="font-mono text-gray-900 dark:text-gray-100">{leverage}x</b></label>
      </div>
      <input id="sala-lev" type="range" min={1} max={100} value={leverage} onChange={(e) => setLeverage(Number(e.target.value))} aria-label="Apalancamiento" />
      <div className="grid grid-cols-2 gap-1 rounded-lg bg-gray-100 p-1 dark:bg-gray-800">
        {(["market", "limit"] as const).map((k) => (
          <button key={k} type="button" aria-pressed={kind === k} onClick={() => { setKind(k); if (k === "limit" && !limit && price) setLimit(String(price)); }}
            className={`rounded-md py-1 text-sm font-semibold ${kind === k ? "bg-blue-600 text-white" : "text-gray-500 dark:text-gray-400"}`}>
            {k === "market" ? "Mercado" : "Límite"}
          </button>
        ))}
      </div>
      {kind === "limit" && (
        <label className={label}>Precio límite (USDT)
          <input className={field} value={limit} onChange={(e) => setLimit(e.target.value)} inputMode="decimal" />
        </label>
      )}
      <label className={label}>
        <span>Margen por operación (USDT){available !== null && <span> · disponible {available.toFixed(2)}</span>}</span>
        <input className={field} value={margin} onChange={(e) => setMargin(e.target.value)} inputMode="decimal" />
      </label>
      <div className="flex gap-1">
        {[10, 25, 50, 100].map((p) => (
          <button key={p} type="button" disabled={available === null}
            onClick={() => available !== null && setMargin(((available * p) / 100 / (1 + leverage * fee)).toFixed(2))}
            className="flex-1 rounded border border-gray-300 py-0.5 text-xs disabled:opacity-40 dark:border-gray-600">{p} %</button>
        ))}
      </div>
      <div className="grid grid-cols-2 gap-2">
        <label className={label}>SL % (vacío = sin SL)<input className={field} value={sl} onChange={(e) => setSl(e.target.value)} inputMode="decimal" /></label>
        <label className={label}>Trailing % (vacío = sin trailing)<input className={field} value={trail} onChange={(e) => setTrail(e.target.value)} inputMode="decimal" /></label>
      </div>
      <label className={label}>Toma de ganancias
        <select className={field} value={tpMode} onChange={(e) => setTpMode(e.target.value as "single" | "partial" | "none")}>
          <option value="single">Un solo TP (cierra todo)</option>
          <option value="partial">TP parciales (salida escalonada)</option>
          <option value="none">Sin TP</option>
        </select>
      </label>
      {tpMode === "single" && (
        <label className={label}>TP %<input className={field} value={tp} onChange={(e) => setTp(e.target.value)} inputMode="decimal" /></label>
      )}
      {tpMode === "partial" && (
        <div className="flex flex-col gap-1.5">
          {levels.map((lv, i) => (
            <div key={i} className="grid grid-cols-[1fr_1fr_auto] items-end gap-2">
              <label className={label}>{`TP${i + 1}: distancia %`}<input className={field} value={lv.dist} onChange={(e) => setLevel(i, "dist", e.target.value)} inputMode="decimal" /></label>
              <label className={label}>{`TP${i + 1}: % que cierra`}<input className={field} value={lv.close} onChange={(e) => setLevel(i, "close", e.target.value)} inputMode="decimal" /></label>
              <button type="button" aria-label={`Quitar TP${i + 1}`} disabled={levels.length === 1}
                onClick={() => setLevels((cur) => cur.filter((_, k) => k !== i))}
                className="mb-0.5 h-8 w-7 rounded border border-gray-300 text-sm disabled:opacity-30 dark:border-gray-600">×</button>
            </div>
          ))}
          <div className="flex items-center justify-between text-xs">
            <button type="button" disabled={levels.length >= 3} onClick={() => setLevels((cur) => [...cur, { dist: "", close: "" }])}
              className="text-blue-700 underline disabled:opacity-40 dark:text-blue-300">Agregar nivel</button>
            <span className={closeSum > 100 ? "text-red-700 dark:text-red-400" : "text-gray-500 dark:text-gray-400"}>
              {closeSum > 100 ? `Cierran ${closeSum} %: más del 100 %` : closeSum < 100 ? `Cierran ${closeSum} %; el ${100 - closeSum} % sigue con el SL${trailN ? " y el trailing" : ""}` : "Cierran el 100 %"}
            </span>
          </div>
          <label className="flex items-center gap-2 text-xs">
            <input type="checkbox" checked={breakeven} onChange={(e) => setBreakeven(e.target.checked)} /> Mover el SL a la entrada al tocarse el TP1
          </label>
        </div>
      )}
      <div className="grid grid-cols-2 gap-2">
        <button type="button" disabled={place.isPending} onClick={() => submit("LONG")} className="rounded-lg bg-green-600 py-2 font-bold text-white disabled:opacity-50">Abrir largo</button>
        <button type="button" disabled={place.isPending} onClick={() => submit("SHORT")} className="rounded-lg bg-red-600 py-2 font-bold text-white disabled:opacity-50">Abrir corto</button>
      </div>
      <dl className="grid grid-cols-[1fr_auto] gap-x-3 gap-y-1 text-xs">
        <dt className="text-gray-500 dark:text-gray-400">Tamaño</dt><dd className="text-right font-mono">{m.toFixed(2)} × {leverage}x = {(m * leverage).toFixed(2)} USDT</dd>
        <dt className="text-gray-500 dark:text-gray-400">Liquidación aprox. (L / C)</dt><dd className="text-right font-mono">{fp(liq(1), ref)} / {fp(liq(-1), ref)}</dd>
        <dt className="text-gray-500 dark:text-gray-400">Si salta el SL pierdes</dt><dd className="text-right font-mono text-red-700 dark:text-red-400">{slN ? `${((m * leverage * slN) / 100).toFixed(2)} USDT (${(slN * leverage).toFixed(1)} % del margen)` : trailN ? "según el trailing" : "hasta el margen (sin SL)"}</dd>
        <dt className="text-gray-500 dark:text-gray-400">Comisión / deslizamiento</dt><dd className="text-right font-mono">{(fee * 100).toFixed(2)} % · {state?.fees.slippage_bps ?? 5} pb</dd>
      </dl>
      {place.isError && <p role="alert" className="text-xs text-red-700 dark:text-red-300">{errorText(place.error)}</p>}
      {done && <p role="status" className="text-xs text-green-700 dark:text-green-300">{done}</p>}
    </form>
  );
}

function AccountPanel({ state, onReset }: { state: SalaState | undefined; onReset: () => void }) {
  const a = state?.account;
  const item = (label: string, value: string, cls = "") => (
    <div className="flex flex-col gap-0.5"><span className="text-xs text-gray-500 dark:text-gray-400">{label}</span><span className={`font-mono text-sm font-semibold ${cls}`}>{value}</span></div>
  );
  const usd = (v: number | null | undefined) => (v === null || v === undefined ? "-" : `${v.toFixed(2)} USDT`);
  return (
    <div className="flex flex-col gap-3 p-3">
      <div className="grid grid-cols-[repeat(auto-fit,minmax(140px,1fr))] gap-3">
        {item("Equidad", usd(a?.equity))}
        {item("Disponible", usd(a?.available))}
        {item("Saldo", usd(a?.balance))}
        {item("Margen ocupado", usd(a?.used_margin))}
        {item("PnL no realizado", a?.upnl === null || a?.upnl === undefined ? "-" : `${fs(a.upnl, 3)} USDT`, pnl(a?.upnl))}
        {item("PnL realizado (hoy)", a?.realized_today === null || a?.realized_today === undefined ? "-" : `${fs(a.realized_today, 2)} USDT`, pnl(a?.realized_today))}
      </div>
      <button type="button" onClick={onReset} className="self-start rounded border border-red-300 px-2 py-1 text-xs text-red-700 dark:border-red-700 dark:text-red-300">
        Reiniciar a {a?.start_balance?.toLocaleString("es-CL") ?? "1.000"} USDT
      </button>
      <p className="text-[11px] text-gray-500 dark:text-gray-400">Cuenta separada de las pruebas «En vivo» y de la medición: tus operaciones manuales no cambian ninguna prueba.</p>
    </div>
  );
}

function PositionsPanel({ state, tab }: { state: SalaState | undefined; tab: "pos" | "ord" | "hist" }) {
  const close = useSalaClosePosition();
  const be = useSalaBreakeven();
  const cancel = useSalaCancelOrder();
  const now = state?.now_ms ?? Date.now();
  const err = close.error ?? be.error ?? cancel.error;
  const th = "sticky top-0 whitespace-nowrap bg-white px-2 py-1.5 text-left font-medium text-gray-500 dark:text-gray-400 dark:bg-gray-900";
  const td = "whitespace-nowrap border-t border-gray-100 px-2 py-1.5 align-top dark:border-gray-800";
  const side = (s: string, lev: number) => (
    <span className={`rounded px-1 text-[11px] font-semibold ${s === "LONG" ? "bg-green-100 text-green-800 dark:bg-green-900/40 dark:text-green-200" : "bg-red-100 text-red-800 dark:bg-red-900/40 dark:text-red-200"}`}>
      {s === "LONG" ? "Largo" : "Corto"} {lev}x
    </span>
  );
  const btn = "rounded border border-gray-300 px-2 py-0.5 text-[11px] dark:border-gray-600";
  let body;
  if (tab === "pos") {
    const rows = state?.positions ?? [];
    body = rows.length === 0 ? <p className="p-6 text-center text-sm text-gray-500 dark:text-gray-400">Sin posiciones abiertas. Abre una desde «Crear operación».</p> : (
      <table className="w-full min-w-[900px] text-xs tabular-nums" aria-label="Posiciones manuales">
        <thead><tr>{["Moneda", "Tamaño", "Entrada", "Precio actual", "Liquidación", "PnL no realizado (ROE)", "Neto si cierra", "TP / SL", "Abierta", "Acciones"].map((h) => <th key={h} className={th}>{h}</th>)}</tr></thead>
        <tbody>
          {rows.map((p: SalaPosition) => (
            <tr key={p.id} className="text-gray-800 dark:text-gray-200">
              <td className={td}><b>{p.symbol}</b><div>{side(p.side, p.leverage)}</div></td>
              <td className={td}>{p.notional === null ? "-" : `${p.notional.toFixed(2)} USDT`}<div className="text-gray-500 dark:text-gray-400">margen {p.margin_usdt.toFixed(2)} · {Math.round(p.size_pct)} % abierto</div></td>
              <td className={`${td} font-mono`}>{fp(p.entry_price)}</td>
              <td className={`${td} font-mono`}><b>{fp(p.price, p.entry_price)}</b><div className="text-gray-500 dark:text-gray-400">{p.price_ts_ms ? `hace ${age(p.price_ts_ms, now)}` : "sin precio"}</div></td>
              <td className={`${td} font-mono`}>{fp(p.liq, p.entry_price)}<div className="text-gray-500 dark:text-gray-400">{p.to_liq_pct === null ? "" : `a ${p.to_liq_pct.toFixed(2)} %`}</div></td>
              <td className={`${td} font-mono font-semibold ${pnl(p.upnl)}`}>{fs(p.upnl, 3, " USDT")}<div>{fs(p.roe_pct, 2, " %")}</div></td>
              <td className={`${td} font-mono ${pnl(p.net_if_closed)}`}>{fs(p.net_if_closed, 3, " USDT")}<div className="text-gray-500 dark:text-gray-400">con comisiones</div></td>
              <td className={`${td} font-mono`}>
                {p.tp_levels && p.tp_levels.length > 0 ? (
                  p.tp_levels.map(([lp, frac, hit], k) => (
                    <div key={k} className={hit ? "text-gray-400 line-through" : "text-green-700 dark:text-green-400"}>
                      TP{k + 1} {fp(lp, p.entry_price)} ({Math.round(frac * 100)} %){hit ? " ✓" : ""}
                    </div>
                  ))
                ) : (
                  <span className="text-green-700 dark:text-green-400">{p.tp ? fp(p.tp, p.entry_price) : "sin TP"}</span>
                )}
                <div className="text-red-700 dark:text-red-400">
                  {p.no_sl ? "sin SL" : `${p.trailing ? "Trailing " : p.stop === p.entry_price ? "SL en entrada " : "SL "}${fp(p.stop, p.entry_price)}`}
                </div>
                {p.trailing ? <div className="text-gray-500 dark:text-gray-400">trailing {p.trail_pct ?? "-"} %</div> : null}
              </td>
              <td className={td}>{age(p.entry_time_ms, now)}</td>
              <td className={td}>
                <div className="flex gap-1">
                  <button type="button" className={btn} onClick={() => close.mutate({ id: p.id, fraction: 1 })}>Cerrar</button>
                  <button type="button" className={btn} onClick={() => close.mutate({ id: p.id, fraction: 0.5 })}>Cerrar 50 %</button>
                  <button type="button" className={btn} onClick={() => be.mutate(p.id)}>SL a entrada</button>
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    );
  } else if (tab === "ord") {
    const rows = state?.orders ?? [];
    body = rows.length === 0 ? <p className="p-6 text-center text-sm text-gray-500 dark:text-gray-400">Sin órdenes pendientes.</p> : (
      <table className="w-full min-w-[760px] text-xs tabular-nums" aria-label="Órdenes manuales">
        <thead><tr>{["Moneda", "Lado", "Tipo", "Precio", "Precio actual", "Margen", "TP / SL", "Creada", ""].map((h) => <th key={h} className={th}>{h}</th>)}</tr></thead>
        <tbody>
          {rows.map((o) => (
            <tr key={o.id} className="text-gray-800 dark:text-gray-200">
              <td className={td}><b>{o.symbol}</b></td><td className={td}>{side(o.side, o.leverage)}</td>
              <td className={td}>{o.kind === "market" ? "Mercado (esperando precio)" : "Límite"}</td>
              <td className={`${td} font-mono`}>{o.price ? fp(o.price) : "-"}</td><td className={`${td} font-mono`}>{fp(o.last_price ?? null, o.price)}</td>
              <td className={`${td} font-mono`}>{o.margin_usdt.toFixed(2)} USDT</td>
              <td className={`${td} font-mono`}>{o.tp_pct ? `${o.tp_pct} %` : "-"} / {o.sl_pct ? `${o.sl_pct} %` : "-"}</td>
              <td className={td}>{age(o.created_ms, now)}</td>
              <td className={td}><button type="button" className={btn} onClick={() => cancel.mutate(o.id)}>Cancelar</button></td>
            </tr>
          ))}
        </tbody>
      </table>
    );
  } else {
    const rows = state?.trades ?? [];
    body = rows.length === 0 ? <p className="p-6 text-center text-sm text-gray-500 dark:text-gray-400">Todavía sin operaciones cerradas.</p> : (
      <table className="w-full min-w-[700px] text-xs tabular-nums" aria-label="Historial manual">
        <thead><tr>{["Cierre (UTC)", "Moneda", "Lado", "Entrada", "Salida", "Motivo", "Neto"].map((h) => <th key={h} className={th}>{h}</th>)}</tr></thead>
        <tbody>
          {rows.map((t) => (
            <tr key={t.id} className="text-gray-800 dark:text-gray-200">
              <td className={`${td} font-mono`}>{new Date(t.exit_time_ms).toISOString().slice(0, 19).replace("T", " ")}</td>
              <td className={td}><b>{t.symbol}</b></td><td className={td}>{side(t.side, t.leverage)}</td>
              <td className={`${td} font-mono`}>{fp(t.entry_price)}</td><td className={`${td} font-mono`}>{fp(t.exit_price, t.entry_price)}</td>
              <td className={td}>{t.close_reason}</td>
              <td className={`${td} font-mono font-semibold ${pnl(t.pnl_net_usdt)}`}>{fs(t.pnl_net_usdt, 3, " USDT")}</td>
            </tr>
          ))}
        </tbody>
      </table>
    );
  }
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      {err && <p role="alert" className="px-3 pt-2 text-xs text-red-700 dark:text-red-300">{errorText(err)}</p>}
      <div className="min-h-0 flex-1 overflow-auto">{body}</div>
    </div>
  );
}

export function SalaTab() {
  const [symbol, setSymbol] = useState("BTCUSDT");
  const [interval, setTf] = useState("15m");
  const [layout, setLayoutState] = useState<Layout>(loadLayout);
  const [overlays, setOverlays] = useState<ChartOverlays>({ ema9: true, ema21: true, cloud: true, fib: false, positions: true });
  const [tab, setTab] = useState<"pos" | "ord" | "hist">("pos");
  const [ask, setAsk] = useState<"closeall" | "reset" | null>(null);
  const others = useMemo(() => SALA_SYMBOLS.filter((s) => s !== symbol), [symbol]);
  const state = useSalaState(symbol, others);
  const candles = useSalaCandles(symbol, interval);
  const book = useSalaBook(symbol);
  const closeAll = useSalaCloseAll();
  const reset = useSalaReset();
  const s = state.data;
  const live = s?.prices[symbol]?.price ?? null;
  const ticker = book.data?.ticker ?? null;
  const last = live ?? num(ticker?.lastPrice);
  const open24 = num(ticker?.open);
  const chg = last !== null && open24 ? (last / open24 - 1) * 100 : null;

  function setLayout(next: Layout) {
    setLayoutState(next);
    saveLayout(next);
  }

  const toggle = (k: keyof ChartOverlays, label: string, color: string) => (
    <label className="inline-flex cursor-pointer items-center gap-1 whitespace-nowrap text-xs text-gray-500 dark:text-gray-400">
      <input type="checkbox" checked={overlays[k]} onChange={(e) => setOverlays({ ...overlays, [k]: e.target.checked })} />
      <i className="inline-block h-[3px] w-2.5 rounded" style={{ background: color }} />{label}
    </label>
  );

  const depth = book.data?.depth;
  const asks = (depth?.asks ?? []).slice(0, 12).reverse();
  const bids = (depth?.bids ?? []).slice(0, 12);
  const maxQty = Math.max(1e-12, ...asks.map((a) => a[1]), ...bids.map((b) => b[1]));
  const bidSum = bids.reduce((a, b) => a + b[1], 0);
  const askSum = asks.reduce((a, b) => a + b[1], 0);
  const bidPct = bidSum + askSum > 0 ? (bidSum / (bidSum + askSum)) * 100 : 50;

  const panels = {
    chart: {
      tools: (
        <>
          <div className="flex gap-0.5 rounded-md border border-gray-200 bg-gray-100 p-0.5 dark:border-gray-700 dark:bg-gray-800" role="group" aria-label="Marco">
            {INTERVALS.map((iv) => (
              <button key={iv} type="button" aria-pressed={interval === iv} onClick={() => setTf(iv)}
                className={`rounded px-1.5 text-xs ${interval === iv ? "bg-white font-semibold text-gray-900 dark:bg-gray-700 dark:text-white" : "text-gray-500 dark:text-gray-400"}`}>{iv}</button>
            ))}
          </div>
          {toggle("ema9", "EMA 9", "#f59e0b")}{toggle("ema21", "EMA 21", "#a855f7")}{toggle("cloud", "Cloud 50", "#64748b")}
          {toggle("fib", "Fibonacci", "#06b6d4")}{toggle("positions", "Mis posiciones", "#3b82f6")}
        </>
      ),
      body: candles.data?.candles ? (
        <ChartPanel candles={candles.data.candles} price={live} overlays={overlays} positions={s?.positions ?? []} orders={s?.orders ?? []} symbol={symbol} />
      ) : (
        <p className="p-6 text-sm text-gray-500 dark:text-gray-400">{candles.isError ? "No se pudieron cargar las velas de Bitunix." : "Cargando velas…"}</p>
      ),
    },
    book: {
      body: (
        <div className="flex min-h-0 flex-1 flex-col overflow-hidden font-mono text-xs tabular-nums">
          <div className="grid grid-cols-3 px-3 py-1 text-[11px] text-gray-500 dark:text-gray-400"><span>Precio</span><span className="text-right">Cant.</span><span className="text-right">Acum.</span></div>
          <div className="flex min-h-0 flex-1 flex-col justify-end overflow-hidden">
            {asks.map(([p, q], i) => (
              <div key={`a${i}`} className="relative grid grid-cols-3 px-3 py-px">
                <i className="absolute inset-y-px right-0 bg-red-100 dark:bg-red-900/30" style={{ width: `${(q / maxQty) * 100}%` }} />
                <span className="relative text-red-700 dark:text-red-400">{fp(p, last)}</span><span className="relative text-right">{q.toFixed(3)}</span><span className="relative text-right text-gray-500 dark:text-gray-400">{(p * q).toFixed(0)}</span>
              </div>
            ))}
          </div>
          <div className="border-y border-gray-200 px-3 py-1 text-base font-semibold dark:border-gray-700">{fp(last)}</div>
          <div className="min-h-0 flex-1 overflow-hidden">
            {bids.map(([p, q], i) => (
              <div key={`b${i}`} className="relative grid grid-cols-3 px-3 py-px">
                <i className="absolute inset-y-px right-0 bg-green-100 dark:bg-green-900/30" style={{ width: `${(q / maxQty) * 100}%` }} />
                <span className="relative text-green-700 dark:text-green-400">{fp(p, last)}</span><span className="relative text-right">{q.toFixed(3)}</span><span className="relative text-right text-gray-500 dark:text-gray-400">{(p * q).toFixed(0)}</span>
              </div>
            ))}
          </div>
          <div className="mx-3 my-1.5 flex h-4 overflow-hidden rounded text-[10px]">
            <div className="flex items-center bg-green-100 px-1 text-green-800 dark:bg-green-900/40 dark:text-green-200" style={{ width: `${bidPct}%` }}>C {bidPct.toFixed(0)} %</div>
            <div className="flex flex-1 items-center justify-end bg-red-100 px-1 text-red-800 dark:bg-red-900/40 dark:text-red-200">{(100 - bidPct).toFixed(0)} % V</div>
          </div>
          {book.isError && <p className="px-3 pb-2 text-[11px] text-red-600">Libro no disponible.</p>}
        </div>
      ),
    },
    trades: {
      body: (
        <div className="font-mono text-xs tabular-nums">
          <div className="grid grid-cols-3 px-3 py-1 text-[11px] text-gray-500 dark:text-gray-400"><span>Precio</span><span className="text-right">Cant.</span><span className="text-right">Hora UTC</span></div>
          {(s?.recent ?? []).length === 0 && <p className="px-3 py-2 font-sans text-gray-500 dark:text-gray-400">Esperando operaciones del mercado…</p>}
          {(s?.recent ?? []).slice(0, 40).map((t, i) => (
            <div key={i} className="grid grid-cols-3 px-3 py-px">
              <span className={t.side === "buy" ? "text-green-700 dark:text-green-400" : "text-red-700 dark:text-red-400"}>{fp(t.price)}</span>
              <span className="text-right">{t.qty.toFixed(3)}</span><span className="text-right text-gray-500 dark:text-gray-400">{new Date(t.ts_ms).toISOString().slice(11, 19)}</span>
            </div>
          ))}
        </div>
      ),
    },
    watch: {
      body: (
        <div className="text-xs">
          {SALA_SYMBOLS.map((sym) => (
            <button key={sym} type="button" onClick={() => setSymbol(sym)} aria-pressed={sym === symbol}
              className={`grid w-full grid-cols-[1fr_auto] gap-2 border-b border-gray-100 px-3 py-1.5 text-left dark:border-gray-800 ${sym === symbol ? "bg-blue-50 dark:bg-blue-950" : "hover:bg-gray-50 dark:hover:bg-gray-800"}`}>
              <b>{sym.replace("USDT", "")}</b><span className="font-mono">{fp(s?.prices[sym]?.price ?? null)}</span>
            </button>
          ))}
        </div>
      ),
    },
    order: { body: <OrderForm symbol={symbol} price={last} state={s} /> },
    account: { body: <AccountPanel state={s} onReset={() => setAsk("reset")} /> },
    positions: {
      titleNode: (
        <div className="flex items-center gap-3 text-xs" role="tablist">
          {([["pos", `Posiciones (${s?.positions.length ?? 0})`], ["ord", `Órdenes (${s?.orders.length ?? 0})`], ["hist", "Historial"]] as const).map(([k, label]) => (
            <button key={k} type="button" role="tab" aria-selected={tab === k} onClick={() => setTab(k)}
              className={`border-b-2 py-0.5 ${tab === k ? "border-blue-600 font-semibold text-gray-900 dark:text-gray-100" : "border-transparent text-gray-500 dark:text-gray-400"}`}>{label}</button>
          ))}
        </div>
      ),
      tools: (
        <span className="ml-auto flex items-center gap-2 text-xs">
          {s && s.positions.length > 0 && <span className={`font-mono ${pnl(s.account.upnl)}`}>PnL {fs(s.account.upnl, 3, " USDT")}</span>}
          <button type="button" onClick={() => setAsk("closeall")} className="rounded border border-gray-300 px-2 py-0.5 dark:border-gray-600">Cerrar todo</button>
        </span>
      ),
      body: <PositionsPanel state={s} tab={tab} />,
    },
  };

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-x-6 gap-y-2 rounded-lg border border-gray-200 bg-white px-3 py-2 text-gray-900 dark:border-gray-700 dark:bg-gray-900 dark:text-gray-100">
        <label className="flex items-center gap-2 text-xs text-gray-500 dark:text-gray-400">Par
          <select value={symbol} onChange={(e) => setSymbol(e.target.value)} className="rounded border border-gray-300 bg-gray-50 px-1.5 py-1 text-sm font-bold text-gray-900 dark:border-gray-600 dark:bg-gray-800 dark:text-gray-100" aria-label="Par">
            {SALA_SYMBOLS.map((x) => <option key={x} value={x}>{x}</option>)}
          </select>
        </label>
        <span className={`font-mono text-xl font-semibold ${pnl(chg)}`}>{fp(last)}</span>
        {[["Cambio 24 h", chg === null ? "-" : fs(chg, 2, " %")], ["Precio marcado", fp(num(ticker?.markPrice), last)], ["Máximo 24 h", fp(num(ticker?.high), last)],
          ["Mínimo 24 h", fp(num(ticker?.low), last)], ["Funding", book.data?.funding === null || book.data?.funding === undefined ? "-" : `${(book.data.funding * 100).toFixed(4)} %`]].map(([k, v]) => (
          <div key={k} className="flex flex-col text-xs"><span className="text-[11px] text-gray-500 dark:text-gray-400">{k}</span><span className="font-mono">{v}</span></div>
        ))}
        <div className="ml-auto"><PanelsMenu layout={layout} onChange={setLayout} /></div>
      </div>
      {s && !s.runner.enabled && (
        <p role="alert" className="rounded border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-900 dark:border-amber-700 dark:bg-amber-900/30 dark:text-amber-200">
          El corredor de «En vivo» está apagado en esta instancia: la sala muestra lo guardado, pero no puede operar ni recibir precios.
        </p>
      )}
      {s && s.runner.enabled && !s.runner.bot_on && (
        <p role="alert" className="rounded border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-900 dark:border-amber-700 dark:bg-amber-900/30 dark:text-amber-200">
          El bot está apagado: enciéndelo en la pestaña «En vivo» para recibir precios y operar. Tus posiciones manuales siguen abiertas y se vuelven a vigilar al encenderlo.
        </p>
      )}
      <FloatingDesk layout={layout} onChange={setLayout} panels={panels} />
      <p className="text-[11px] text-gray-500 dark:text-gray-400">Paper trading con el precio real de Bitunix: ninguna orden sale de Minerva. Gráfico: Lightweight Charts de TradingView.</p>
      {ask === "closeall" && (
        <ConfirmDialog title="Cerrar todas las posiciones manuales" warning="Se cierran todas tus posiciones manuales con el último precio y se cancelan las órdenes pendientes."
          phrase="CERRAR" confirmLabel="Cerrar todo" busy={closeAll.isPending} error={closeAll.isError ? errorText(closeAll.error) : null}
          onConfirm={() => closeAll.mutate(undefined, { onSuccess: () => setAsk(null) })} onCancel={() => setAsk(null)} />
      )}
      {ask === "reset" && (
        <ConfirmDialog title="Reiniciar la cuenta manual" warning="Se borran las posiciones y órdenes abiertas (sin cerrarlas) y el saldo vuelve al inicial. El historial queda guardado."
          phrase="REINICIAR" confirmLabel="Reiniciar" busy={reset.isPending} error={reset.isError ? errorText(reset.error) : null}
          onConfirm={() => reset.mutate(undefined, { onSuccess: () => setAsk(null) })} onCancel={() => setAsk(null)} />
      )}
    </div>
  );
}
