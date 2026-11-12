import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { RunProgress } from "./RunProgress";
import { mockFetch, withQueryClient } from "./testUtils";

afterEach(() => vi.unstubAllGlobals());

describe("RunProgress", () => {
  it("muestra el porcentaje, la moneda, el paso y el tiempo que falta", async () => {
    vi.stubGlobal("fetch", mockFetch({
      "/api/explorer/progress": {
        status: 200,
        body: { running: true, done: 5, total: 20, symbol: "ETHUSDT", stage: "control", elapsed_s: 75, eta_s: 225 },
      },
    }));
    render(withQueryClient(<RunProgress />));
    expect(await screen.findByText(/25 % · moneda 3 de 10 \(ETHUSDT, control al azar\)/)).toBeInTheDocument();
    expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "25");
    expect(screen.getByText(/Llevas 1 min 15 s · faltan unos 3 min 45 s/)).toBeInTheDocument();
  });

  it("dice que esta preparando antes del primer paso", async () => {
    vi.stubGlobal("fetch", mockFetch({
      "/api/explorer/progress": {
        status: 200,
        body: { running: false, done: 0, total: 0, symbol: null, stage: null, elapsed_s: null, eta_s: null },
      },
    }));
    render(withQueryClient(<RunProgress />));
    expect(await screen.findByText("Preparando…")).toBeInTheDocument();
  });
});
