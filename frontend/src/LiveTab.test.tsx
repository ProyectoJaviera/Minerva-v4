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

afterEach(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
});

// Abre el detalle de una prueba desde la tabla resumen.
async function expand(name: RegExp | string) {
  fireEvent.click(await screen.findByRole("button", { name: typeof name === "string" ? new RegExp(name.replace(/[[\]+]/g, (c) => `\\${c}`)) : name }));
}

describe("LiveTab", () => {
  it("muestra el corredor, cada prueba con su resumen y el detalle al pedirlo", async () => {
    vi.stubGlobal("fetch", mockFetch({
      "/api/live/options": { status: 200, body: OPTIONS },
      "/api/live/tests": { status: 200, body: TESTS },
      "/api/live/tests/1": { status: 200, body: DETAIL },
    }));
    render(withQueryClient(<LiveTab />));
    const table = await screen.findByRole("table", { name: "Resumen de las pruebas" });
    expect(table).toHaveTextContent("[1] RF + EMA 15m");
    expect(screen.getByRole("status", { name: "Estado del bot" })).toHaveTextContent("Bot activo");
    expect(table).toHaveTextContent("1.80");
    expect(table).toHaveTextContent("+0.42 USDT");
    expect(table).toHaveTextContent("67%");
    expect(screen.queryByText("Ver operaciones y eventos")).not.toBeInTheDocument();
    await expand("[1] RF + EMA 15m");
    fireEvent.click(screen.getByText("Ver operaciones y eventos"));
    expect(await screen.findByText(/BTCUSDT LONG a 74000 desde/)).toBeInTheDocument();
    expect(screen.getByText("ETHUSDT")).toBeInTheDocument();
    expect(screen.getByText(/OPEN BTCUSDT LONG a 74000/)).toBeInTheDocument();
  });

  it("muestra la prueba contra su gemelo al azar", async () => {
    const withControl = {
      ...TEST,
      control: {
        id: 2, name: "Azar: RF + EMA 15m", status: "active", created_at_ms: TEST.created_at_ms,
        summary: { ...SUMMARY, trades: 5, pf: 0.9, net: -0.31 }, open_positions: 0,
      },
      rate: { eligible_bars: 40, signals: 4, rate: 0.1 },
      vs_control: {
        verdict: "pocas_operaciones", strategy_mean: 0.14, control_mean: -0.062, diff: 0.202, t: 1.1,
        min_trades: 30, strategy_trades: 3, control_trades: 5,
      },
    };
    vi.stubGlobal("fetch", mockFetch({
      "/api/live/options": { status: 200, body: OPTIONS },
      "/api/live/tests": { status: 200, body: { ...TESTS, tests: [withControl] } },
    }));
    render(withQueryClient(<LiveTab />));
    const table = await screen.findByRole("table", { name: "Resumen de las pruebas" });
    expect(table).toHaveTextContent("+0.202 USDT");
    expect(table).toHaveTextContent("gemelo -0.31 USDT");
    await expand("[1] RF + EMA 15m");
    const panel = await screen.findByRole("region", { name: "Contra el azar" });
    expect(panel).toHaveTextContent("Faltan operaciones para decir algo");
    expect(panel).toHaveTextContent("-0.31 USDT");
    expect(panel).toHaveTextContent("+0.202 USDT");
    expect(panel).toHaveTextContent("4 señales en 40 velas");
    expect(panel).toHaveTextContent("al menos 30 operaciones");
  });

  it("dice si la prueba le gana al azar", async () => {
    const wins = {
      ...TEST,
      control: { id: 2, name: "Azar: x", status: "active", created_at_ms: 0, summary: SUMMARY, open_positions: 0 },
      rate: { eligible_bars: 400, signals: 40, rate: 0.1 },
      vs_control: {
        verdict: "le_gana", strategy_mean: 0.3, control_mean: 0.0, diff: 0.3, t: 2.4,
        min_trades: 30, strategy_trades: 40, control_trades: 38,
      },
    };
    vi.stubGlobal("fetch", mockFetch({
      "/api/live/options": { status: 200, body: OPTIONS },
      "/api/live/tests": { status: 200, body: { ...TESTS, tests: [wins] } },
    }));
    render(withQueryClient(<LiveTab />));
    expect(await screen.findByRole("table", { name: "Resumen de las pruebas" })).toHaveTextContent("le gana al azar");
    await expand("[1] RF + EMA 15m");
    const panel = await screen.findByRole("region", { name: "Contra el azar" });
    expect(panel).toHaveTextContent("Le gana al azar");
    expect(panel).toHaveTextContent("t = 2.40");
  });

  it("muestra las posiciones abiertas en vivo, como en un exchange", async () => {
    const now = Date.now();
    const position = {
      test_id: 1, is_control: false, symbol: "XRPUSDT", side: "SHORT", entry_time_ms: now - 90 * 60_000,
      entry_price: 1.0361, qty: 96.5, qty_initial: 96.5, margin_usdt: 10, leverage: 10, stop: 1.0538,
      liq: 1.13, tp: null, tp_levels: [[1.02056, 0.5, false], [1.00502, 0.5, false]], trailing: 0.0207,
      price: 1.0301, price_ts_ms: now - 1000, price_stale: false, size_pct: 100, next_tp: 1.02056,
      realized: 0, funding: 0, notional: 99.4, upnl: 0.579, roe_pct: 5.79, net_if_closed: 0.46,
      to_sl_pct: 2.3, to_tp_pct: 0.93, to_liq_pct: 9.7,
    };
    vi.stubGlobal("fetch", mockFetch({
      "/api/live/options": { status: 200, body: OPTIONS },
      "/api/live/tests": { status: 200, body: TESTS },
      "/api/live/positions": { status: 200, body: { now_ms: now, positions: [position] } },
    }));
    render(withQueryClient(<LiveTab />));
    const summary = await screen.findByRole("table", { name: "Resumen de las pruebas" });
    await waitFor(() => expect(summary).toHaveTextContent("+0.579 USDT"));
    await expand("[1] RF + EMA 15m");
    expect(screen.queryByRole("table", { name: "Posiciones abiertas" })).not.toBeInTheDocument();
    fireEvent.click(await screen.findByRole("button", { name: /Posiciones abiertas \(1\)/ }));
    const table = await screen.findByRole("table", { name: "Posiciones abiertas" });
    expect(table).toHaveTextContent("XRPUSDT");
    expect(table).toHaveTextContent("Corto 10x");
    expect(table).toHaveTextContent("1.0301");
    expect(table).toHaveTextContent("+0.579 USDT");
    expect(table).toHaveTextContent("+5.79 %");
    expect(table).toHaveTextContent("a 0.93 %");
    expect(table).toHaveTextContent("1 h 30 min");
    expect(screen.getByText(/se actualiza cada 2 s/)).toBeInTheDocument();
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

  it("elige el modo del Range Filter (Intraday, Scalping o Swing)", async () => {
    const fetchMock = mockFetch({
      "/api/live/options": {
        status: 200,
        body: {
          ...OPTIONS,
          indicators: [
            { id: "rf", label: "RF Intraday", kind: "estado", group: "rf", variant: "Intraday Mode", group_label: "Range Filter (Frost): estado alcista / bajista" },
            { id: "rf_scalp", label: "RF Scalping", kind: "estado", group: "rf", variant: "Scalping Mode", group_label: "Range Filter (Frost): estado alcista / bajista" },
            { id: "rf_swing", label: "RF Swing", kind: "estado", group: "rf", variant: "Swing Mode", group_label: "Range Filter (Frost): estado alcista / bajista" },
            { id: "ema", label: "EMA 144", kind: "estado" },
          ],
        },
      },
      "/api/live/tests": { status: 200, body: { ...TESTS, tests: [] } },
      "POST /api/live/tests": { status: 200, body: { id: 8, name: "RF swing" } },
    });
    vi.stubGlobal("fetch", fetchMock);
    render(withQueryClient(<LiveTab />));
    fireEvent.change(await screen.findByLabelText("Nombre"), { target: { value: "RF swing" } });
    expect(screen.getAllByRole("checkbox")).toHaveLength(2);
    fireEvent.click(screen.getByLabelText("Range Filter (Frost): estado alcista / bajista"));
    fireEvent.change(screen.getByLabelText("Modo del Range Filter"), { target: { value: "rf_swing" } });
    fireEvent.click(screen.getByRole("button", { name: "Crear prueba" }));
    expect(await screen.findByText(/Prueba 8 creada/)).toBeInTheDocument();
    const post = fetchMock.mock.calls.find(([, init]) => (init as RequestInit | undefined)?.method === "POST");
    expect(JSON.parse(String((post?.[1] as RequestInit).body)).indicators).toEqual(["rf_swing"]);
  });

  it("elige el par de EMAs que se cruzan (50 / 200) y lo manda como un solo indicador", async () => {
    const pairs = [[9, 21], [9, 50], [21, 50], [50, 200]];
    const fetchMock = mockFetch({
      "/api/live/options": {
        status: 200,
        body: {
          ...OPTIONS,
          indicators: [
            { id: "ema200", label: "Cierre sobre / bajo la EMA 200", kind: "estado" },
            ...pairs.map(([f, s]) => ({
              id: `emax_${f}_${s}`, label: `Cruce de EMAs ${f} / ${s}`, kind: "evento",
              group: "emax", variant: `EMA ${f} / EMA ${s}`, group_label: "Cruce de dos EMAs (largo al cruzar hacia arriba, corto hacia abajo)",
            })),
          ],
        },
      },
      "/api/live/tests": { status: 200, body: { ...TESTS, tests: [] } },
      "POST /api/live/tests": { status: 200, body: { id: 9, name: "Cruce dorado" } },
    });
    vi.stubGlobal("fetch", fetchMock);
    render(withQueryClient(<LiveTab />));
    fireEvent.change(await screen.findByLabelText("Nombre"), { target: { value: "Cruce dorado" } });
    // La EMA 200 suelta y una sola casilla para los cuatro cruces.
    expect(screen.getAllByRole("checkbox")).toHaveLength(2);
    fireEvent.click(screen.getByLabelText("Cruce de dos EMAs (largo al cruzar hacia arriba, corto hacia abajo)"));
    fireEvent.change(screen.getByLabelText("Par de EMAs"), { target: { value: "emax_50_200" } });
    expect((screen.getByLabelText("Par de EMAs") as HTMLSelectElement).value).toBe("emax_50_200");
    fireEvent.click(screen.getByRole("button", { name: "Crear prueba" }));
    expect(await screen.findByText(/Prueba 9 creada/)).toBeInTheDocument();
    const post = fetchMock.mock.calls.find(([, init]) => (init as RequestInit | undefined)?.method === "POST");
    expect(JSON.parse(String((post?.[1] as RequestInit).body)).indicators).toEqual(["emax_50_200"]);
  });

  it("elige el retesteo del Cloud de Frost con longitud 50", async () => {
    const cloud = "Cloud de Frost: cierre sobre el techo (alcista) / bajo el piso (bajista)";
    const retest = "Retesteo del Cloud de Frost: rebote en el techo (largo) o en el piso (corto)";
    const fetchMock = mockFetch({
      "/api/live/options": {
        status: 200,
        body: {
          ...OPTIONS,
          indicators: [
            { id: "cloud_retest36", label: "Retesteo 36", kind: "evento", group: "cloud_retest", variant: "Longitud 36", group_label: retest },
            { id: "cloud_retest50", label: "Retesteo 50", kind: "evento", group: "cloud_retest", variant: "Longitud 50", group_label: retest },
            { id: "cloud36", label: "Cloud 36", kind: "estado", group: "cloud", variant: "Longitud 36", group_label: cloud },
            { id: "cloud50", label: "Cloud 50", kind: "estado", group: "cloud", variant: "Longitud 50", group_label: cloud },
          ],
        },
      },
      "/api/live/tests": { status: 200, body: { ...TESTS, tests: [] } },
      "POST /api/live/tests": { status: 200, body: { id: 12, name: "Cloud" } },
    });
    vi.stubGlobal("fetch", fetchMock);
    render(withQueryClient(<LiveTab />));
    fireEvent.change(await screen.findByLabelText("Nombre"), { target: { value: "Cloud" } });
    // dos casillas (estado y retesteo), cada una con su selector de longitud
    expect(screen.getAllByRole("checkbox")).toHaveLength(2);
    fireEvent.click(screen.getByLabelText(retest));
    fireEvent.change(screen.getByLabelText("Longitud del retesteo del Cloud"), { target: { value: "cloud_retest50" } });
    fireEvent.click(screen.getByRole("button", { name: "Crear prueba" }));
    expect(await screen.findByText(/Prueba 12 creada/)).toBeInTheDocument();
    const post = fetchMock.mock.calls.find(([, init]) => (init as RequestInit | undefined)?.method === "POST");
    expect(JSON.parse(String((post?.[1] as RequestInit).body)).indicators).toEqual(["cloud_retest50"]);
  });

  it("elige el rebote en el 61,8 % de Fibonacci", async () => {
    const label = "Rebote en un nivel Fibonacci de Frost (a favor de la ultima pierna)";
    const levels = [["fib0", "0 %"], ["fib382", "38,2 %"], ["fib500", "50 %"], ["fib618", "61,8 %"], ["fib812", "81,2 %"]];
    const fetchMock = mockFetch({
      "/api/live/options": {
        status: 200,
        body: {
          ...OPTIONS,
          indicators: levels.map(([id, variant]) => ({ id, label: id, kind: "evento", group: "fib", variant, group_label: label })),
        },
      },
      "/api/live/tests": { status: 200, body: { ...TESTS, tests: [] } },
      "POST /api/live/tests": { status: 200, body: { id: 13, name: "Fibo" } },
    });
    vi.stubGlobal("fetch", fetchMock);
    render(withQueryClient(<LiveTab />));
    fireEvent.change(await screen.findByLabelText("Nombre"), { target: { value: "Fibo" } });
    expect(screen.getAllByRole("checkbox")).toHaveLength(1);
    fireEvent.change(screen.getByLabelText("Nivel de Fibonacci"), { target: { value: "fib618" } });
    fireEvent.click(screen.getByLabelText(label));
    fireEvent.click(screen.getByRole("button", { name: "Crear prueba" }));
    expect(await screen.findByText(/Prueba 13 creada/)).toBeInTheDocument();
    const post = fetchMock.mock.calls.find(([, init]) => (init as RequestInit | undefined)?.method === "POST");
    expect(JSON.parse(String((post?.[1] as RequestInit).body)).indicators).toEqual(["fib618"]);
  });

  it("elige la ruptura del 100 % de Fibonacci", async () => {
    const label = "Ruptura de un nivel Fibonacci de Frost (cierre que cruza el nivel)";
    const levels = [["fibx0", "0 %"], ["fibx618", "61,8 %"], ["fibx1000", "100 %"], ["fibx1500", "150 %"]];
    const fetchMock = mockFetch({
      "/api/live/options": {
        status: 200,
        body: {
          ...OPTIONS,
          indicators: levels.map(([id, variant]) => ({ id, label: id, kind: "evento", group: "fib_break", variant, group_label: label })),
        },
      },
      "/api/live/tests": { status: 200, body: { ...TESTS, tests: [] } },
      "POST /api/live/tests": { status: 200, body: { id: 14, name: "Ruptura" } },
    });
    vi.stubGlobal("fetch", fetchMock);
    render(withQueryClient(<LiveTab />));
    fireEvent.change(await screen.findByLabelText("Nombre"), { target: { value: "Ruptura" } });
    fireEvent.click(screen.getByLabelText(label));
    fireEvent.change(screen.getByLabelText("Nivel de la ruptura"), { target: { value: "fibx1000" } });
    fireEvent.click(screen.getByRole("button", { name: "Crear prueba" }));
    expect(await screen.findByText(/Prueba 14 creada/)).toBeInTheDocument();
    const post = fetchMock.mock.calls.find(([, init]) => (init as RequestInit | undefined)?.method === "POST");
    expect(JSON.parse(String((post?.[1] as RequestInit).body)).indicators).toEqual(["fibx1000"]);
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
    await expand("[1] RF + EMA 15m");
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

const row = (id: number, name: string, timeframe: string, status: "active" | "stopped", net: number, trades: number, indicators: string[]) => ({
  ...TEST, id, status, config: { ...TEST.config, name, timeframe, indicators },
  summary: { ...SUMMARY, net, trades, pf: trades ? 1 + net : null }, open_positions: 0,
});
const MANY = [
  row(1, "Mezcla 15m", "15m", "active", -19.87, 22, ["sfp", "fvg"]),
  row(3, "Range Filter Intraday", "4h", "active", 0, 0, ["rf"]),
  row(5, "Range Filter Scalping", "15m", "active", -5.64, 8, ["rf_scalp"]),
  row(7, "Vieja", "1h", "stopped", 3.2, 12, ["ema"]),
  row(9, "Cruce dorado", "1h", "active", 1.5, 4, ["emax_50_200"]),
];

function names(): string[] {
  const table = screen.getByRole("table", { name: "Resumen de las pruebas" });
  return [...table.querySelectorAll("tbody button[aria-expanded]")].map((b) => b.textContent?.replace(/^[▸▾]/, "") ?? "");
}

describe("LiveTab: tabla resumen", () => {
  function renderMany() {
    vi.stubGlobal("fetch", mockFetch({
      "/api/live/options": { status: 200, body: OPTIONS },
      "/api/live/tests": { status: 200, body: { ...TESTS, tests: MANY } },
    }));
    render(withQueryClient(<LiveTab />));
  }

  it("muestra solo las activas por defecto y filtra por estado, marco y busqueda", async () => {
    renderMany();
    await screen.findByRole("table", { name: "Resumen de las pruebas" });
    expect(names()).toEqual(["[1] Mezcla 15m", "[3] Range Filter Intraday", "[5] Range Filter Scalping", "[9] Cruce dorado"]);
    expect(screen.getByText("4 de 5 pruebas")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Estado"), { target: { value: "stopped" } });
    expect(names()).toEqual(["[7] Vieja"]);
    fireEvent.change(screen.getByLabelText("Estado"), { target: { value: "all" } });
    fireEvent.change(screen.getByLabelText("Marco"), { target: { value: "15m" } });
    expect(names()).toEqual(["[1] Mezcla 15m", "[5] Range Filter Scalping"]);
    fireEvent.change(screen.getByLabelText("Marco"), { target: { value: "" } });
    fireEvent.change(screen.getByLabelText("Buscar"), { target: { value: "emax" } });
    expect(names()).toEqual(["[9] Cruce dorado"]);
    fireEvent.change(screen.getByLabelText("Buscar"), { target: { value: "nada" } });
    expect(screen.getByText("Ninguna prueba cumple los filtros.")).toBeInTheDocument();
    expect(screen.getByText("0 de 5 pruebas")).toBeInTheDocument();
  });

  it("ordena al hacer clic en la cabecera (resultado de mayor a menor y al reves)", async () => {
    renderMany();
    await screen.findByRole("table", { name: "Resumen de las pruebas" });
    fireEvent.click(screen.getByRole("button", { name: /^Resultado/ }));
    expect(names()).toEqual(["[9] Cruce dorado", "[3] Range Filter Intraday", "[5] Range Filter Scalping", "[1] Mezcla 15m"]);
    expect(screen.getByRole("columnheader", { name: /Resultado/ })).toHaveAttribute("aria-sort", "descending");
    fireEvent.click(screen.getByRole("button", { name: /^Resultado/ }));
    expect(names()).toEqual(["[1] Mezcla 15m", "[5] Range Filter Scalping", "[3] Range Filter Intraday", "[9] Cruce dorado"]);
    // PF vacio (sin operaciones) siempre al final
    fireEvent.click(screen.getByRole("button", { name: /^PF/ }));
    expect(names().slice(-1)[0]).toBe("[3] Range Filter Intraday");
  });

  it("abre y cierra el detalle de cada prueba, y expande o colapsa todo", async () => {
    renderMany();
    await screen.findByRole("table", { name: "Resumen de las pruebas" });
    expect(screen.queryAllByText("Ver operaciones y eventos")).toHaveLength(0);
    await expand("[5] Range Filter Scalping");
    expect(screen.getAllByText("Ver operaciones y eventos")).toHaveLength(1);
    expect(screen.getByRole("button", { name: /\[5\] Range Filter Scalping/ })).toHaveAttribute("aria-expanded", "true");
    await expand("[5] Range Filter Scalping");
    expect(screen.queryAllByText("Ver operaciones y eventos")).toHaveLength(0);
    fireEvent.click(screen.getByText("Expandir todo"));
    expect(screen.getAllByText("Ver operaciones y eventos")).toHaveLength(4);
    fireEvent.click(screen.getByText("Colapsar todo"));
    expect(screen.queryAllByText("Ver operaciones y eventos")).toHaveLength(0);
  });

  it("recuerda filtros, orden y detalles abiertos en este navegador", async () => {
    renderMany();
    await screen.findByRole("table", { name: "Resumen de las pruebas" });
    fireEvent.change(screen.getByLabelText("Marco"), { target: { value: "1h" } });
    fireEvent.click(screen.getByRole("button", { name: /^Oper\./ }));
    await expand("[9] Cruce dorado");
    const saved = JSON.parse(localStorage.getItem("minerva.live.view") ?? "{}");
    expect(saved).toMatchObject({ timeframe: "1h", sort: "trades", desc: true, open: [9] });
  });

  it("arranca con la vista guardada y aguanta una guardada rota", async () => {
    localStorage.setItem("minerva.live.view", JSON.stringify({ status: "all", sort: "net", desc: true, open: [7] }));
    renderMany();
    await screen.findByRole("table", { name: "Resumen de las pruebas" });
    expect(names()[0]).toBe("[7] Vieja");
    expect(screen.getAllByText("Ver operaciones y eventos")).toHaveLength(1);
    localStorage.setItem("minerva.live.view", "{no es json");
    const { loadView, DEFAULT_VIEW } = await import("./live/view");
    expect(loadView()).toEqual(DEFAULT_VIEW);
  });

  it("deja el formulario cerrado si hay pruebas, lo abre con «+ Nueva prueba» y lo cierra al crear", async () => {
    const fetchMock = mockFetch({
      "/api/live/options": { status: 200, body: OPTIONS },
      "/api/live/tests": { status: 200, body: { ...TESTS, tests: MANY } },
      "POST /api/live/tests": { status: 200, body: { id: 11, name: "Otra" } },
    });
    vi.stubGlobal("fetch", fetchMock);
    render(withQueryClient(<LiveTab />));
    await screen.findByRole("table", { name: "Resumen de las pruebas" });
    expect(screen.queryByRole("form", { name: "Nueva prueba en vivo" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "+ Nueva prueba" }));
    fireEvent.change(await screen.findByLabelText("Nombre"), { target: { value: "Otra" } });
    fireEvent.click(screen.getByLabelText(/EMA 144/));
    fireEvent.click(screen.getByRole("button", { name: "Crear prueba" }));
    expect(await screen.findByText(/Prueba 11 creada/)).toBeInTheDocument();
    expect(screen.queryByRole("form", { name: "Nueva prueba en vivo" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "+ Nueva prueba" })).toHaveAttribute("aria-expanded", "false");
  });
});
