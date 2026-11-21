import type { LiveOpenPosition, LiveTestRow } from "../api";

// Vista de la pestana «En vivo»: filtros, orden y tarjetas abiertas de la tabla resumen. Se recuerda
// en localStorage (solo comodidad de este navegador: si falla, se usa la vista por defecto).

export type StatusFilter = "active" | "stopped" | "all";
export type SortKey =
  | "id" | "name" | "timeframe" | "status" | "trades" | "pf" | "net" | "winrate" | "open" | "upnl" | "diff";

export interface LiveView {
  status: StatusFilter;
  timeframe: string; // "" = todos
  query: string;
  sort: SortKey;
  desc: boolean;
  open: number[]; // ids de las pruebas con el detalle abierto
}

export const DEFAULT_VIEW: LiveView = { status: "active", timeframe: "", query: "", sort: "id", desc: false, open: [] };
export const VIEW_KEY = "minerva.live.view";

const SORT_KEYS: SortKey[] = ["id", "name", "timeframe", "status", "trades", "pf", "net", "winrate", "open", "upnl", "diff"];

export function loadView(key: string = VIEW_KEY): LiveView {
  try {
    const raw = localStorage.getItem(key);
    if (!raw) return DEFAULT_VIEW;
    const v = JSON.parse(raw) as Partial<LiveView>;
    return {
      status: v.status === "stopped" || v.status === "all" ? v.status : "active",
      timeframe: typeof v.timeframe === "string" ? v.timeframe : "",
      query: typeof v.query === "string" ? v.query : "",
      sort: SORT_KEYS.includes(v.sort as SortKey) ? (v.sort as SortKey) : "id",
      desc: v.desc === true,
      open: Array.isArray(v.open) ? v.open.filter((x): x is number => Number.isInteger(x)) : [],
    };
  } catch {
    return DEFAULT_VIEW;
  }
}

export function saveView(view: LiveView, key: string = VIEW_KEY): void {
  try {
    localStorage.setItem(key, JSON.stringify(view));
  } catch {
    // sin almacenamiento (modo privado, bloqueado): la vista vale solo para esta visita
  }
}

/** PnL no realizado de cada prueba (suma de sus posiciones abiertas con precio). */
export function upnlByTest(positions: LiveOpenPosition[]): Map<number, number> {
  const out = new Map<number, number>();
  for (const p of positions) {
    if (p.upnl !== null) out.set(p.test_id, (out.get(p.test_id) ?? 0) + p.upnl);
  }
  return out;
}

const TF_ORDER = ["15m", "1h", "4h", "1d", "1w"];

function sortValue(t: LiveTestRow, key: SortKey, upnl: Map<number, number>): number | string | null {
  const s = t.summary;
  switch (key) {
    case "id": return t.id;
    case "name": return t.config.name.toLowerCase();
    case "timeframe": return TF_ORDER.indexOf(t.config.timeframe);
    case "status": return t.status === "active" ? 0 : 1;
    case "trades": return s.trades;
    case "pf": return s.pf === null ? null : s.pf === "inf" ? Number.POSITIVE_INFINITY : s.pf;
    case "net": return s.net;
    case "winrate": return s.winrate;
    case "open": return t.open_positions;
    case "upnl": return upnl.get(t.id) ?? null;
    case "diff": return t.vs_control?.diff ?? null;
  }
}

/** Las pruebas que pasan los filtros, en el orden pedido (los valores vacios siempre al final). */
export function visibleTests(tests: LiveTestRow[], view: LiveView, upnl: Map<number, number>): LiveTestRow[] {
  const q = view.query.trim().toLowerCase();
  const rows = tests.filter((t) => {
    if (view.status !== "all" && t.status !== view.status) return false;
    if (view.timeframe && t.config.timeframe !== view.timeframe) return false;
    if (!q) return true;
    return `${t.id} ${t.config.name} ${t.config.indicators.join(" ")}`.toLowerCase().includes(q);
  });
  return rows.sort((a, b) => {
    const va = sortValue(a, view.sort, upnl);
    const vb = sortValue(b, view.sort, upnl);
    if (va === null && vb === null) return a.id - b.id;
    if (va === null) return 1;
    if (vb === null) return -1;
    const cmp = va < vb ? -1 : va > vb ? 1 : a.id - b.id;
    return view.desc ? -cmp : cmp;
  });
}
