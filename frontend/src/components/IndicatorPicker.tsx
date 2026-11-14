import { useState } from "react";
import type { IndicatorOption } from "../api";

// Casillas de indicadores y filtros, compartidas por «Indicadores» y «En vivo». Los indicadores con
// variantes (el Range Filter en sus modos de Frost, el cruce de dos EMAs con sus pares) se muestran
// como UNA casilla con un selector: se elige como mucho una variante por grupo.

const SELECT_LABEL: Record<string, string> = {
  rf: "Modo del Range Filter",
  emax: "Par de EMAs",
  cloud: "Longitud del Cloud",
  cloud_retest: "Longitud del retesteo del Cloud",
  fib: "Nivel de Fibonacci",
};

function groupsOf(options: IndicatorOption[]): Map<string, IndicatorOption[]> {
  const groups = new Map<string, IndicatorOption[]>();
  for (const i of options) {
    if (i.group) groups.set(i.group, [...(groups.get(i.group) ?? []), i]);
  }
  return groups;
}

/** Ids de «todos»: cada indicador suelto y la primera variante de cada grupo. */
export function allIndicatorIds(options: IndicatorOption[]): string[] {
  const groups = groupsOf(options);
  return options.filter((i) => !i.group || groups.get(i.group)?.[0]?.id === i.id).map((i) => i.id);
}

function KindTag({ kind, layout }: { kind: IndicatorOption["kind"]; layout: "grid" | "list" }) {
  if (layout === "grid") return <span className="text-xs text-gray-500">({kind})</span>;
  return (
    <span
      className={
        "rounded px-1.5 text-xs " +
        (kind === "evento"
          ? "bg-blue-50 text-blue-800 dark:bg-blue-900/40 dark:text-blue-200"
          : "bg-gray-100 text-gray-700 dark:bg-gray-700 dark:text-gray-200")
      }
    >
      {kind}
    </span>
  );
}

export function IndicatorPicker({
  options,
  selected,
  onChange,
  layout = "grid",
}: {
  options: IndicatorOption[];
  selected: string[];
  onChange: (next: string[]) => void;
  layout?: "grid" | "list";
}) {
  const groups = groupsOf(options);
  const [variantOf, setVariantOf] = useState<Record<string, string>>(() =>
    Object.fromEntries([...groups].map(([g, list]) => [g, list[0].id])),
  );
  const idsOf = (group: string) => (groups.get(group) ?? []).map((v) => v.id);
  // La variante que muestra el selector: la marcada, o la ultima elegida.
  const shown = (group: string) => idsOf(group).find((id) => selected.includes(id)) ?? variantOf[group];

  function toggle(id: string) {
    onChange(selected.includes(id) ? selected.filter((x) => x !== id) : [...selected, id]);
  }

  function toggleGroup(group: string) {
    const ids = idsOf(group);
    onChange(selected.some((x) => ids.includes(x)) ? selected.filter((x) => !ids.includes(x)) : [...selected, shown(group)]);
  }

  function chooseVariant(group: string, id: string) {
    const ids = idsOf(group);
    setVariantOf((cur) => ({ ...cur, [group]: id }));
    if (selected.some((x) => ids.includes(x))) onChange([...selected.filter((x) => !ids.includes(x)), id]);
  }

  const rowCls =
    layout === "grid"
      ? "flex items-start gap-2 text-sm text-gray-800 dark:text-gray-200"
      : "flex flex-wrap items-center gap-2 text-sm text-gray-800 dark:text-gray-200";
  const boxCls = layout === "grid" ? "mt-1" : undefined;

  const items = options.map((i) => {
    if (!i.group) {
      const row = (
        <label className={rowCls}>
          <input type="checkbox" checked={selected.includes(i.id)} onChange={() => toggle(i.id)} className={boxCls} />
          {layout === "grid" ? (
            <span>
              {i.label} <KindTag kind={i.kind} layout={layout} />
            </span>
          ) : (
            <>
              <span className="flex-1">{i.label}</span>
              <KindTag kind={i.kind} layout={layout} />
            </>
          )}
        </label>
      );
      return layout === "grid" ? (
        <div key={i.id}>{row}</div>
      ) : (
        <li key={i.id} className="py-1">
          {row}
        </li>
      );
    }
    const list = groups.get(i.group) ?? [];
    if (list[0]?.id !== i.id) return null;
    const group = i.group;
    const label = i.group_label ?? group;
    const select = (
      <select
        className="ml-2 rounded border border-gray-300 bg-white px-1 py-0.5 text-xs dark:border-gray-600 dark:bg-gray-800"
        value={shown(group)}
        onChange={(e) => chooseVariant(group, e.target.value)}
        aria-label={SELECT_LABEL[group] ?? `Variante de ${label}`}
      >
        {list.map((v) => (
          <option key={v.id} value={v.id}>
            {v.variant ?? v.label}
          </option>
        ))}
      </select>
    );
    const row = (
      <div className={rowCls}>
        <input
          type="checkbox"
          checked={list.some((v) => selected.includes(v.id))}
          onChange={() => toggleGroup(group)}
          className={boxCls}
          aria-label={label}
        />
        {layout === "grid" ? (
          <span>
            {label} <KindTag kind={i.kind} layout={layout} />
            {select}
          </span>
        ) : (
          <>
            <span className="flex-1">
              {label}
              {select}
            </span>
            <KindTag kind={i.kind} layout={layout} />
          </>
        )}
      </div>
    );
    return layout === "grid" ? (
      <div key={group}>{row}</div>
    ) : (
      <li key={group} className="py-1">
        {row}
      </li>
    );
  });

  return layout === "grid" ? (
    <div className="grid gap-1 sm:grid-cols-2">{items}</div>
  ) : (
    <ul className="divide-y divide-gray-100 dark:divide-gray-700">{items}</ul>
  );
}
