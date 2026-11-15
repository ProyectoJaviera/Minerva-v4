import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { mockFetch, withQueryClient } from "../testUtils";
import { DEFAULT_LAYOUT, LAYOUT_KEY, loadLayout } from "./layout";
import { SalaTab } from "./SalaTab";
import { ema, frostFibonacci } from "./indicators";

// jsdom no tiene canvas: el grafico se reemplaza por un doble que registra lo que recibe.
const chartCalls = vi.hoisted(() => ({ candles: 0, priceLines: [] as string[] }));
vi.mock("lightweight-charts", () => {
  const series = () => ({
    setData: () => { chartCalls.candles += 1; },
    update: () => {},
    applyOptions: () => {},
    createPriceLine: (o: { title: string }) => { chartCalls.priceLines.push(o.title); return o; },
    removePriceLine: () => {},
  });
  return {
    LineStyle: { Solid: 0, Dotted: 1, Dashed: 2 },
    createChart: () => ({
      addCandlestickSeries: series,
      addLineSeries: series,
      applyOptions: () => {},
      timeScale: () => ({ fitContent: () => {} }),
      remove: () => {},
    }),
  };
});

const T0 = Date.UTC(2026, 10, 15, 12, 0);
const POSITION = {
  id: 7, symbol: "BTCUSDT", side: "LONG", entry_time_ms: T0 - 3_600_000, entry_price: 65000, qty: 0.0015,
  qty_initial: 0.0015, margin_usdt: 10, leverage: 10, stop: 64350, tp: 66300, liq: 58800, fee_entry_usdt: 0.06,
  price: 65200, price_ts_ms: T0 - 1000, notional: 97.8, upnl: 0.3, roe_pct: 3, net_if_closed: 0.12, realized: 0,
  size_pct: 100, to_sl_pct: 1.3, to_tp_pct: 1.7, to_liq_pct: 9.8, no_sl: false, kind: "market",
};
const STATE = {
  now_ms: T0,
  runner: { enabled: true, bot_on: true, alive: true },
  account: { balance: 1001.2, equity: 1001.5, available: 980.1, used_margin: 20, upnl: 0.3, realized_today: 1.2,
    start_balance: 1000, reset_at_ms: 0 },
  positions: [POSITION],
  orders: [{ id: 9, symbol: "BTCUSDT", side: "SHORT", kind: "limit", price: 66000, margin_usdt: 10, leverage: 5,
    tp_pct: null, sl_pct: 2, created_ms: T0 - 60_000, last_price: 65200 }],
  trades: [{ id: 1, symbol: "ETHUSDT", side: "LONG", entry_price: 1800, exit_price: 1820, exit_time_ms: T0 - 7_200_000,
    leverage: 10, pnl_net_usdt: 1.2, close_reason: "TP" }],
  recent: [{ ts_ms: T0 - 2000, price: 65200.5, qty: 0.012, side: "buy" }],
  prices: { BTCUSDT: { price: 65200, ts_ms: T0 - 1000 }, ETHUSDT: { price: 1830.5, ts_ms: T0 - 1000 } },
  events: [],
  fees: { taker_pct: 0.0006, slippage_bps: 5 },
};
const CANDLES = Array.from({ length: 60 }, (_, i) => ({ t: T0 - (60 - i) * 900_000, o: 65000 + i, h: 65100 + i, l: 64900 + i, c: 65050 + i, v: 1 }));
const MARKET = {
  symbol: "BTCUSDT", interval: "15m", candles: CANDLES, funding: 0.0001, errors: {},
  depth: { bids: [[65199.9, 1.2], [65199.8, 0.4]], asks: [[65200.1, 0.8], [65200.2, 2.1]] },
  ticker: { symbol: "BTCUSDT", lastPrice: "65200", open: "64000", high: "66000", low: "63500", markPrice: "65201" },
};

function routes(extra = {}) {
  return mockFetch({
    "/api/sala/state": { status: 200, body: STATE },
    "/api/sala/market": { status: 200, body: MARKET },
    ...extra,
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
  chartCalls.candles = 0;
  chartCalls.priceLines = [];
});

describe("SalaTab", () => {
  it("muestra los siete paneles con la cuenta, el libro, las posiciones y el gráfico", async () => {
    vi.stubGlobal("fetch", routes());
    render(withQueryClient(<SalaTab />));
    const positions = await screen.findByRole("table", { name: "Posiciones manuales" });
    expect(positions).toHaveTextContent("BTCUSDT");
    expect(positions).toHaveTextContent("+0.300 USDT");
    for (const name of ["Gráfico", "Libro de órdenes", "Operaciones del mercado", "Monedas", "Crear operación", "Cuenta paper manual", "Posiciones y órdenes"]) {
      expect(screen.getByRole("region", { name })).toBeInTheDocument();
    }
    // en modo oscuro todo el texto de los paneles y de la barra toma el color claro del tema
    for (const region of screen.getAllByRole("region")) expect(region).toHaveClass("dark:text-gray-100");
    const account = screen.getByRole("region", { name: "Cuenta paper manual" });
    expect(account).toHaveTextContent("1001.50 USDT");
    expect(account).toHaveTextContent("980.10 USDT");
    await waitFor(() => expect(screen.getByRole("region", { name: "Libro de órdenes" })).toHaveTextContent("65,200.1"));
    expect(screen.getByRole("region", { name: "Operaciones del mercado" })).toHaveTextContent("65,200.5");
    await waitFor(() => expect(chartCalls.candles).toBeGreaterThan(0));
    // la posicion se dibuja en el grafico (entrada, TP, SL y liquidacion) y tambien la orden limite
    expect(chartCalls.priceLines).toEqual(expect.arrayContaining(["Largo 10x", "TP", "SL", "Liq.", "Límite corto"]));
  });

  it("crea una orden a mercado con TP y SL y muestra el error del servidor", async () => {
    let calls = 0;
    const fetchMock = routes({
      "POST /api/sala/orders": () => (++calls === 1
        ? { status: 400, body: { detail: "No alcanza el disponible (980.10 USDT)." } }
        : { status: 200, body: { order: { id: 10 } } }),
    });
    vi.stubGlobal("fetch", fetchMock);
    render(withQueryClient(<SalaTab />));
    const form = await screen.findByRole("form", { name: "Crear operación" });
    fireEvent.change(within(form).getByLabelText(/Margen por operación/), { target: { value: "25" } });
    fireEvent.change(screen.getByLabelText("Apalancamiento"), { target: { value: "20" } });
    fireEvent.click(within(form).getByRole("button", { name: "Abrir corto" }));
    expect(await within(form).findByRole("alert")).toHaveTextContent("No alcanza el disponible");
    fireEvent.click(within(form).getByRole("button", { name: "Abrir largo" }));
    expect(await within(form).findByRole("status")).toHaveTextContent("Orden enviada: largo BTCUSDT");
    const posts = fetchMock.mock.calls.filter(([, init]) => (init as RequestInit | undefined)?.method === "POST");
    expect(JSON.parse(String((posts[1][1] as RequestInit).body))).toEqual({
      symbol: "BTCUSDT", side: "LONG", kind: "market", margin_usdt: 25, leverage: 20, price: null, tp_pct: 2, sl_pct: 1,
      take_profits: [], breakeven: false, trail_pct: null,
    });
  });

  it("arma una orden con TP parciales, SL a la entrada y trailing", async () => {
    const fetchMock = routes({ "POST /api/sala/orders": { status: 200, body: { order: { id: 11 } } } });
    vi.stubGlobal("fetch", fetchMock);
    render(withQueryClient(<SalaTab />));
    const form = await screen.findByRole("form", { name: "Crear operación" });
    fireEvent.change(within(form).getByLabelText("Toma de ganancias"), { target: { value: "partial" } });
    fireEvent.change(within(form).getByLabelText("TP1: % que cierra"), { target: { value: "40" } });
    expect(within(form).getByText(/Cierran 90 %; el 10 % sigue con el SL/)).toBeInTheDocument();
    fireEvent.click(within(form).getByText("Agregar nivel"));
    fireEvent.change(within(form).getByLabelText("TP3: distancia %"), { target: { value: "5" } });
    fireEvent.change(within(form).getByLabelText("TP3: % que cierra"), { target: { value: "10" } });
    expect(within(form).getByText("Cierran el 100 %")).toBeInTheDocument();
    expect(within(form).getByText("Agregar nivel")).toBeDisabled();
    fireEvent.change(within(form).getByLabelText(/Trailing %/), { target: { value: "0.8" } });
    fireEvent.click(within(form).getByRole("button", { name: "Abrir corto" }));
    expect(await within(form).findByRole("status")).toHaveTextContent("corto BTCUSDT");
    const post = fetchMock.mock.calls.find(([, init]) => (init as RequestInit | undefined)?.method === "POST");
    expect(JSON.parse(String((post?.[1] as RequestInit).body))).toMatchObject({
      side: "SHORT", tp_pct: null, sl_pct: 1, breakeven: true, trail_pct: 0.8,
      take_profits: [[1.5, 40], [3, 50], [5, 10]],
    });
  });

  it("muestra los TP parciales (con el ya tocado tachado) y el trailing de una posición", async () => {
    const partial = { ...POSITION, tp: null, tp_levels: [[65650, 0.5, true], [66300, 0.5, false]], stop: 65100,
      trailing: 650, trail_pct: 1, breakeven: true };
    vi.stubGlobal("fetch", routes({ "/api/sala/state": { status: 200, body: { ...STATE, positions: [partial] } } }));
    render(withQueryClient(<SalaTab />));
    const table = await screen.findByRole("table", { name: "Posiciones manuales" });
    expect(table).toHaveTextContent("TP1 65,650.0 (50 %) ✓");
    expect(table).toHaveTextContent("TP2 66,300.0 (50 %)");
    expect(table).toHaveTextContent("Trailing 65,100.0");
    expect(table).toHaveTextContent("trailing 1 %");
    await waitFor(() => expect(chartCalls.priceLines).toEqual(expect.arrayContaining(["TP2", "Trailing"])));
    expect(chartCalls.priceLines).not.toContain("TP1");
  });

  it("cierra el 50 %, cancela una orden y pide CERRAR para cerrar todo", async () => {
    const fetchMock = routes({
      "POST /api/sala/positions/7/close": { status: 200, body: { closed: 7 } },
      "POST /api/sala/orders/9/cancel": { status: 200, body: { cancelled: 9 } },
      "POST /api/sala/close-all": { status: 200, body: { closed: 1 } },
    });
    vi.stubGlobal("fetch", fetchMock);
    render(withQueryClient(<SalaTab />));
    fireEvent.click(await screen.findByRole("button", { name: "Cerrar 50 %" }));
    await waitFor(() => expect(fetchMock.mock.calls.some(([u]) => String(u) === "/api/sala/positions/7/close")).toBe(true));
    const close = fetchMock.mock.calls.find(([u]) => String(u) === "/api/sala/positions/7/close");
    expect(JSON.parse(String((close?.[1] as RequestInit).body))).toEqual({ fraction: 0.5 });
    fireEvent.click(screen.getByRole("tab", { name: /Órdenes \(1\)/ }));
    fireEvent.click(await screen.findByRole("button", { name: "Cancelar" }));
    await waitFor(() => expect(fetchMock.mock.calls.some(([u]) => String(u) === "/api/sala/orders/9/cancel")).toBe(true));
    fireEvent.click(screen.getByRole("button", { name: "Cerrar todo" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByRole("button", { name: "Cerrar todo" })).toBeDisabled();
    fireEvent.change(within(dialog).getByRole("textbox"), { target: { value: "CERRAR" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Cerrar todo" }));
    await waitFor(() => expect(fetchMock.mock.calls.some(([u]) => String(u) === "/api/sala/close-all")).toBe(true));
  });

  it("oculta un panel con la x, lo vuelve a mostrar desde «Paneles» y guarda la disposición", async () => {
    vi.stubGlobal("fetch", routes());
    render(withQueryClient(<SalaTab />));
    await screen.findByRole("region", { name: "Libro de órdenes" });
    fireEvent.click(screen.getByRole("button", { name: "Ocultar Libro de órdenes" }));
    expect(screen.queryByRole("region", { name: "Libro de órdenes" })).not.toBeInTheDocument();
    expect(JSON.parse(localStorage.getItem(LAYOUT_KEY) ?? "{}").book.hidden).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: /Paneles/ }));
    const menu = screen.getByRole("menu");
    expect(within(menu).getByText("oculto")).toBeInTheDocument();
    fireEvent.click(within(menu).getByLabelText(/Libro de órdenes/));
    expect(screen.getByRole("region", { name: "Libro de órdenes" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Ocultar Monedas" }));
    // el menu sigue abierto: «Restablecer disposición» vuelve todo a su lugar
    fireEvent.click(within(screen.getByRole("menu")).getByRole("button", { name: "Restablecer disposición" }));
    expect(screen.getByRole("region", { name: "Monedas" })).toBeInTheDocument();
    expect(JSON.parse(localStorage.getItem(LAYOUT_KEY) ?? "{}")).toEqual(DEFAULT_LAYOUT);
  });

  it("avisa si el bot está apagado", async () => {
    vi.stubGlobal("fetch", routes({ "/api/sala/state": { status: 200, body: { ...STATE, runner: { enabled: true, bot_on: false, alive: true } } } }));
    render(withQueryClient(<SalaTab />));
    expect(await screen.findByRole("alert")).toHaveTextContent("El bot está apagado");
  });
});

describe("disposición e indicadores de la Sala", () => {
  it("una disposición guardada rota o incompleta cae en la de por defecto", () => {
    localStorage.setItem(LAYOUT_KEY, "{no es json");
    expect(loadLayout()).toEqual(DEFAULT_LAYOUT);
    localStorage.setItem(LAYOUT_KEY, JSON.stringify({ chart: { x: 0.1, y: 0, w: 0.5, h: 0.5, hidden: true }, book: { x: "a" } }));
    const l = loadLayout();
    expect(l.chart).toEqual({ x: 0.1, y: 0, w: 0.5, h: 0.5, hidden: true });
    expect(l.book).toEqual(DEFAULT_LAYOUT.book);
  });

  it("la EMA arranca con la media simple y el Fibonacci usa los dos últimos pivotes fijos", () => {
    expect(ema([1, 2, 3, 4], 3)).toEqual([null, null, 2, 3]);
    // mismo caso a mano que el test del backend (periodo 3)
    const highs = [10, 11, 12, 11, 10, 12, 14, 13];
    const lows = [9, 10, 11, 10, 8, 11, 12, 9.5];
    const f = frostFibonacci(highs.map((h, i) => ({ t: i, o: 0, h, l: lows[i], c: 0, v: 0 })), 3);
    expect(f?.v1).toBe(14);
    expect(f?.v2).toBe(8);
    expect(f?.levels.find(([r]) => r === 0.5)?.[1]).toBe(11);
  });
});
