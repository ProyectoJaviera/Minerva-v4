import { Fragment, useState, type FormEvent, type ReactNode } from "react";
import {
  ApiError,
  type LiveOptions,
  type LiveSummary,
  type LiveTestRow,
  type LiveVerdict,
  type LiveOpenPosition,
  useCreateLiveTest,
  useLiveBotSwitch,
  useLiveOptions,
  useLivePositions,
  useLiveTest,
  useLiveTests,
  useStopLiveTest,
} from "./api";
import { Card } from "./components/Card";
import { ConfirmDialog } from "./components/ConfirmDialog";
import { IndicatorPicker } from "./components/IndicatorPicker";
import { StatTile } from "./components/StatTile";
import { Unavailable } from "./components/Unavailable";
import { loadView, saveView, upnlByTest, visibleTests, type LiveView, type SortKey, type StatusFilter } from "./live/view";

// Pestana «En vivo» (v3): pruebas de paper trading hacia adelante con el precio en tiempo real de
// Bitunix. El dueno elige monedas, marco, indicadores, salida y modo; el corredor (dentro de la app)
// las opera tick a tick. Solo cuentan las operaciones posteriores a crear la prueba.
//
// v4: el mismo componente muestra la pestana «Bots de senales» (`kind="bot"`): bots de una sola
// direccion con reglas de entrada y reglas de salida (la senal contraria de los indicadores de
// salida cierra la posicion), sin gemelo al azar. Cada pestana muestra solo lo suyo.

type Kind = "test" | "bot";

const KIND_TEXT: Record<Kind, { one: string; title: string; create: string; empty: string; none: string; viewKey: string }> = {
  test: {
    one: "prueba", title: "Pruebas en vivo", create: "Nueva prueba", empty: "Todavía no hay pruebas. Crea la primera con «+ Nueva prueba».",
    none: "Ninguna prueba cumple los filtros.", viewKey: "minerva.live.view",
  },
  bot: {
    one: "bot", title: "Bots de señales", create: "Nuevo bot", empty: "Todavía no hay bots. Crea el primero con «+ Nuevo bot».",
    none: "Ningún bot cumple los filtros.", viewKey: "minerva.bots.view",
  },
};

function kindOf(t: LiveTestRow): Kind {
  return t.config.kind === "bot" ? "bot" : "test";
}

const REASON_LABEL: Record<string, string> = { SIGNAL: "SEÑAL" };

const MODE_LABEL: Record<string, string> = {
  close: "Al cierre de la vela",
  intrabar: "Dentro de la vela (vela abierta)",
};

function fmtTime(ms: number | null): string {
  return ms === null ? "-" : `${new Date(ms).toISOString().slice(0, 16).replace("T", " ")} UTC`;
}

function fmtPf(pf: LiveSummary["pf"]): string {
  if (pf === null) return "-";
  if (pf === "inf") return "∞";
  return pf.toFixed(2);
}

function fmtUsdt(v: number): string {
  return `${v >= 0 ? "+" : ""}${v.toFixed(2)} USDT`;
}

function errorText(error: unknown): string {
  if (error instanceof ApiError && error.detail) return error.detail;
  return "No se pudo guardar (error del servidor).";
}

type BotState = "active" | "off" | "noresponse" | "unavailable";

function botState(enabled: boolean, alive: boolean, botOn: boolean): BotState {
  if (!enabled) return "unavailable";
  if (!alive) return "noresponse";
  return botOn ? "active" : "off";
}

const BOT_LABEL: Record<BotState, string> = {
  active: "Bot activo",
  off: "Bot apagado",
  noresponse: "Bot sin respuesta",
  unavailable: "Bot no disponible en esta instancia",
};

const BOT_STYLE: Record<BotState, string> = {
  active: "bg-green-100 text-green-800 dark:bg-green-900/40 dark:text-green-200",
  off: "bg-gray-200 text-gray-800 dark:bg-gray-700 dark:text-gray-100",
  noresponse: "bg-red-100 text-red-800 dark:bg-red-900/40 dark:text-red-200",
  unavailable: "bg-amber-100 text-amber-900 dark:bg-amber-900/40 dark:text-amber-200",
};

const BOT_DOT: Record<BotState, string> = {
  active: "bg-green-500",
  off: "bg-gray-400",
  noresponse: "bg-red-500",
  unavailable: "bg-amber-500",
};

function BotPanel({ enabled, alive, botOn, lastBeat, detail, onAskOff }: {
  enabled: boolean; alive: boolean; botOn: boolean; lastBeat: number | null; detail: string | null;
  onAskOff: () => void;
}) {
  const turn = useLiveBotSwitch();
  const state = botState(enabled, alive, botOn);
  return (
    <div className="space-y-2 rounded border border-gray-200 p-3 dark:border-gray-700">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span
          role="status"
          aria-label="Estado del bot"
          className={`inline-flex items-center gap-2 rounded-full px-3 py-1 text-sm font-medium ${BOT_STYLE[state]}`}
        >
          <span aria-hidden="true" className={`h-2.5 w-2.5 rounded-full ${BOT_DOT[state]}`} />
          {BOT_LABEL[state]}
        </span>
        {enabled && (
          botOn ? (
            <button
              onClick={onAskOff}
              className="rounded border border-red-300 px-3 py-1 text-sm font-medium text-red-700 hover:bg-red-50 dark:border-red-700 dark:text-red-300"
            >
              Apagar bot
            </button>
          ) : (
            <button
              onClick={() => turn.mutate({ on: true })}
              disabled={turn.isPending}
              className="rounded bg-green-600 px-3 py-1 text-sm font-medium text-white disabled:opacity-50"
            >
              Encender bot
            </button>
          )
        )}
      </div>
      <p className="text-xs text-gray-500 dark:text-gray-400">
        {state === "unavailable"
          ? "El corredor de pruebas en vivo está apagado en esta instancia (LIVE_TESTS_ENABLED=false). Puedes crear pruebas, pero solo operan donde el corredor esté encendido (el contenedor de la v3)."
          : state === "noresponse"
            ? `El bot no late hace más de un minuto${lastBeat !== null ? ` (último latido ${fmtTime(lastBeat)})` : ""}: el contenedor puede estar detenido.`
            : `${detail ?? ""}${lastBeat !== null ? ` · último latido ${fmtTime(lastBeat)}` : ""}`}
        {state === "noresponse" && !botOn ? " Además está apagado desde el dashboard." : ""}
      </p>
      {turn.isError && <p role="alert" className="text-sm text-red-700 dark:text-red-300">{errorText(turn.error)}</p>}
    </div>
  );
}

const DIRECTION_LABEL: Record<string, string> = {
  both: "Largos y cortos",
  long: "Solo largos",
  short: "Solo cortos",
};

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <fieldset className="space-y-2 rounded border border-gray-200 p-3 dark:border-gray-700">
      <legend className="px-1 text-xs font-semibold uppercase tracking-wide text-gray-500 dark:text-gray-400">{title}</legend>
      {children}
    </fieldset>
  );
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="flex flex-col gap-1 text-xs text-gray-600 dark:text-gray-300">
      {label}
      {children}
    </label>
  );
}

function CreateForm({ options, onCreated, kind = "test" }: { options: LiveOptions; onCreated?: (message: string) => void; kind?: Kind }) {
  const create = useCreateLiveTest();
  const isBot = kind === "bot";
  const [exitIndicators, setExitIndicators] = useState<string[]>([]);
  const [exitMinAgree, setExitMinAgree] = useState("");
  const [name, setName] = useState("");
  const [symbols, setSymbols] = useState(options.default_symbols.join(" "));
  const [timeframe, setTimeframe] = useState(options.timeframes[0] ?? "15m");
  const [mode, setMode] = useState("close");
  const [evalSeconds, setEvalSeconds] = useState(String(options.eval_seconds.default));
  const [indicators, setIndicators] = useState<string[]>([]);
  const [minAgree, setMinAgree] = useState("");
  const [windowBars, setWindowBars] = useState(String(options.default_window));
  const [direction, setDirection] = useState(kind === "bot" ? "long" : "both");
  const [margin, setMargin] = useState(String(options.margin.default));
  const [leverage, setLeverage] = useState(String(options.leverage.default));
  const [maxOpen, setMaxOpen] = useState("");
  const [exitKind, setExitKind] = useState<"pct" | "atr">("pct");
  const [atrExit, setAtrExit] = useState(options.exits.find((x) => x.id !== "pct")?.id ?? "s2_tp2r");
  const [slPct, setSlPct] = useState("1");
  const [tpMode, setTpMode] = useState<"single" | "partial" | "none">("single");
  const [tpPct, setTpPct] = useState("2");
  const [tps, setTps] = useState<{ dist: string; close: string }[]>([
    { dist: "1.5", close: "50" },
    { dist: "3", close: "50" },
  ]);
  const [breakeven, setBreakeven] = useState(false);
  const [trailPct, setTrailPct] = useState("");
  const [created, setCreated] = useState<string | null>(null);

  function num(text: string): number | null {
    const v = Number(text.replace(",", "."));
    return text.trim() === "" || !Number.isFinite(v) ? null : v;
  }

  const marginN = num(margin) ?? options.margin.default;
  const leverageN = num(leverage) ?? options.leverage.default;
  const slN = num(slPct);
  const riskPct = exitKind === "pct" && slN !== null ? slN * leverageN : null;
  const partialTotal = tps.reduce((acc, tp) => acc + (num(tp.close) ?? 0), 0);

  function setTp(i: number, key: "dist" | "close", value: string) {
    setTps((cur) => cur.map((tp, j) => (j === i ? { ...tp, [key]: value } : tp)));
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    setCreated(null);
    const partial = tpMode === "partial";
    create.mutate(
      {
        name, symbols: symbols.split(/[\s,]+/).filter(Boolean), timeframe, indicators, mode,
        exit_name: exitKind === "pct" ? "pct" : atrExit,
        sl_pct: exitKind === "pct" ? slN : null,
        tp_pct: exitKind === "pct" && tpMode === "single" ? num(tpPct) : null,
        trail_pct: exitKind === "pct" ? num(trailPct) : null,
        window: num(windowBars) ?? options.default_window,
        min_agree: num(minAgree),
        eval_seconds: num(evalSeconds) ?? options.eval_seconds.default,
        margin_usdt: marginN,
        leverage: leverageN,
        direction,
        take_profits: partial
          ? tps.map((tp) => [num(tp.dist) ?? 0, num(tp.close) ?? 0] as [number, number])
          : [],
        breakeven: partial && breakeven,
        max_open: num(maxOpen),
        ...(isBot ? { kind: "bot" as const, exit_indicators: exitIndicators, exit_min_agree: exitIndicators.length ? num(exitMinAgree) : null } : {}),
      },
      {
        onSuccess: (r) => {
          const msg = `${isBot ? "Bot" : "Prueba"} ${r.id} ${isBot ? "creado" : "creada"}: «${r.name}». Empieza a operar en unos segundos.`;
          if (onCreated) onCreated(msg);
          else setCreated(msg);
        },
      },
    );
  }

  const field = "rounded border border-gray-300 px-2 py-1 text-sm dark:border-gray-600 dark:bg-gray-900 dark:text-gray-100";
  return (
    <form onSubmit={submit} className="space-y-3" aria-label={isBot ? "Nuevo bot de señales" : "Nueva prueba en vivo"}>
      <Section title="1. Mercado">
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Nombre">
            <input className={field} value={name} onChange={(e) => setName(e.target.value)} maxLength={60} required />
          </Field>
          <Field label="Monedas (separadas por espacio)">
            <input className={field} value={symbols} onChange={(e) => setSymbols(e.target.value)} />
          </Field>
          <Field label="Marco">
            <select className={field} value={timeframe} onChange={(e) => setTimeframe(e.target.value)}>
              {options.timeframes.map((t) => <option key={t} value={t}>{t}</option>)}
            </select>
          </Field>
          <Field label="Modo">
            <select className={field} value={mode} onChange={(e) => setMode(e.target.value)}>
              {options.modes.map((m) => <option key={m} value={m}>{MODE_LABEL[m] ?? m}</option>)}
            </select>
          </Field>
          {mode === "intrabar" && (
            <Field label="Reevaluar cada (segundos)">
              <input className={field} value={evalSeconds} onChange={(e) => setEvalSeconds(e.target.value)} inputMode="numeric" />
            </Field>
          )}
        </div>
      </Section>

      <Section title="2. Entrada">
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Dirección">
            <select className={field} value={direction} onChange={(e) => setDirection(e.target.value)}>
              {options.directions.filter((d) => !isBot || d !== "both").map((d) => <option key={d} value={d}>{DIRECTION_LABEL[d] ?? d}</option>)}
            </select>
          </Field>
          <Field label={`Ventana de los eventos (velas, 1 a ${options.max_window})`}>
            <input className={field} value={windowBars} onChange={(e) => setWindowBars(e.target.value)} inputMode="numeric" />
          </Field>
          <Field label="Cuántos deben coincidir (vacío = todos)">
            <input className={field} value={minAgree} onChange={(e) => setMinAgree(e.target.value)} inputMode="numeric" />
          </Field>
        </div>
        <p className="text-xs text-gray-500 dark:text-gray-400">
          Los indicadores de estado votan en cada vela; los de evento (SFP, FVG, Camarilla…) se disparan en una vela y siguen
          votando durante la ventana. Ventana chica: los eventos tienen que coincidir casi en la misma vela.
        </p>
        <p className="text-xs text-gray-600 dark:text-gray-300">Indicadores y filtros (se entra cuando coinciden)</p>
        <IndicatorPicker options={options.indicators} selected={indicators} onChange={setIndicators} />
      </Section>

      {isBot && (
        <Section title="Reglas de salida (señal)">
          <p className="text-xs text-gray-500 dark:text-gray-400">
            Al cierre de cada vela, si estos indicadores dan la señal <strong>contraria</strong> a la posición abierta (corto para un
            largo, largo para un corto), la posición se cierra entera en el tick siguiente. Ejemplo: entrada «WaveTrend: cruce en
            zona extrema» en un bot de largos (X verde) y salida con el mismo indicador (la X roja cierra el largo). Si en la vela
            de la entrada la salida también da la señal contraria, la entrada se omite. Vacío = el bot sale solo por SL, TP o trailing.
          </p>
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="Cuántos deben coincidir para salir (vacío = todos)">
              <input className={field} value={exitMinAgree} onChange={(e) => setExitMinAgree(e.target.value)} inputMode="numeric" />
            </Field>
          </div>
          <IndicatorPicker options={options.indicators} selected={exitIndicators} onChange={setExitIndicators} />
          {exitIndicators.length > 0 && (
            <p className="text-xs text-gray-500 dark:text-gray-400">
              Con reglas de salida no se usa el TP de respaldo del motor: si no pones TP ni trailing, el bot espera la señal (el SL sigue activo).
            </p>
          )}
        </Section>
      )}

      <Section title="3. Posición">
        <div className="grid gap-3 sm:grid-cols-3">
          <Field label="Margen por operación (USDT)">
            <input className={field} value={margin} onChange={(e) => setMargin(e.target.value)} inputMode="decimal" />
          </Field>
          <Field label={`Apalancamiento (1 a ${options.leverage.max}x)`}>
            <input className={field} value={leverage} onChange={(e) => setLeverage(e.target.value)} inputMode="numeric" />
          </Field>
          <Field label="Máximo de posiciones abiertas (vacío = sin tope)">
            <input className={field} value={maxOpen} onChange={(e) => setMaxOpen(e.target.value)} inputMode="numeric" />
          </Field>
        </div>
        <p className="text-xs text-gray-500 dark:text-gray-400">
          Tamaño de cada operación: {marginN.toFixed(2)} USDT × {leverageN}x = {(marginN * leverageN).toFixed(2)} USDT. Margen aislado; comisión
          taker {(options.fees.taker_pct * 100).toFixed(2)} % por lado y deslizamiento de {options.fees.slippage_bps} pb.
        </p>
      </Section>

      <Section title="4. Salida">
        <div className="grid gap-3 sm:grid-cols-3">
          <Field label="Stop loss">
            <select className={field} value={exitKind} onChange={(e) => setExitKind(e.target.value as "pct" | "atr")}>
              <option value="pct">En % del precio de entrada</option>
              <option value="atr">Por volatilidad (ATR)</option>
            </select>
          </Field>
          {exitKind === "pct" ? (
            <Field label="SL %">
              <input className={field} value={slPct} onChange={(e) => setSlPct(e.target.value)} inputMode="decimal" />
            </Field>
          ) : (
            <Field label="Salida por ATR">
              <select className={field} value={atrExit} onChange={(e) => setAtrExit(e.target.value)}>
                {options.exits.filter((x) => x.id !== "pct").map((x) => <option key={x.id} value={x.id}>{x.label}</option>)}
              </select>
            </Field>
          )}
          {exitKind === "pct" && (
            <Field label="Trailing % (vacío = sin trailing)">
              <input className={field} value={trailPct} onChange={(e) => setTrailPct(e.target.value)} inputMode="decimal" />
            </Field>
          )}
        </div>
        {riskPct !== null && (
          <p className={`text-xs ${riskPct > options.max_sl_margin_loss_pct ? "text-red-700 dark:text-red-300" : "text-gray-500 dark:text-gray-400"}`}>
            Si salta el SL pierdes {riskPct.toFixed(1)} % del margen ({((marginN * riskPct) / 100).toFixed(2)} USDT, sin comisiones).
            {riskPct > options.max_sl_margin_loss_pct ? ` Supera el tope de ${options.max_sl_margin_loss_pct} %: las entradas se van a omitir.` : ""}
          </p>
        )}
        <Field label="Toma de ganancias">
          <select className={field} value={tpMode} onChange={(e) => setTpMode(e.target.value as "single" | "partial" | "none")}>
            {exitKind === "pct" && <option value="single">Un solo TP (cierra todo)</option>}
            {exitKind === "atr" && <option value="single">La del preset de ATR</option>}
            <option value="partial">TP parciales (salida escalonada)</option>
            {exitKind === "pct" && <option value="none">Sin TP (solo SL o trailing)</option>}
          </select>
        </Field>
        {tpMode === "single" && exitKind === "pct" && (
          <Field label="TP %">
            <input className={field} value={tpPct} onChange={(e) => setTpPct(e.target.value)} inputMode="decimal" />
          </Field>
        )}
        {tpMode === "partial" && (
          <div className="space-y-2">
            {tps.map((tp, i) => (
              <div key={i} className="grid grid-cols-[auto_1fr_1fr_auto] items-end gap-2">
                <span className="pb-1 text-xs font-medium text-gray-600 dark:text-gray-300">TP{i + 1}</span>
                <Field label={`TP${i + 1}: distancia %`}>
                  <input className={field} value={tp.dist} onChange={(e) => setTp(i, "dist", e.target.value)} inputMode="decimal" />
                </Field>
                <Field label={`TP${i + 1}: % que cierra`}>
                  <input className={field} value={tp.close} onChange={(e) => setTp(i, "close", e.target.value)} inputMode="decimal" />
                </Field>
                <button
                  type="button"
                  onClick={() => setTps((cur) => cur.filter((_, j) => j !== i))}
                  disabled={tps.length === 1}
                  className="pb-1 text-xs text-red-700 underline disabled:opacity-40 dark:text-red-300"
                >
                  Quitar
                </button>
              </div>
            ))}
            <div className="flex flex-wrap items-center gap-3">
              {tps.length < options.max_tp_levels && (
                <button
                  type="button"
                  onClick={() => setTps((cur) => [...cur, { dist: "", close: "" }])}
                  className="text-xs text-blue-700 underline dark:text-blue-300"
                >
                  Agregar nivel
                </button>
              )}
              <span className={`text-xs ${partialTotal > 100 ? "text-red-700 dark:text-red-300" : "text-gray-500 dark:text-gray-400"}`}>
                Cierran en total {partialTotal.toFixed(0)} %
                {partialTotal < 100 ? `; el ${(100 - partialTotal).toFixed(0)} % restante sigue con el SL${exitKind === "pct" && num(trailPct) ? " y el trailing" : ""}` : ""}.
              </span>
            </div>
            <label className="flex items-center gap-2 text-sm text-gray-800 dark:text-gray-200">
              <input type="checkbox" checked={breakeven} onChange={(e) => setBreakeven(e.target.checked)} />
              Mover el SL al precio de entrada después del TP1
            </label>
          </div>
        )}
      </Section>

      <div className="flex items-center gap-3">
        <button
          type="submit"
          disabled={create.isPending || indicators.length === 0 || name.trim() === ""}
          className="rounded bg-blue-600 px-3 py-1 text-sm font-medium text-white disabled:opacity-50"
        >
          {isBot ? "Crear bot" : "Crear prueba"}
        </button>
        {create.isError && <span role="alert" className="text-sm text-red-700 dark:text-red-300">{errorText(create.error)}</span>}
        {created && <span role="status" className="text-sm text-green-700 dark:text-green-300">{created}</span>}
      </div>
    </form>
  );
}

function TestDetail({ id }: { id: number }) {
  const q = useLiveTest(id);
  if (q.isLoading) return <p className="text-sm text-gray-500">Cargando…</p>;
  if (q.isError || !q.data) return <Unavailable error={q.error} what="Detalle de la prueba" />;
  const d = q.data;
  return (
    <div className="space-y-3 text-sm">
      {d.positions.length > 0 && (
        <div>
          <h4 className="text-xs font-semibold text-gray-500">Posiciones abiertas</h4>
          <ul className="space-y-1">
            {d.positions.map((p) => (
              <li key={p.symbol} className="tabular-nums text-gray-800 dark:text-gray-200">
                {p.symbol} {p.side} a {p.entry_price} desde {fmtTime(p.entry_time_ms)} · {p.margin_usdt} USDT x{p.leverage} ·
                SL {p.stop} · TP{" "}
                {p.tp_levels && p.tp_levels.length > 0
                  ? p.tp_levels.map((lv, i) => `${lv[2] ? "✓ " : ""}TP${i + 1} ${lv[0].toPrecision(6)} (${Math.round(lv[1] * 100)} %)`).join(" · ")
                  : (p.tp ?? "-")}
                {p.qty_initial && p.qty !== undefined && p.qty < p.qty_initial
                  ? ` · abierta ${Math.round((100 * p.qty) / p.qty_initial)} %`
                  : ""}
              </li>
            ))}
          </ul>
        </div>
      )}
      <div>
        <h4 className="text-xs font-semibold text-gray-500">Últimas operaciones</h4>
        {d.trades.length === 0 ? (
          <p className="text-gray-500">Todavía sin operaciones cerradas.</p>
        ) : (
          <table className="w-full text-left text-xs tabular-nums">
            <thead>
              <tr className="text-gray-500"><th>Salida</th><th>Moneda</th><th>Lado</th><th>Entrada</th><th>Salida</th><th>Motivo</th><th>Neto</th></tr>
            </thead>
            <tbody>
              {d.trades.slice(0, 30).map((t) => (
                <tr key={`${t.symbol}-${t.exit_time_ms}`} className="text-gray-800 dark:text-gray-200">
                  <td>{fmtTime(t.exit_time_ms)}</td><td>{t.symbol}</td><td>{t.side}</td><td>{t.entry_price}</td>
                  <td>{t.exit_price.toPrecision(6)}</td>
                  <td>{t.partials && t.partials.length > 1 ? t.partials.map((pt) => REASON_LABEL[pt[3]] ?? pt[3]).join(" + ") : (REASON_LABEL[t.close_reason] ?? t.close_reason)}</td>
                  <td>{fmtUsdt(t.pnl_net_usdt)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
      <div>
        <h4 className="text-xs font-semibold text-gray-500">Eventos recientes</h4>
        <ul className="space-y-0.5 text-xs text-gray-700 dark:text-gray-300">
          {d.events.slice(0, 15).map((e, i) => (
            <li key={i}>{fmtTime(e.ts_ms)} · {e.kind} {e.symbol ?? ""} {e.detail}</li>
          ))}
        </ul>
      </div>
    </div>
  );
}

function fmtPrice(v: number | null): string {
  return v === null ? "-" : String(Number(v.toPrecision(6)));
}

function fmtSigned(v: number | null, digits = 2, unit = ""): string {
  if (v === null) return "-";
  return `${v >= 0 ? "+" : ""}${v.toFixed(digits)}${unit}`;
}

function pnlClass(v: number | null): string {
  if (v === null || v === 0) return "text-gray-800 dark:text-gray-200";
  return v > 0 ? "text-green-700 dark:text-green-400" : "text-red-700 dark:text-red-400";
}

function fmtAge(fromMs: number, nowMs: number): string {
  const s = Math.max(0, Math.round((nowMs - fromMs) / 1000));
  if (s < 60) return `${s} s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m} min`;
  const h = Math.floor(m / 60);
  return `${h} h ${m % 60} min`;
}

// v4: las posiciones abiertas como en un exchange, con el precio actual y el PnL no realizado. Se
// refresca cada 2 segundos con el ultimo precio que guarda el corredor.
function PositionsTable({ rows, nowMs, label }: { rows: LiveOpenPosition[]; nowMs: number; label: string }) {
  if (rows.length === 0) return null;
  return (
    <div className="overflow-x-auto">
      <table aria-label={label} className="w-full min-w-[720px] text-left text-xs tabular-nums">
        <thead>
          <tr className="text-gray-500 dark:text-gray-400">
            <th className="py-1 pr-2 font-medium">Moneda</th>
            <th className="pr-2 font-medium">Tamaño</th>
            <th className="pr-2 font-medium">Entrada</th>
            <th className="pr-2 font-medium">Precio actual</th>
            <th className="pr-2 font-medium">Liquidación</th>
            <th className="pr-2 font-medium">PnL no realizado (ROE)</th>
            <th className="pr-2 font-medium">Neto si cierra</th>
            <th className="pr-2 font-medium">SL</th>
            <th className="pr-2 font-medium">Próximo TP</th>
            <th className="font-medium">Abierta</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((p) => (
            <tr key={`${p.test_id}-${p.symbol}`} className="border-t border-gray-100 align-top text-gray-800 dark:border-gray-700 dark:text-gray-200">
              <td className="py-1.5 pr-2">
                <div className="font-semibold">{p.symbol}</div>
                <span className={`rounded px-1 text-[11px] font-medium ${p.side === "LONG" ? "bg-green-100 text-green-800 dark:bg-green-900/40 dark:text-green-200" : "bg-red-100 text-red-800 dark:bg-red-900/40 dark:text-red-200"}`}>
                  {p.side === "LONG" ? "Largo" : "Corto"} {p.leverage}x
                </span>
              </td>
              <td className="pr-2">
                <div>{p.notional === null ? "-" : `${p.notional.toFixed(2)} USDT`}</div>
                <div className="text-gray-500">margen {p.margin_usdt} · {Math.round(p.size_pct)} % abierto</div>
              </td>
              <td className="pr-2">{fmtPrice(p.entry_price)}</td>
              <td className="pr-2">
                <div className="font-semibold">{fmtPrice(p.price)}</div>
                <div className={p.price_stale ? "text-amber-700 dark:text-amber-400" : "text-gray-500"}>
                  {p.price_ts_ms === null ? "sin precio" : p.price_stale ? `viejo (${fmtAge(p.price_ts_ms, nowMs)})` : `hace ${fmtAge(p.price_ts_ms, nowMs)}`}
                </div>
              </td>
              <td className="pr-2">
                <div>{fmtPrice(p.liq)}</div>
                <div className="text-gray-500">{p.to_liq_pct === null ? "" : `a ${p.to_liq_pct.toFixed(2)} %`}</div>
              </td>
              <td className={`pr-2 font-semibold ${pnlClass(p.upnl)}`}>
                <div>{fmtSigned(p.upnl, 3, " USDT")}</div>
                <div>{fmtSigned(p.roe_pct, 2, " %")}</div>
              </td>
              <td className={`pr-2 ${pnlClass(p.net_if_closed)}`}>
                <div>{fmtSigned(p.net_if_closed, 3, " USDT")}</div>
                {p.realized !== 0 && <div className="text-gray-500">TP hechos {fmtSigned(p.realized, 3)}</div>}
              </td>
              <td className="pr-2">
                <div>{fmtPrice(p.stop)}</div>
                <div className="text-gray-500">{p.to_sl_pct === null ? "" : `a ${p.to_sl_pct.toFixed(2)} %`}</div>
              </td>
              <td className="pr-2">
                <div>{fmtPrice(p.next_tp)}</div>
                <div className="text-gray-500">{p.to_tp_pct === null ? (p.trailing !== null ? "trailing" : "") : `a ${p.to_tp_pct.toFixed(2)} %`}</div>
              </td>
              <td>{fmtAge(p.entry_time_ms, nowMs)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

const VERDICT_TEXT: Record<LiveVerdict, string> = {
  pocas_operaciones: "Faltan operaciones para decir algo",
  le_gana: "Le gana al azar",
  pierde: "Pierde contra el azar",
  no_se_distingue: "No se distingue del azar",
};

const VERDICT_CLASS: Record<LiveVerdict, string> = {
  pocas_operaciones: "bg-gray-100 text-gray-700 dark:bg-gray-700 dark:text-gray-200",
  le_gana: "bg-green-100 text-green-800 dark:bg-green-900/40 dark:text-green-200",
  pierde: "bg-red-100 text-red-800 dark:bg-red-900/40 dark:text-red-200",
  no_se_distingue: "bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-200",
};

function fmtPerTrade(v: number | null): string {
  return v === null ? "-" : `${v >= 0 ? "+" : ""}${v.toFixed(3)} USDT`;
}

// v4: la prueba contra su gemelo de entradas al azar (misma configuracion, entradas sorteadas a la
// misma frecuencia). Si no le gana al gemelo, su resultado no se distingue de la suerte.
function ControlPanel({ t, twinRows, nowMs }: { t: LiveTestRow; twinRows: LiveOpenPosition[]; nowMs: number }) {
  const [showTwin, setShowTwin] = useState(false);
  const ctl = t.control;
  const vs = t.vs_control;
  if (!ctl || !vs) return null;
  const rate = t.rate;
  return (
    <section aria-label="Contra el azar" className="space-y-2 rounded bg-gray-50 p-2 dark:bg-gray-800/60">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h4 className="text-xs font-semibold text-gray-700 dark:text-gray-200">Contra el azar</h4>
        <span className={`rounded px-2 py-0.5 text-xs font-medium ${VERDICT_CLASS[vs.verdict]}`}>
          {VERDICT_TEXT[vs.verdict]}
        </span>
      </div>
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <StatTile label="Operaciones del gemelo" value={String(ctl.summary.trades)} />
        <StatTile label="PF del gemelo" value={fmtPf(ctl.summary.pf)} />
        <StatTile label="Resultado del gemelo" value={fmtUsdt(ctl.summary.net)} />
        <StatTile label="Diferencia por operación" value={fmtPerTrade(vs.diff)} />
      </div>
      <p className="text-xs text-gray-500 dark:text-gray-400">
        El gemelo [{ctl.id}] opera las mismas monedas, marco, margen y salida, pero entra al azar
        {rate ? ` con la frecuencia de la prueba (${rate.signals} señales en ${rate.eligible_bars} velas)` : ""}.
        {vs.verdict === "pocas_operaciones"
          ? ` El veredicto necesita al menos ${vs.min_trades} operaciones cerradas en cada lado.`
          : ` Prueba de Welch de una cola al 5 % sobre el resultado neto por operación (t = ${vs.t === null ? "-" : vs.t.toFixed(2)}).`}
      </p>
      {twinRows.length > 0 && (
        <ToggleButton open={showTwin} onClick={() => setShowTwin(!showTwin)}>
          Posiciones del gemelo al azar ({twinRows.length})
        </ToggleButton>
      )}
      {showTwin && <PositionsTable rows={twinRows} nowMs={nowMs} label="Posiciones abiertas del gemelo" />}
    </section>
  );
}

function ToggleButton({ open, onClick, children }: { open: boolean; onClick: () => void; children: ReactNode }) {
  return (
    <button
      type="button"
      aria-expanded={open}
      onClick={onClick}
      className="inline-flex items-center gap-1 rounded border border-gray-300 px-2 py-0.5 text-xs text-gray-700 hover:bg-gray-100 dark:border-gray-600 dark:text-gray-200 dark:hover:bg-gray-700"
    >
      <span aria-hidden="true">{open ? "▾" : "▸"}</span>
      {children}
    </button>
  );
}

function configLine(t: LiveTestRow): string {
  const c = t.config;
  const sl = c.exit_name === "pct" && c.pct_exit ? `${c.pct_exit[0]} %` : c.exit_name;
  const tps = c.take_profits && c.take_profits.length > 0
    ? ` · TP ${c.take_profits.map(([d, cl]) => `${d} %→${cl} %`).join(", ")}${c.breakeven ? " · SL a entrada tras TP1" : ""}`
    : "";
  const exits = c.kind === "bot"
    ? ` · sale con ${c.exit_indicators && c.exit_indicators.length ? c.exit_indicators.join(" + ") : "solo SL/TP"}`
    : "";
  return `${c.timeframe} · ${MODE_LABEL[c.mode] ?? c.mode} · entra con ${c.indicators.join(" + ")}${exits} · ${DIRECTION_LABEL[c.direction ?? "both"]} · `
    + `${c.margin_usdt ?? "-"} USDT x${c.leverage ?? "-"} · SL ${sl}${tps} · ${c.symbols.length} monedas · desde ${fmtTime(t.created_at_ms)}`;
}

function StatusBadge({ status }: { status: LiveTestRow["status"] }) {
  return (
    <span className={`rounded px-2 py-0.5 text-xs font-medium ${status === "active" ? "bg-green-100 text-green-800 dark:bg-green-900/40 dark:text-green-200" : "bg-gray-100 text-gray-700 dark:bg-gray-700 dark:text-gray-200"}`}>
      {status === "active" ? "activa" : "detenida"}
    </span>
  );
}

// El detalle de una prueba (se abre desde la tabla resumen): configuracion, numeros, gemelo y, detras
// de botones, las posiciones abiertas y las operaciones.
function TestCard({ t, positions, nowMs, onStop }: {
  t: LiveTestRow; positions: LiveOpenPosition[]; nowMs: number; onStop: (t: LiveTestRow) => void;
}) {
  const [showDetail, setShowDetail] = useState(false);
  const [showOwn, setShowOwn] = useState(false);
  const own = positions.filter((p) => p.test_id === t.id);
  const twin = t.control ? positions.filter((p) => p.test_id === t.control?.id) : [];
  const s = t.summary;
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <p className="min-w-0 flex-1 text-xs text-gray-500 dark:text-gray-400">{configLine(t)}</p>
        <div className="flex items-center gap-2">
          <StatusBadge status={t.status} />
          {t.status === "active" && (
            <button onClick={() => onStop(t)} className="rounded border border-red-300 px-2 py-0.5 text-xs text-red-700 hover:bg-red-50 dark:border-red-700 dark:text-red-300">
              Detener
            </button>
          )}
        </div>
      </div>
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-5">
        <StatTile label="Operaciones" value={String(s.trades)} />
        <StatTile label="PF" value={fmtPf(s.pf)} />
        <StatTile label="Resultado" value={fmtUsdt(s.net)} />
        <StatTile label="Ganadoras" value={s.winrate === null ? "-" : `${Math.round(100 * s.winrate)}%`} />
        <StatTile label="Abiertas" value={String(own.length || t.open_positions)} />
      </div>
      {own.length > 0 && (
        <div className="space-y-1">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <ToggleButton open={showOwn} onClick={() => setShowOwn(!showOwn)}>
              Posiciones abiertas ({own.length})
            </ToggleButton>
            <span className="text-xs text-gray-500 dark:text-gray-400">
              PnL no realizado {fmtSigned(own.reduce((a, p) => a + (p.upnl ?? 0), 0), 3, " USDT")} · se actualiza cada 2 s
            </span>
          </div>
          {showOwn && <PositionsTable rows={own} nowMs={nowMs} label="Posiciones abiertas" />}
        </div>
      )}
      <ControlPanel t={t} twinRows={twin} nowMs={nowMs} />
      <ToggleButton open={showDetail} onClick={() => setShowDetail(!showDetail)}>
        {showDetail ? "Ocultar operaciones y eventos" : "Ver operaciones y eventos"}
      </ToggleButton>
      {showDetail && <TestDetail id={t.id} />}
    </div>
  );
}

const COLUMNS: { key: SortKey; label: string; right?: boolean }[] = [
  { key: "id", label: "Prueba" },
  { key: "timeframe", label: "Marco" },
  { key: "status", label: "Estado" },
  { key: "trades", label: "Oper.", right: true },
  { key: "pf", label: "PF", right: true },
  { key: "net", label: "Resultado", right: true },
  { key: "winrate", label: "Ganadoras", right: true },
  { key: "open", label: "Abiertas", right: true },
  { key: "upnl", label: "PnL no realizado", right: true },
  { key: "diff", label: "vs azar (por oper.)", right: true },
];

function SummaryTable({ rows, view, setView, upnl, positions, nowMs, onStop }: {
  rows: LiveTestRow[]; view: LiveView; setView: (v: LiveView) => void; upnl: Map<number, number>;
  positions: LiveOpenPosition[]; nowMs: number; onStop: (t: LiveTestRow) => void;
}) {
  function sortBy(key: SortKey) {
    // primer clic: los numeros de mayor a menor; el id y los textos, de menor a mayor
    const textual = key === "id" || key === "name" || key === "timeframe" || key === "status";
    setView({ ...view, sort: key, desc: view.sort === key ? !view.desc : !textual });
  }
  function toggle(id: number) {
    setView({ ...view, open: view.open.includes(id) ? view.open.filter((x) => x !== id) : [...view.open, id] });
  }
  return (
    <div className="overflow-x-auto rounded border border-gray-200 dark:border-gray-700">
      <table aria-label="Resumen de las pruebas" className="w-full min-w-[860px] text-left text-xs tabular-nums">
        <thead className="bg-gray-50 dark:bg-gray-800/60">
          <tr className="text-gray-600 dark:text-gray-300">
            {COLUMNS.map((col) => (
              <th
                key={col.key}
                scope="col"
                aria-sort={view.sort === col.key ? (view.desc ? "descending" : "ascending") : "none"}
                className={`whitespace-nowrap px-2 py-1.5 font-medium ${col.right ? "text-right" : ""}`}
              >
                <button type="button" onClick={() => sortBy(col.key)} className="inline-flex items-center gap-1 hover:text-gray-900 dark:hover:text-white">
                  {col.label}
                  <span aria-hidden="true" className="text-[10px]">
                    {view.sort === col.key ? (view.desc ? "▼" : "▲") : ""}
                  </span>
                </button>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((t) => {
            const s = t.summary;
            const open = view.open.includes(t.id);
            const u = upnl.get(t.id) ?? null;
            const vs = t.vs_control;
            return (
              <Fragment key={t.id}>
                <tr
                  onClick={() => toggle(t.id)}
                  className={`cursor-pointer border-t border-gray-100 align-top text-gray-800 hover:bg-gray-50 dark:border-gray-700 dark:text-gray-200 dark:hover:bg-gray-800/60 ${open ? "bg-gray-50 dark:bg-gray-800/60" : ""}`}
                >
                  <td className="max-w-[18rem] px-2 py-1.5">
                    <button
                      type="button"
                      aria-expanded={open}
                      onClick={(e) => { e.stopPropagation(); toggle(t.id); }}
                      className="text-left font-semibold text-gray-900 hover:underline dark:text-gray-100"
                    >
                      <span aria-hidden="true" className="mr-1 text-gray-400">{open ? "▾" : "▸"}</span>
                      [{t.id}] {t.config.name}
                    </button>
                    <div className="truncate text-gray-500 dark:text-gray-400" title={t.config.indicators.join(" + ")}>
                      {t.config.indicators.join(" + ")}
                    </div>
                  </td>
                  <td className="px-2 py-1.5">{t.config.timeframe}</td>
                  <td className="px-2 py-1.5"><StatusBadge status={t.status} /></td>
                  <td className="whitespace-nowrap px-2 py-1.5 text-right">{s.trades}</td>
                  <td className="whitespace-nowrap px-2 py-1.5 text-right">{fmtPf(s.pf)}</td>
                  <td className={`whitespace-nowrap px-2 py-1.5 text-right font-semibold ${pnlClass(s.trades ? s.net : null)}`}>{fmtUsdt(s.net)}</td>
                  <td className="whitespace-nowrap px-2 py-1.5 text-right">{s.winrate === null ? "-" : `${Math.round(100 * s.winrate)}%`}</td>
                  <td className="whitespace-nowrap px-2 py-1.5 text-right">{t.open_positions}</td>
                  <td className={`whitespace-nowrap px-2 py-1.5 text-right ${pnlClass(u)}`}>{fmtSigned(u, 3, " USDT")}</td>
                  <td className="whitespace-nowrap px-2 py-1.5 text-right">
                    {vs ? (
                      <>
                        <div className={pnlClass(vs.diff)}>{fmtPerTrade(vs.diff)}</div>
                        <div className="text-gray-500 dark:text-gray-400">
                          gemelo {t.control ? fmtUsdt(t.control.summary.net) : "-"}
                          {vs.verdict !== "pocas_operaciones" ? ` · ${VERDICT_TEXT[vs.verdict].toLowerCase()}` : ""}
                        </div>
                      </>
                    ) : "-"}
                  </td>
                </tr>
                {open && (
                  <tr className="border-t border-gray-100 dark:border-gray-700">
                    <td colSpan={COLUMNS.length} className="bg-white px-3 py-3 dark:bg-gray-900/40">
                      <TestCard t={t} positions={positions} nowMs={nowMs} onStop={onStop} />
                    </td>
                  </tr>
                )}
              </Fragment>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function Toolbar({ view, setView, shown, total, timeframes, onExpandAll, onCollapseAll }: {
  view: LiveView; setView: (v: LiveView) => void; shown: number; total: number; timeframes: string[];
  onExpandAll: () => void; onCollapseAll: () => void;
}) {
  const field = "rounded border border-gray-300 px-2 py-1 text-xs dark:border-gray-600 dark:bg-gray-900 dark:text-gray-100";
  return (
    <div className="flex flex-wrap items-end gap-2">
      <label className="flex flex-col gap-0.5 text-xs text-gray-600 dark:text-gray-300">
        Estado
        <select className={field} value={view.status} onChange={(e) => setView({ ...view, status: e.target.value as StatusFilter })}>
          <option value="active">Activas</option>
          <option value="stopped">Detenidas</option>
          <option value="all">Todas</option>
        </select>
      </label>
      <label className="flex flex-col gap-0.5 text-xs text-gray-600 dark:text-gray-300">
        Marco
        <select className={field} value={view.timeframe} onChange={(e) => setView({ ...view, timeframe: e.target.value })}>
          <option value="">Todos</option>
          {timeframes.map((tf) => <option key={tf} value={tf}>{tf}</option>)}
        </select>
      </label>
      <label className="flex min-w-[10rem] flex-1 flex-col gap-0.5 text-xs text-gray-600 dark:text-gray-300">
        Buscar
        <input
          type="search"
          className={field}
          value={view.query}
          placeholder="nombre o indicador"
          onChange={(e) => setView({ ...view, query: e.target.value })}
        />
      </label>
      <span className="pb-1 text-xs text-gray-500 dark:text-gray-400" aria-live="polite">{shown} de {total} pruebas</span>
      <div className="flex gap-2 pb-0.5">
        <button type="button" onClick={onExpandAll} className="text-xs text-blue-700 underline dark:text-blue-300">Expandir todo</button>
        <button type="button" onClick={onCollapseAll} className="text-xs text-blue-700 underline dark:text-blue-300">Colapsar todo</button>
      </div>
    </div>
  );
}

export function LiveTab({ kind = "test" }: { kind?: Kind }) {
  const text = KIND_TEXT[kind];
  const options = useLiveOptions();
  const tests = useLiveTests();
  const stop = useStopLiveTest();
  const turn = useLiveBotSwitch();
  const [stopping, setStopping] = useState<LiveTestRow | null>(null);
  const [askOff, setAskOff] = useState(false);
  const [view, setViewState] = useState<LiveView>(() => loadView(text.viewKey));
  const [formOpen, setFormOpen] = useState<boolean | null>(null); // null = abierto solo si no hay pruebas
  const [created, setCreated] = useState<string | null>(null);
  const all = (tests.data?.tests ?? []).filter((t) => kindOf(t) === kind);
  const anyRunning = (tests.data?.tests ?? []).some((t) => t.status === "active" || t.open_positions > 0);
  const live = useLivePositions(anyRunning);
  const positions = live.data?.positions ?? [];
  const nowMs = Date.now();
  const upnl = upnlByTest(positions);
  const rows = visibleTests(all, view, upnl);
  const showForm = formOpen ?? (tests.data !== undefined && all.length === 0);
  const timeframes = options.data?.timeframes ?? [...new Set(all.map((t) => t.config.timeframe))];

  function setView(next: LiveView) {
    setViewState(next);
    saveView(next, text.viewKey);
  }

  return (
    <div className="space-y-4">
      <Card title={text.title}>
        {tests.isError ? (
          <Unavailable error={tests.error} what={text.title} />
        ) : tests.data ? (
          <div className="space-y-3">
            <BotPanel
              enabled={tests.data.enabled}
              alive={tests.data.runner.alive}
              botOn={tests.data.runner.bot_on}
              lastBeat={tests.data.runner.last_beat_ms}
              detail={tests.data.runner.detail}
              onAskOff={() => setAskOff(true)}
            />
            <div className="flex flex-wrap items-center justify-between gap-2">
              <p className="min-w-0 flex-1 text-xs text-gray-500 dark:text-gray-400">
                {kind === "bot"
                  ? "Bots que abren con unos indicadores y cierran con la señal contraria de otros (como Cornix o WunderTrading), en paper con el precio real de Bitunix; nunca órdenes reales. Comparten el bot y el interruptor con «En vivo». Sin gemelo al azar: no dicen si le ganan a la suerte."
                  : "Paper trading hacia adelante con el precio real de Bitunix; nunca órdenes reales. Si creas muchas pruebas y miras solo la que ganó, es sobreajuste hecho hacia adelante."}
              </p>
              <button
                type="button"
                aria-expanded={showForm}
                onClick={() => { setFormOpen(!showForm); setCreated(null); }}
                className="rounded bg-blue-600 px-3 py-1 text-sm font-medium text-white hover:bg-blue-700"
              >
                {showForm ? "Cerrar formulario" : `+ ${text.create}`}
              </button>
            </div>
            {created && <p role="status" className="text-sm text-green-700 dark:text-green-300">{created}</p>}
            {showForm && (
              <section aria-label={text.create} className="rounded border border-blue-200 p-3 dark:border-blue-900">
                <h3 className="mb-2 text-sm font-semibold text-gray-900 dark:text-gray-100">{text.create}</h3>
                {options.isError ? (
                  <Unavailable error={options.error} what="Opciones" />
                ) : options.data ? (
                  <CreateForm
                    kind={kind}
                    options={options.data}
                    onCreated={(msg) => { setCreated(msg); setFormOpen(false); }}
                  />
                ) : (
                  <p className="text-sm text-gray-500">Cargando…</p>
                )}
              </section>
            )}
            {all.length === 0 ? (
              <p className="text-sm text-gray-500">{text.empty}</p>
            ) : (
              <>
                <Toolbar
                  view={view}
                  setView={setView}
                  shown={rows.length}
                  total={all.length}
                  timeframes={timeframes}
                  onExpandAll={() => setView({ ...view, open: [...new Set([...view.open, ...rows.map((t) => t.id)])] })}
                  onCollapseAll={() => setView({ ...view, open: [] })}
                />
                {rows.length === 0 ? (
                  <p className="text-sm text-gray-500">{text.none}</p>
                ) : (
                  <SummaryTable
                    rows={rows}
                    view={view}
                    setView={setView}
                    upnl={upnl}
                    positions={positions}
                    nowMs={nowMs}
                    onStop={setStopping}
                  />
                )}
              </>
            )}
          </div>
        ) : (
          <p className="text-sm text-gray-500">Cargando…</p>
        )}
      </Card>
      {askOff && (
        <ConfirmDialog
          title="Apagar el bot de pruebas en vivo"
          warning="El bot deja de escuchar el mercado y cierra todas las posiciones abiertas de todas las pruebas con el último precio. Las pruebas siguen guardadas y vuelven a operar al encenderlo; las horas apagado no cuentan."
          phrase="APAGAR"
          confirmLabel="Apagar"
          busy={turn.isPending}
          error={turn.isError ? errorText(turn.error) : null}
          onConfirm={() => turn.mutate({ on: false, confirm: "APAGAR" }, { onSuccess: () => setAskOff(false) })}
          onCancel={() => setAskOff(false)}
        />
      )}
      {stopping && (
        <ConfirmDialog
          title={`Detener ${kind === "bot" ? "el bot" : "la prueba"} ${stopping.id}`}
          warning={`${kind === "bot" ? "El bot deja" : "La prueba deja"} de operar y sus posiciones abiertas se cierran con el último precio. No se puede reanudar.`}
          phrase="DETENER"
          confirmLabel="Detener"
          busy={stop.isPending}
          error={stop.isError ? errorText(stop.error) : null}
          onConfirm={() => stop.mutate(stopping.id, { onSuccess: () => setStopping(null) })}
          onCancel={() => setStopping(null)}
        />
      )}
    </div>
  );
}
