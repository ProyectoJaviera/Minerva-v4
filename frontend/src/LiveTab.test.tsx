import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { LiveTab } from "./LiveTab";
import { mockFetch, withQueryClient } from "./testUtils";

const OPTIONS = {
  enabled: true,
  indicators: [
    { id: "rf", label: "Range Filter", kind: "estado" },
    { id: "ema", label: "EMA 144", kind: "estado" },
  ],
  exits: [{ id: "s2_tp2r", label: "SL 2 ATR, TP 2R" }, { id: "pct", label: "Personalizada en %" }],
  timeframes: ["15m", "1h", "4h", "1d", "1w"],
  modes: ["close", "intrabar"],
  default_symbols: ["BTCUSDT", "ETHUSDT"],
  max_symbols: 30, max_window: 50, default_window: 5,
  eval_seconds: { min: 5, max: 3600, default: 60 },
  margin: { min: 1, max: 100000, default: 5 },
  leverage: { min: 1, max: 100, default: 10 },
  directions: ["both", "long", "short"],
  max_tp_levels: 3,
  fees: { taker_pct: 0.0006, slippage_bps: 5 },
  max_sl_margin_loss_pct: 50,
};

const SUMMARY = { trades: 3, wins: 2, winrate: 0.6667, pf: 1.8, net: 0.42, fees: 0.12, avg_entry_delay_ms: 900 };
const TEST = {
  id: 1, status: "active", created_at_ms: Date.UTC(2026, 10, 11, 20, 0), stopped_at_ms: null,
  config: {
    name: "RF + EMA 15m", symbols: ["BTCUSDT", "ETHUSDT"], timeframe: "15m", indicators: ["rf", "ema"],
    mode: "intrabar", exit_name: "s2_tp2r", pct_exit: null, window: 5, min_agree: null, eval_seconds: 60,
  },
  summary: SUMMARY, open_positions: 1,
};
const TESTS = {
  enabled: true,
  runner: {
    alive: true, last_beat_ms: Date.UTC(2026, 10, 11, 20, 30), detail: "1 pruebas, 1 posiciones",
    bot_on: true, switch_changed_ms: null,
  },
  tests: [TEST],
};
const DETAIL = {
  ...TEST,
  positions: [{ symbol: "BTCUSDT", side: "LONG", entry_time_ms: Date.UTC(2026, 10, 11, 20, 10), entry_price: 74000, stop: 73500, tp: 75000, trailing: null }],
  trades: [{
    symbol: "ETHUSDT", side: "SHORT", entry_time_ms: 1, exit_time_ms: Date.UTC(2026, 10, 11, 20, 20),
    entry_price: 2080, exit_price: 2060, pnl_net_usdt: 0.9, close_reason: "TP", signal_time_ms: 0, decision_price: 2080,
  }],
  events: [{ ts_ms: Date.UTC(2026, 10, 11, 20, 10), symbol: "BTCUSDT", kind: "OPEN", detail: "LONG a 74000" }],
};

afterEach(() => vi.unstubAllGlobals());

describe("LiveTab", () => {
  it("muestra el corredor, cada prueba con su resumen y el detalle al pedirlo", async () => {
    vi.stubGlobal("fetch", mockFetch({
      "/api/live/options": { status: 200, body: OPTIONS },
      "/api/live/tests": { status: 200, body: TESTS },
      "/api/live/tests/1": { status: 200, body: DETAIL },
    }));
    render(withQueryClient(<LiveTab />));
    expect(await screen.findByText("[1] RF + EMA 15m")).toBeInTheDocument();
    expect(screen.getByRole("status", { name: "Estado del bot" })).toHaveTextContent("Bot activo");
    expect(screen.getByText("1.80")).toBeInTheDocument();
    expect(screen.getByText("+0.42 USDT")).toBeInTheDocument();
    expect(screen.getByText("67%")).toBeInTheDocument();
    fireEvent.click(screen.getByText("Ver detalle"));
    expect(await screen.findByText(/BTCUSDT LONG a 74000 desde/)).toBeInTheDocument();
    expect(screen.getByText("ETHUSDT")).toBeInTheDocument();
    expect(screen.getByText(/OPEN BTCUSDT LONG a 74000/)).toBeInTheDocument();
  });

  it("crea una prueba con los indicadores elegidos", async () => {
    const fetchMock = mockFetch({
      "/api/live/options": { status: 200, body: OPTIONS },
      "/api/live/tests": { status: 200, body: { ...TESTS, tests: [] } },
      "POST /api/live/tests": { status: 200, body: { id: 7, name: "Mi prueba" } },
    });
    vi.stubGlobal("fetch", fetchMock);
    render(withQueryClient(<LiveTab />));
    expect(await screen.findByText(/Todavía no hay pruebas/)).toBeInTheDocument();
    const button = screen.getByRole("button", { name: "Crear prueba" });
    expect(button).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Nombre"), { target: { value: "Mi prueba" } });
    fireEvent.click(screen.getByLabelText(/Range Filter/));
    fireEvent.change(screen.getByLabelText("Modo"), { target: { value: "intrabar" } });
    fireEvent.click(button);
    expect(await screen.findByText(/Prueba 7 creada/)).toBeInTheDocument();
    const post = fetchMock.mock.calls.find(([, init]) => (init as RequestInit | undefined)?.method === "POST");
    const body = JSON.parse(String((post?.[1] as RequestInit).body));
    expect(body).toMatchObject({
      name: "Mi prueba", indicators: ["rf"], mode: "intrabar", timeframe: "15m",
      symbols: ["BTCUSDT", "ETHUSDT"], exit_name: "pct", sl_pct: 1, tp_pct: 2, eval_seconds: 60,
    });
  });

  it("muestra el motivo si el servidor rechaza la prueba", async () => {
    vi.stubGlobal("fetch", mockFetch({
      "/api/live/options": { status: 200, body: OPTIONS },
      "/api/live/tests": { status: 200, body: { ...TESTS, tests: [] } },
      "POST /api/live/tests": { status: 422, body: { detail: "monedas invalidas: ['BTC']" } },
    }));
    render(withQueryClient(<LiveTab />));
    fireEvent.change(await screen.findByLabelText("Nombre"), { target: { value: "x" } });
    fireEvent.click(screen.getByLabelText(/EMA 144/));
    fireEvent.click(screen.getByRole("button", { name: "Crear prueba" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("monedas invalidas");
  });

  it("detiene una prueba solo despues de escribir DETENER", async () => {
    const fetchMock = mockFetch({
      "/api/live/options": { status: 200, body: OPTIONS },
      "/api/live/tests": { status: 200, body: TESTS },
      "POST /api/live/tests/1/stop": { status: 200, body: { id: 1, stopped: true } },
    });
    vi.stubGlobal("fetch", fetchMock);
    render(withQueryClient(<LiveTab />));
    fireEvent.click(await screen.findByRole("button", { name: "Detener" }));
    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveTextContent("posiciones abiertas se cierran");
    fireEvent.change(screen.getByRole("textbox", { name: /DETENER/ }), { target: { value: "detener" } });
    const stopButtons = screen.getAllByRole("button", { name: "Detener" });
    fireEvent.click(stopButtons[stopButtons.length - 1]);
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    const post = fetchMock.mock.calls.find(([url]) => String(url).endsWith("/stop"));
    expect(JSON.parse(String((post?.[1] as RequestInit).body))).toEqual({ confirm: "DETENER" });
  });

  it("avisa si el corredor esta apagado en esta instancia", async () => {
    vi.stubGlobal("fetch", mockFetch({
      "/api/live/options": { status: 200, body: { ...OPTIONS, enabled: false } },
      "/api/live/tests": { status: 200, body: { ...TESTS, enabled: false, tests: [] } },
    }));
    render(withQueryClient(<LiveTab />));
    expect(await screen.findByText(/corredor de pruebas en vivo está apagado/)).toBeInTheDocument();
    expect(screen.getByRole("status", { name: "Estado del bot" })).toHaveTextContent("no disponible");
    expect(screen.queryByRole("button", { name: /bot/ })).not.toBeInTheDocument();
  });

  it("apaga el bot solo despues de escribir APAGAR", async () => {
    const fetchMock = mockFetch({
      "/api/live/options": { status: 200, body: OPTIONS },
      "/api/live/tests": { status: 200, body: TESTS },
      "POST /api/live/bot": { status: 200, body: { bot_on: false, enabled: true } },
    });
    vi.stubGlobal("fetch", fetchMock);
    render(withQueryClient(<LiveTab />));
    fireEvent.click(await screen.findByRole("button", { name: "Apagar bot" }));
    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveTextContent("cierra todas las posiciones abiertas");
    const confirm = screen.getByRole("button", { name: "Apagar" });
    expect(confirm).toBeDisabled();
    fireEvent.change(screen.getByRole("textbox", { name: /APAGAR/ }), { target: { value: "apagar" } });
    fireEvent.click(confirm);
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    const post = fetchMock.mock.calls.find(([url]) => String(url).endsWith("/api/live/bot"));
    expect(JSON.parse(String((post?.[1] as RequestInit).body))).toEqual({ on: false, confirm: "APAGAR" });
  });

  it("muestra el bot apagado y lo enciende con un clic", async () => {
    const fetchMock = mockFetch({
      "/api/live/options": { status: 200, body: OPTIONS },
      "/api/live/tests": { status: 200, body: { ...TESTS, runner: { ...TESTS.runner, bot_on: false } } },
      "POST /api/live/bot": { status: 200, body: { bot_on: true, enabled: true } },
    });
    vi.stubGlobal("fetch", fetchMock);
    render(withQueryClient(<LiveTab />));
    expect(await screen.findByRole("status", { name: "Estado del bot" })).toHaveTextContent("Bot apagado");
    fireEvent.click(screen.getByRole("button", { name: "Encender bot" }));
    await waitFor(() =>
      expect(fetchMock.mock.calls.some(([url]) => String(url).endsWith("/api/live/bot"))).toBe(true),
    );
    const post = fetchMock.mock.calls.find(([url]) => String(url).endsWith("/api/live/bot"));
    expect(JSON.parse(String((post?.[1] as RequestInit).body))).toEqual({ on: true, confirm: "" });
  });

  it("avisa si el bot no late", async () => {
    vi.stubGlobal("fetch", mockFetch({
      "/api/live/options": { status: 200, body: OPTIONS },
      "/api/live/tests": { status: 200, body: { ...TESTS, runner: { ...TESTS.runner, alive: false } } },
    }));
    render(withQueryClient(<LiveTab />));
    expect(await screen.findByRole("status", { name: "Estado del bot" })).toHaveTextContent("sin respuesta");
    expect(screen.getByText(/el contenedor puede estar detenido/)).toBeInTheDocument();
  });

  it("arma un bot con margen, apalancamiento, direccion y TP parciales", async () => {
    const fetchMock = mockFetch({
      "/api/live/options": { status: 200, body: OPTIONS },
      "/api/live/tests": { status: 200, body: { ...TESTS, tests: [] } },
      "POST /api/live/tests": { status: 200, body: { id: 8, name: "Bot real" } },
    });
    vi.stubGlobal("fetch", fetchMock);
    render(withQueryClient(<LiveTab />));
    fireEvent.change(await screen.findByLabelText("Nombre"), { target: { value: "Bot real" } });
    fireEvent.click(screen.getByLabelText(/Range Filter/));
    fireEvent.change(screen.getByLabelText("Dirección"), { target: { value: "long" } });
    fireEvent.change(screen.getByLabelText("Margen por operación (USDT)"), { target: { value: "20" } });
    fireEvent.change(screen.getByLabelText(/Apalancamiento/), { target: { value: "5" } });
    expect(screen.getByText(/20.00 USDT × 5x = 100.00 USDT/)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("SL %"), { target: { value: "2" } });
    expect(screen.getByText(/pierdes 10.0 % del margen \(2.00 USDT/)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Toma de ganancias"), { target: { value: "partial" } });
    fireEvent.change(screen.getByLabelText("TP1: % que cierra"), { target: { value: "40" } });
    expect(screen.getByText(/Cierran en total 90 %; el 10 % restante/)).toBeInTheDocument();
    fireEvent.click(screen.getByText("Agregar nivel"));
    fireEvent.change(screen.getByLabelText("TP3: distancia %"), { target: { value: "5" } });
    fireEvent.change(screen.getByLabelText("TP3: % que cierra"), { target: { value: "10" } });
    expect(screen.queryByText("Agregar nivel")).not.toBeInTheDocument();
    fireEvent.click(screen.getByLabelText(/Mover el SL al precio de entrada/));
    fireEvent.change(screen.getByLabelText("Máximo de posiciones abiertas (vacío = sin tope)"), { target: { value: "2" } });
    fireEvent.click(screen.getByRole("button", { name: "Crear prueba" }));
    expect(await screen.findByText(/Prueba 8 creada/)).toBeInTheDocument();
    const post = fetchMock.mock.calls.find(([, init]) => (init as RequestInit | undefined)?.method === "POST");
    expect(JSON.parse(String((post?.[1] as RequestInit).body))).toMatchObject({
      direction: "long", margin_usdt: 20, leverage: 5, exit_name: "pct", sl_pct: 2, tp_pct: null,
      take_profits: [[1.5, 40], [3, 50], [5, 10]], breakeven: true, max_open: 2,
    });
  });
});
