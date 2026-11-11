import { useState, type FormEvent } from "react";
import {
  ApiError,
  type LiveOptions,
  type LiveSummary,
  type LiveTestRow,
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

function CreateForm({ options }: { options: LiveOptions }) {
  const create = useCreateLiveTest();
  const [name, setName] = useState("");
  const [symbols, setSymbols] = useState(options.default_symbols.join(" "));
  const [timeframe, setTimeframe] = useState(options.timeframes[0] ?? "15m");
  const [indicators, setIndicators] = useState<string[]>([]);
  const [mode, setMode] = useState("close");
  const [exitName, setExitName] = useState(options.exits[0]?.id ?? "s2_tp2r");
  const [slPct, setSlPct] = useState("1");
  const [tpPct, setTpPct] = useState("2");
  const [trailPct, setTrailPct] = useState("");
  const [minAgree, setMinAgree] = useState("");
  const [evalSeconds, setEvalSeconds] = useState(String(options.eval_seconds.default));
  const [created, setCreated] = useState<string | null>(null);

  function toggle(id: string) {
    setIndicators((cur) => (cur.includes(id) ? cur.filter((x) => x !== id) : [...cur, id]));
  }

  function num(text: string): number | null {
    const v = Number(text.replace(",", "."));
    return text.trim() === "" || !Number.isFinite(v) ? null : v;
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    setCreated(null);
    create.mutate(
      {
        name, symbols: symbols.split(/[\s,]+/).filter(Boolean), timeframe, indicators, mode,
        exit_name: exitName,
        sl_pct: exitName === "pct" ? num(slPct) : null,
        tp_pct: exitName === "pct" ? num(tpPct) : null,
        trail_pct: exitName === "pct" ? num(trailPct) : null,
        window: options.default_window,
        min_agree: num(minAgree),
        eval_seconds: num(evalSeconds) ?? options.eval_seconds.default,
      },
      { onSuccess: (r) => setCreated(`Prueba ${r.id} creada: «${r.name}». Empieza a operar en unos segundos.`) },
    );
  }

  const field = "rounded border border-gray-300 px-2 py-1 text-sm dark:border-gray-600 dark:bg-gray-900 dark:text-gray-100";
  return (
    <form onSubmit={submit} className="space-y-3" aria-label="Nueva prueba en vivo">
      <div className="grid gap-3 sm:grid-cols-2">
        <label className="flex flex-col gap-1 text-xs text-gray-600 dark:text-gray-300">
          Nombre
          <input className={field} value={name} onChange={(e) => setName(e.target.value)} maxLength={60} required />
        </label>
        <label className="flex flex-col gap-1 text-xs text-gray-600 dark:text-gray-300">
          Monedas (separadas por espacio)
          <input className={field} value={symbols} onChange={(e) => setSymbols(e.target.value)} />
        </label>
        <label className="flex flex-col gap-1 text-xs text-gray-600 dark:text-gray-300">
          Marco
          <select className={field} value={timeframe} onChange={(e) => setTimeframe(e.target.value)}>
            {options.timeframes.map((t) => <option key={t} value={t}>{t}</option>)}
          </select>
        </label>
        <label className="flex flex-col gap-1 text-xs text-gray-600 dark:text-gray-300">
          Modo
          <select className={field} value={mode} onChange={(e) => setMode(e.target.value)}>
            {options.modes.map((m) => <option key={m} value={m}>{MODE_LABEL[m] ?? m}</option>)}
          </select>
        </label>
        {mode === "intrabar" && (
          <label className="flex flex-col gap-1 text-xs text-gray-600 dark:text-gray-300">
            Reevaluar cada (segundos)
            <input className={field} value={evalSeconds} onChange={(e) => setEvalSeconds(e.target.value)} inputMode="numeric" />
          </label>
        )}
        <label className="flex flex-col gap-1 text-xs text-gray-600 dark:text-gray-300">
          Cuántos deben coincidir (vacío = todos)
          <input className={field} value={minAgree} onChange={(e) => setMinAgree(e.target.value)} inputMode="numeric" />
        </label>
        <label className="flex flex-col gap-1 text-xs text-gray-600 dark:text-gray-300">
          Salida
          <select className={field} value={exitName} onChange={(e) => setExitName(e.target.value)}>
            {options.exits.map((x) => <option key={x.id} value={x.id}>{x.label}</option>)}
          </select>
        </label>
        {exitName === "pct" && (
          <div className="grid grid-cols-3 gap-2">
            <label className="flex flex-col gap-1 text-xs text-gray-600 dark:text-gray-300">
              SL %<input className={field} value={slPct} onChange={(e) => setSlPct(e.target.value)} />
            </label>
            <label className="flex flex-col gap-1 text-xs text-gray-600 dark:text-gray-300">
              TP %<input className={field} value={tpPct} onChange={(e) => setTpPct(e.target.value)} />
            </label>
            <label className="flex flex-col gap-1 text-xs text-gray-600 dark:text-gray-300">
              Trailing %<input className={field} value={trailPct} onChange={(e) => setTrailPct(e.target.value)} />
            </label>
          </div>
        )}
      </div>
      <fieldset className="space-y-1">
        <legend className="text-xs text-gray-600 dark:text-gray-300">Indicadores y filtros (se entra cuando coinciden)</legend>
        <div className="grid gap-1 sm:grid-cols-2">
          {options.indicators.map((i) => (
            <label key={i.id} className="flex items-start gap-2 text-sm text-gray-800 dark:text-gray-200">
              <input type="checkbox" checked={indicators.includes(i.id)} onChange={() => toggle(i.id)} className="mt-1" />
              <span>{i.label} <span className="text-xs text-gray-500">({i.kind})</span></span>
            </label>
          ))}
        </div>
      </fieldset>
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
                {p.symbol} {p.side} a {p.entry_price} desde {fmtTime(p.entry_time_ms)} · SL {p.stop} · TP {p.tp ?? "-"}
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
                  <td>{t.exit_price}</td><td>{t.close_reason}</td><td>{fmtUsdt(t.pnl_net_usdt)}</td>
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
            {c.timeframe} · {MODE_LABEL[c.mode] ?? c.mode} · {c.indicators.join(" + ")} · salida {c.exit_name} ·{" "}
            {c.symbols.length} monedas · desde {fmtTime(t.created_at_ms)}
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
