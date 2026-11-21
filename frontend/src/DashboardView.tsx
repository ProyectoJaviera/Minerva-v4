import { lazy, Suspense, useState } from "react";
import { AccountTab } from "./AccountTab";
import { ArmTab } from "./ArmTab";
import { IndicatorsTab } from "./IndicatorsTab";
import { LiveTab } from "./LiveTab";
import { RiskTab } from "./RiskTab";
import { StudyTab } from "./StudyTab";
import { SummaryTab } from "./SummaryTab";
import { UniverseTab } from "./UniverseTab";
import { useHealth, useLogout } from "./api";
import { CoinGeckoAttribution } from "./components/Attribution";
import { type TabDef, Tabs, tabId, tabPanelId } from "./components/Tabs";

// Fase 5: el dashboard pasa a tener pestanas. "Resumen" es todo lo que ya existia; las demas son
// la cuenta paper, el brazo ejecutado, el estado de riesgo con los controles y el universo con los
// veredictos de backtest. Cada pestana pide sus datos solo cuando se abre.
// La Sala de trading trae la libreria del grafico: se carga solo al abrir su pestana.
const SalaTab = lazy(() => import("./sala/SalaTab").then((m) => ({ default: m.SalaTab })));

const TABS: TabDef[] = [
  { id: "summary", label: "Resumen" },
  { id: "account", label: "Cuenta paper" },
  { id: "arm", label: "Brazo ejecutado" },
  { id: "risk", label: "Riesgo y controles" },
  { id: "universe", label: "Universo y backtest" },
  // v2: pruebas manuales y exploratorias de indicadores sobre el historial local.
  { id: "indicators", label: "Indicadores" },
  // v2: prueba hacia adelante de las estrategias en estudio y resumen de las rondas.
  { id: "study", label: "Estudio" },
  // v3: pruebas de paper hacia adelante con el precio en tiempo real.
  { id: "live", label: "En vivo" },
  // v4: bots que abren y cierran por eventos de los indicadores (sin gemelo al azar).
  { id: "bots", label: "Bots de señales" },
  // v4: trading manual en paper con el precio real, en paneles flotantes.
  { id: "sala", label: "Sala de trading" },
];

function ActiveTab({ id }: { id: string }) {
  switch (id) {
    case "account":
      return <AccountTab />;
    case "arm":
      return <ArmTab />;
    case "risk":
      return <RiskTab />;
    case "universe":
      return <UniverseTab />;
    case "indicators":
      return <IndicatorsTab />;
    case "study":
      return <StudyTab />;
    case "live":
      return <LiveTab />;
    case "bots":
      return <LiveTab kind="bot" />;
    case "sala":
      return (
        <Suspense fallback={<p className="text-sm text-gray-500">Cargando la sala…</p>}>
          <SalaTab />
        </Suspense>
      );
    default:
      return <SummaryTab />;
  }
}

export function DashboardView({ username }: { username: string }) {
  const logout = useLogout();
  const health = useHealth();
  const [active, setActive] = useState("summary");

  // Pausar el scheduler DETIENE LA MEDICION: el aviso se ve en todas las pestanas.
  const paused = health.data?.control?.paused === true;
  const kill = health.data?.control?.kill_switch_active === true;

  return (
    // v4: el dashboard usa todo el ancho de la pantalla (antes, solo una columna central).
    <div className="w-full space-y-4 px-3 py-4 sm:px-4 2xl:px-6">
      <div className="flex items-center justify-between">
        <span className="text-sm text-gray-500 dark:text-gray-400">Sesion: {username}</span>
        <button
          onClick={() => logout.mutate()}
          className="rounded border border-gray-300 px-3 py-1 text-sm text-gray-700 hover:bg-gray-100 dark:border-gray-600 dark:text-gray-200 dark:hover:bg-gray-700"
        >
          Cerrar sesion
        </button>
      </div>

      {(paused || kill) && (
        <div
          role="alert"
          className="rounded border border-red-300 bg-red-50 p-3 text-sm font-medium text-red-900 dark:border-red-700 dark:bg-red-900/30 dark:text-red-200"
        >
          {paused
            ? "LA MEDICION ESTA DETENIDA: el scheduler esta en pausa (sin senales, sombras ni etiquetado del LLM)."
            : null}
          {paused && kill ? " " : null}
          {kill ? "KILL SWITCH ACTIVO: no se abren entradas nuevas." : null}
        </div>
      )}

      <Tabs tabs={TABS} active={active} onChange={setActive} />
      <div role="tabpanel" id={tabPanelId(active)} aria-labelledby={tabId(active)}>
        <ActiveTab id={active} />
      </div>

      <footer className="border-t border-gray-200 pt-2 dark:border-gray-700">
        <CoinGeckoAttribution />
      </footer>
    </div>
  );
}
