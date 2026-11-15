// Disposicion de los paneles flotantes de la Sala de trading: posicion y tamano en fracciones del
// escritorio (asi se adapta a cualquier ventana) y si cada panel esta oculto. Se guarda en
// localStorage solo como comodidad de este navegador: si falla, se usa la disposicion por defecto.

export const PANEL_TITLES = {
  chart: "Gráfico",
  book: "Libro de órdenes",
  trades: "Operaciones del mercado",
  watch: "Monedas",
  order: "Crear operación",
  account: "Cuenta paper manual",
  positions: "Posiciones y órdenes",
} as const;

export type PanelId = keyof typeof PANEL_TITLES;
export const PANEL_IDS = Object.keys(PANEL_TITLES) as PanelId[];

export interface PanelBox {
  x: number;
  y: number;
  w: number;
  h: number;
  hidden: boolean;
}
export type Layout = Record<PanelId, PanelBox>;

// Ocupa toda la pantalla: grafico grande, libro chico, orden y cuenta separadas a la derecha.
export const DEFAULT_LAYOUT: Layout = {
  chart: { x: 0, y: 0, w: 0.585, h: 0.63, hidden: false },
  book: { x: 0.585, y: 0, w: 0.17, h: 0.4, hidden: false },
  trades: { x: 0.585, y: 0.4, w: 0.17, h: 0.23, hidden: false },
  order: { x: 0.755, y: 0, w: 0.245, h: 0.63, hidden: false },
  positions: { x: 0, y: 0.63, w: 0.585, h: 0.37, hidden: false },
  watch: { x: 0.585, y: 0.63, w: 0.17, h: 0.37, hidden: false },
  account: { x: 0.755, y: 0.63, w: 0.245, h: 0.37, hidden: false },
};

export const LAYOUT_KEY = "minerva.sala.layout.v1";

const finite = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);

export function loadLayout(): Layout {
  const out = structuredClone(DEFAULT_LAYOUT);
  try {
    const raw = localStorage.getItem(LAYOUT_KEY);
    if (!raw) return out;
    const saved = JSON.parse(raw) as Partial<Record<PanelId, Partial<PanelBox>>>;
    for (const id of PANEL_IDS) {
      const s = saved?.[id];
      if (s && finite(s.x) && finite(s.y) && finite(s.w) && finite(s.h)) {
        out[id] = { x: s.x, y: s.y, w: s.w, h: s.h, hidden: s.hidden === true };
      }
    }
  } catch {
    // almacenamiento bloqueado o JSON roto: disposicion por defecto
  }
  return out;
}

export function saveLayout(layout: Layout): void {
  try {
    localStorage.setItem(LAYOUT_KEY, JSON.stringify(layout));
  } catch {
    // sin almacenamiento: la disposicion vale solo para esta visita
  }
}
