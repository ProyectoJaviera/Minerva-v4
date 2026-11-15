import { type PointerEvent as ReactPointerEvent, type ReactNode, useEffect, useRef, useState } from "react";
import { DEFAULT_LAYOUT, type Layout, PANEL_IDS, PANEL_TITLES, type PanelId, saveLayout } from "./layout";

// Escritorio de paneles flotantes: cada panel se arrastra desde su barra, cambia de tamano desde
// bordes y esquinas (con una grilla de 8 px), pasa al frente al tocarlo y se oculta con la x. El
// menu «Paneles» (en la barra de la sala) los vuelve a mostrar. En pantallas angostas los paneles
// se apilan en una columna.

const GAP = 6;
const MIN_W = 200;
const MIN_H = 120;
const SNAP = 8;
const snap = (v: number) => Math.round(v / SNAP) * SNAP;
type Dir = "move" | "n" | "e" | "s" | "w" | "se" | "sw";

function useNarrow(): boolean {
  const query = "(max-width: 760px)";
  const [narrow, setNarrow] = useState(() => typeof window !== "undefined" && !!window.matchMedia?.(query).matches);
  useEffect(() => {
    const mq = window.matchMedia?.(query);
    if (!mq) return;
    const on = () => setNarrow(mq.matches);
    mq.addEventListener?.("change", on);
    return () => mq.removeEventListener?.("change", on);
  }, []);
  return narrow;
}

export function PanelsMenu({ layout, onChange }: { layout: Layout; onChange: (next: Layout) => void }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [open]);
  const set = (id: PanelId, hidden: boolean) => onChange({ ...layout, [id]: { ...layout[id], hidden } });
  return (
    <div className="relative" ref={ref}>
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen(!open)}
        className="rounded-full border border-blue-500 px-3 py-1 text-xs font-semibold text-gray-900 dark:text-gray-100"
      >
        Paneles ▾
      </button>
      {open && (
        <div role="menu" className="absolute right-0 z-[1000] mt-2 w-64 rounded-lg border border-gray-300 bg-white p-2 text-gray-900 shadow-lg dark:border-gray-600 dark:bg-gray-900 dark:text-gray-100">
          <p className="px-2 pb-1 text-[11px] font-semibold uppercase tracking-wide text-gray-500">Mostrar u ocultar</p>
          {PANEL_IDS.map((id) => (
            <label key={id} className="flex items-center gap-2 rounded px-2 py-1.5 text-sm hover:bg-gray-100 dark:hover:bg-gray-800">
              <input type="checkbox" checked={!layout[id].hidden} onChange={(e) => set(id, !e.target.checked)} />
              {PANEL_TITLES[id]}
              {layout[id].hidden && <span className="ml-auto text-[11px] text-gray-400">oculto</span>}
            </label>
          ))}
          <hr className="my-1 border-gray-200 dark:border-gray-700" />
          <button type="button" className="w-full rounded px-2 py-1.5 text-left text-sm hover:bg-gray-100 dark:hover:bg-gray-800"
            onClick={() => onChange(structuredClone(DEFAULT_LAYOUT))}>
            Restablecer disposición
          </button>
          <button type="button" className="w-full rounded px-2 py-1.5 text-left text-sm hover:bg-gray-100 dark:hover:bg-gray-800"
            onClick={() => onChange(Object.fromEntries(PANEL_IDS.map((id) => [id, { ...layout[id], hidden: false }])) as Layout)}>
            Mostrar todos los paneles
          </button>
        </div>
      )}
    </div>
  );
}

export function FloatingDesk({
  layout,
  onChange,
  panels,
}: {
  layout: Layout;
  onChange: (next: Layout) => void;
  panels: Record<PanelId, { tools?: ReactNode; body: ReactNode; titleNode?: ReactNode }>;
}) {
  const deskRef = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ w: 1200, h: 700 });
  const [front, setFront] = useState<PanelId>("chart");
  const [order, setOrder] = useState<PanelId[]>(PANEL_IDS);
  const [live, setLive] = useState<{ id: PanelId; x: number; y: number; w: number; h: number } | null>(null);
  const narrow = useNarrow();

  useEffect(() => {
    const el = deskRef.current;
    if (!el) return;
    const measure = () => setSize({ w: el.clientWidth || 1200, h: el.clientHeight || 700 });
    measure();
    if (typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  function toFront(id: PanelId) {
    setFront(id);
    setOrder((cur) => [...cur.filter((x) => x !== id), id]);
  }

  function box(id: PanelId) {
    if (live && live.id === id) return live;
    const p = layout[id];
    return {
      id,
      x: Math.round(p.x * size.w) + GAP / 2,
      y: Math.round(p.y * size.h) + GAP / 2,
      w: Math.max(MIN_W, Math.round(p.w * size.w) - GAP),
      h: Math.max(MIN_H, Math.round(p.h * size.h) - GAP),
    };
  }

  function start(e: ReactPointerEvent, id: PanelId, dir: Dir) {
    if (narrow || e.button !== 0) return;
    if (dir === "move" && (e.target as HTMLElement).closest("button, input, select, label, a")) return;
    e.preventDefault();
    toFront(id);
    const target = e.currentTarget as HTMLElement;
    target.setPointerCapture?.(e.pointerId);
    const s = box(id), mx = e.clientX, my = e.clientY, W = size.w, H = size.h;
    let cur = { ...s };
    const move = (ev: PointerEvent) => {
      const dx = ev.clientX - mx, dy = ev.clientY - my;
      let { x, y, w, h } = s;
      const d = dir === "move" ? "" : dir;
      if (dir === "move") { x += dx; y += dy; }
      if (d.includes("e")) w = s.w + dx;
      if (d.includes("s")) h = s.h + dy;
      if (d.includes("w")) { w = s.w - dx; x = s.x + dx; }
      if (d.includes("n")) { h = s.h - dy; y = s.y + dy; }
      if (d) {
        if (w < MIN_W) { if (d.includes("w")) x -= MIN_W - w; w = MIN_W; }
        if (h < MIN_H) { if (d.includes("n")) y -= MIN_H - h; h = MIN_H; }
        w = snap(Math.min(w, W));
        h = snap(Math.min(h, H));
      }
      x = Math.min(Math.max(0, snap(x)), Math.max(0, W - w));
      y = Math.min(Math.max(0, snap(y)), Math.max(0, H - h));
      cur = { id, x, y, w, h };
      setLive(cur);
    };
    const up = () => {
      target.removeEventListener("pointermove", move);
      target.removeEventListener("pointerup", up);
      target.removeEventListener("pointercancel", up);
      setLive(null);
      const next = {
        ...layout,
        [id]: {
          x: Math.max(0, cur.x - GAP / 2) / W,
          y: Math.max(0, cur.y - GAP / 2) / H,
          w: Math.min(W, cur.w + GAP) / W,
          h: Math.min(H, cur.h + GAP) / H,
          hidden: false,
        },
      };
      onChange(next);
    };
    target.addEventListener("pointermove", move);
    target.addEventListener("pointerup", up);
    target.addEventListener("pointercancel", up);
  }

  const handles: { dir: Dir; cls: string }[] = [
    { dir: "n", cls: "left-2 right-2 -top-1 h-2 cursor-ns-resize" },
    { dir: "e", cls: "top-2 bottom-2 -right-1 w-2 cursor-ew-resize" },
    { dir: "s", cls: "left-2 right-2 -bottom-1 h-2 cursor-ns-resize" },
    { dir: "w", cls: "top-2 bottom-2 -left-1 w-2 cursor-ew-resize" },
    { dir: "se", cls: "-right-0.5 -bottom-0.5 h-3.5 w-3.5 cursor-nwse-resize" },
    { dir: "sw", cls: "-left-0.5 -bottom-0.5 h-3.5 w-3.5 cursor-nesw-resize" },
  ];

  return (
    <div
      ref={deskRef}
      aria-label="Escritorio de paneles"
      className={narrow ? "flex flex-col gap-3" : "relative h-[calc(100vh-190px)] min-h-[560px] overflow-hidden rounded-lg bg-gray-100 dark:bg-gray-950"}
    >
      {PANEL_IDS.filter((id) => !layout[id].hidden).map((id) => {
        const b = box(id);
        const content = panels[id];
        const style = narrow ? undefined : { left: b.x, top: b.y, width: b.w, height: b.h, zIndex: 10 + order.indexOf(id) };
        return (
          <section
            key={id}
            aria-label={PANEL_TITLES[id]}
            data-panel={id}
            style={style}
            onPointerDown={() => toFront(id)}
            className={`${narrow ? "relative" : "absolute"} flex flex-col overflow-hidden rounded-lg border bg-white text-gray-900 dark:bg-gray-900 dark:text-gray-100 ${
              front === id ? "border-blue-400 shadow-lg dark:border-blue-700" : "border-gray-300 dark:border-gray-700"
            }`}
          >
            <header
              onPointerDown={(e) => start(e, id, "move")}
              className={`flex min-h-8 flex-wrap items-center gap-2 border-b border-gray-200 bg-gray-50 py-1 pl-3 pr-1 dark:border-gray-700 dark:bg-gray-950 ${narrow ? "" : "cursor-grab touch-none select-none"}`}
            >
              {content.titleNode ?? <span className="whitespace-nowrap text-xs font-semibold text-gray-800 dark:text-gray-100">{PANEL_TITLES[id]}</span>}
              <div className="flex min-w-0 flex-1 flex-wrap items-center gap-2">{content.tools}</div>
              <button
                type="button"
                aria-label={`Ocultar ${PANEL_TITLES[id]}`}
                title="Ocultar"
                onClick={() => onChange({ ...layout, [id]: { ...layout[id], hidden: true } })}
                className="ml-auto h-6 w-6 rounded text-base leading-none text-gray-500 hover:bg-red-100 hover:text-red-700 dark:hover:bg-red-900/40"
              >
                ×
              </button>
            </header>
            <div className={`flex min-h-0 flex-1 flex-col overflow-auto ${narrow ? (id === "chart" ? "h-80" : "max-h-96") : ""}`}>{content.body}</div>
            {!narrow &&
              handles.map((h) => (
                <i key={h.dir} aria-hidden="true" onPointerDown={(e) => start(e, id, h.dir)} className={`absolute z-10 touch-none ${h.cls}`} />
              ))}
          </section>
        );
      })}
    </div>
  );
}

export function persistLayout(next: Layout, set: (l: Layout) => void) {
  set(next);
  saveLayout(next);
}
