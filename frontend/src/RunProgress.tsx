import { useExplorerProgress } from "./api";

// Ventana de carga mientras corre una prueba de la pestana Indicadores: barra con el porcentaje,
// la moneda y el paso en curso (la combinacion o su control al azar), el tiempo transcurrido y un
// estimado de lo que falta. Consulta el avance al servidor cada segundo.

function fmtSeconds(s: number | null): string {
  if (s === null) return "-";
  const total = Math.round(s);
  const m = Math.floor(total / 60);
  const sec = total % 60;
  return m > 0 ? `${m} min ${sec} s` : `${sec} s`;
}

export function RunProgress() {
  const q = useExplorerProgress(true);
  const p = q.data;
  const pct = p && p.total > 0 ? Math.min(100, Math.round((100 * p.done) / p.total)) : 0;
  const coin = p ? Math.min(p.total / 2, Math.floor(p.done / 2) + (p.running ? 1 : 0)) : 0;
  return (
    <div className="fixed inset-0 z-40 flex items-center justify-center bg-black/40 p-4">
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="run-progress-title"
        className="w-full max-w-md space-y-3 rounded-lg border border-gray-200 bg-white p-5 shadow-lg dark:border-gray-700 dark:bg-gray-800"
      >
        <h2 id="run-progress-title" className="text-sm font-semibold text-gray-900 dark:text-gray-100">
          Calculando la prueba…
        </h2>
        <div
          role="progressbar"
          aria-label="Avance de la prueba"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={pct}
          className="h-3 w-full overflow-hidden rounded-full bg-gray-200 dark:bg-gray-700"
        >
          <div className="h-full rounded-full bg-blue-500 transition-[width]" style={{ width: `${pct}%` }} />
        </div>
        <p className="text-sm tabular-nums text-gray-700 dark:text-gray-300">
          {p && p.total > 0 ? (
            <>
              {pct} % · moneda {coin} de {p.total / 2}
              {p.symbol ? ` (${p.symbol}, ${p.stage === "control" ? "control al azar" : "combinación"})` : ""}
            </>
          ) : (
            "Preparando…"
          )}
        </p>
        <p className="text-xs text-gray-500 dark:text-gray-400">
          Llevas {fmtSeconds(p?.elapsed_s ?? null)}
          {p?.eta_s != null ? ` · faltan unos ${fmtSeconds(p.eta_s)}` : ""}. Con muchas monedas en 15m puede tardar
          varios minutos; el servidor sigue trabajando aunque la página no cambie.
        </p>
      </div>
    </div>
  );
}
