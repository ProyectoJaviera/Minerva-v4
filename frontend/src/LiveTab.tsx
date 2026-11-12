import { useState, type FormEvent, type ReactNode } from "react";
import {
  ApiError,
  type LiveOptions,
  type LiveSummary,
  type LiveTestRow,
  type LiveVerdict,
  useCreateLiveTest,
  useLiveBotSwitch,
  useLiveOptions,
  useLiveTest,
  useLiveTests,
  useStopLiveTest,
} from "./api";
import { Card } from "./components/Card";
import { ConfirmDialog } from "./components/ConfirmDialog";
import { StatTile } from "./components/StatTile";
import { Unavailable } from "./components/Unavailable";

// Pestana «En vivo» (v3): pruebas de paper trading hacia adelante con el precio en tiempo real de
// Bitunix. El dueno elige monedas, marco, indicadores, salida y modo; el corredor (dentro de la app)
// las opera tick a tick. Solo cuentan las operaciones posteriores a crear la prueba.

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

function CreateForm({ options }: { options: LiveOptions }) {
  const create = useCreateLiveTest();
  const [name, setName] = useState("");
  const [symbols, setSymbols] = useState(options.default_symbols.join(" "));
  const [timeframe, setTimeframe] = useState(options.timeframes[0] ?? "15m");
  const [mode, setMode] = useState("close");
  const [evalSeconds, setEvalSeconds] = useState(String(options.eval_seconds.default));
  const [indicators, setIndicators] = useState<string[]>([]);
  const [minAgree, setMinAgree] = useState("");
  const [windowBars, setWindowBars] = useState(String(options.default_window));
  const [direction, setDirection] = useState("both");
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

  function toggle(id: string) {
    setIndicators((cur) => (cur.includes(id) ? cur.filter((x) => x !== id) : [...cur, id]));
  }

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
      },
      { onSuccess: (r) => setCreated(`Prueba ${r.id} creada: «${r.name}». Empieza a operar en unos segundos.`) },
    );
  }

  const field = "rounded border border-gray-300 px-2 py-1 text-sm dark:border-gray-600 dark:bg-gray-900 dark:text-gray-100";
  return (
    <form onSubmit={submit} className="space-y-3" aria-label="Nueva prueba en vivo">
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
              {options.directions.map((d) => <option key={d} value={d}>{DIRECTION_LABEL[d] ?? d}</option>)}
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
        <div className="grid gap-1 sm:grid-cols-2">
          {options.indicators.map((i) => (
            <label key={i.id} className="flex items-start gap-2 text-sm text-gray-800 dark:text-gray-200">
              <input type="checkbox" checked={indicators.includes(i.id)} onChange={() => toggle(i.id)} className="mt-1" />
              <span>{i.label} <span className="text-xs text-gray-500">({i.kind})</span></span>
            </label>
          ))}
        </div>
      </Section>

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
          Crear prueba
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
                  <td>{t.partials && t.partials.length > 1 ? t.partials.map((pt) => pt[3]).join(" + ") : t.close_reason}</td>
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
function ControlPanel({ t }: { t: LiveTestRow }) {
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
    </section>
  );
}

function TestCard({ t, onStop }: { t: LiveTestRow; onStop: (t: LiveTestRow) => void }) {
  const [open, setOpen] = useState(false);
  const s = t.summary;
  const c = t.config;
  return (
    <div className="space-y-3 rounded border border-gray-200 p-3 dark:border-gray-700">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h3 className="text-sm font-semibold text-gray-900 dark:text-gray-100">[{t.id}] {c.name}</h3>
          <p className="text-xs text-gray-500 dark:text-gray-400">
            {c.timeframe} · {MODE_LABEL[c.mode] ?? c.mode} · {c.indicators.join(" + ")} · {DIRECTION_LABEL[c.direction ?? "both"]} ·{" "}
            {c.margin_usdt ?? "-"} USDT x{c.leverage ?? "-"} · SL {c.exit_name === "pct" && c.pct_exit ? `${c.pct_exit[0]} %` : c.exit_name}
            {c.take_profits && c.take_profits.length > 0
              ? ` · TP ${c.take_profits.map(([d, cl]) => `${d} %→${cl} %`).join(", ")}${c.breakeven ? " · SL a entrada tras TP1" : ""}`
              : ""}
            {" "}· {c.symbols.length} monedas · desde {fmtTime(t.created_at_ms)}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <span className={`rounded px-2 py-0.5 text-xs font-medium ${t.status === "active" ? "bg-green-100 text-green-800 dark:bg-green-900/40 dark:text-green-200" : "bg-gray-100 text-gray-700 dark:bg-gray-700 dark:text-gray-200"}`}>
            {t.status === "active" ? "activa" : "detenida"}
          </span>
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
        <StatTile label="Abiertas" value={String(t.open_positions)} />
      </div>
      <ControlPanel t={t} />
      <button onClick={() => setOpen(!open)} className="text-xs text-blue-700 underline dark:text-blue-300">
        {open ? "Ocultar detalle" : "Ver detalle"}
      </button>
      {open && <TestDetail id={t.id} />}
    </div>
  );
}

export function LiveTab() {
  const options = useLiveOptions();
  const tests = useLiveTests();
  const stop = useStopLiveTest();
  const turn = useLiveBotSwitch();
  const [stopping, setStopping] = useState<LiveTestRow | null>(null);
  const [askOff, setAskOff] = useState(false);

  return (
    <div className="space-y-4">
      <Card title="Pruebas en vivo">
        {tests.isError ? (
          <Unavailable error={tests.error} what="Pruebas en vivo" />
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
            <p className="text-xs text-gray-500 dark:text-gray-400">
              Paper trading hacia adelante con el precio real de Bitunix; nunca órdenes reales. Si creas muchas
              pruebas y miras solo la que ganó, es sobreajuste hecho hacia adelante.
            </p>
            {tests.data.tests.length === 0 ? (
              <p className="text-sm text-gray-500">Todavía no hay pruebas. Crea la primera abajo.</p>
            ) : (
              tests.data.tests.map((t) => <TestCard key={t.id} t={t} onStop={setStopping} />)
            )}
          </div>
        ) : (
          <p className="text-sm text-gray-500">Cargando…</p>
        )}
      </Card>
      <Card title="Nueva prueba">
        {options.isError ? (
          <Unavailable error={options.error} what="Opciones" />
        ) : options.data ? (
          <CreateForm options={options.data} />
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
          title={`Detener la prueba ${stopping.id}`}
          warning="La prueba deja de operar y sus posiciones abiertas se cierran con el último precio. No se puede reanudar."
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
