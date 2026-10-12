// #4469 (ADR-026 §3): flicker-free A/B experiments on the landing. The assignment is computed
// synchronously from a build-time GrowthBook features payload and an in-memory id per page load —
// no network round trip, no change after the first render, no cookie and no Web Storage. Any error
// falls back to the control variant with a warning.
import { afterEach, describe, expect, it, vi } from "vitest";
import { renderHook } from "@testing-library/react";

import { createExperiments, type FeaturesPayload } from "@/lib/experiments";
import { useExperiment } from "../useExperiment";

const PAYLOAD: FeaturesPayload = {
  features: {
    hero_positioning: {
      defaultValue: "control",
      rules: [
        {
          key: "hero_positioning",
          variations: ["control", "positioning"],
          weights: [0.5, 0.5],
          coverage: 1,
          hashAttribute: "id",
        },
      ],
    },
  },
};

const create = (opts: Partial<Parameters<typeof createExperiments>[0]> = {}) =>
  createExperiments({ payload: JSON.stringify(PAYLOAD), id: "visitor-1", search: "", ...opts });

const storageSpies = () => [
  vi.spyOn(window.localStorage, "setItem"),
  vi.spyOn(window.localStorage, "getItem"),
  vi.spyOn(window.sessionStorage, "setItem"),
  vi.spyOn(window.sessionStorage, "getItem"),
  vi.spyOn(Document.prototype, "cookie", "set"),
  vi.spyOn(Document.prototype, "cookie", "get"),
];

afterEach(() => vi.restoreAllMocks());

describe("experiment assignment", () => {
  it("is deterministic for the same id", () => {
    const a = create().evaluate("hero_positioning");
    const b = create().evaluate("hero_positioning");
    expect(a.inExperiment).toBe(true);
    expect(b).toEqual(a);
  });

  it("splits ids across both variations by the configured weights", () => {
    const counts: Record<string, number> = {};
    for (let i = 0; i < 2000; i++) {
      const { value } = create({ id: `visitor-${i}` }).evaluate("hero_positioning");
      counts[value] = (counts[value] ?? 0) + 1;
    }
    expect(Object.keys(counts).sort()).toEqual(["control", "positioning"]);
    expect(counts.positioning / 2000).toBeGreaterThan(0.45);
    expect(counts.positioning / 2000).toBeLessThan(0.55);
  });

  it("can be forced per variation index via the query string (QA, E2E)", () => {
    for (const [idx, value] of [["0", "control"], ["1", "positioning"]] as const) {
      const r = create({ search: `?hero_positioning=${idx}` }).evaluate("hero_positioning");
      expect(r).toMatchObject({ value, inExperiment: true, variationId: idx });
    }
  });

  it("falls back to control with a warning on an invalid payload", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const r = create({ payload: "{not json" }).evaluate("hero_positioning");
    expect(r).toMatchObject({ value: "control", inExperiment: false });
    expect(warn).toHaveBeenCalled();
  });

  it("falls back to control with a warning when the experiment is not defined", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const r = create({ payload: undefined }).evaluate("hero_positioning");
    expect(r).toMatchObject({ value: "control", inExperiment: false });
    expect(warn).toHaveBeenCalledTimes(1);
  });

  it("falls back to control on targeting it cannot evaluate (condition, namespace, filters, ranges)", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const rule = PAYLOAD.features!.hero_positioning.rules![0];
    const targeted = { features: { hero_positioning: { rules: [{ ...rule, condition: { country: "DE" } }] } } };
    const r = create({ payload: JSON.stringify(targeted) }).evaluate("hero_positioning");
    expect(r).toMatchObject({ value: "control", inExperiment: false });
    expect(warn).toHaveBeenCalledTimes(1);
  });

  it("never reads or writes cookies, localStorage or sessionStorage", () => {
    const spies = storageSpies();
    create().evaluate("hero_positioning");
    for (const spy of spies) expect(spy).not.toHaveBeenCalled();
  });
});

describe("useExperiment", () => {
  it("returns the assignment on the first render and never switches", () => {
    const experiments = create();
    const expected = experiments.evaluate("hero_positioning").value;
    const seen: string[] = [];
    const { rerender } = renderHook(() => {
      const v = useExperiment("hero_positioning", "control", experiments);
      seen.push(v);
      return v;
    });
    rerender();
    expect(seen.length).toBeGreaterThan(1);
    expect(new Set(seen)).toEqual(new Set([expected]));
  });

  it("reports one exposure per experiment, without storage access", () => {
    const spies = storageSpies();
    const onExposure = vi.fn();
    const experiments = create({ search: "?hero_positioning=1", onExposure });
    const { rerender } = renderHook(() => useExperiment("hero_positioning", "control", experiments));
    rerender();
    renderHook(() => useExperiment("hero_positioning", "control", experiments));
    expect(onExposure).toHaveBeenCalledTimes(1);
    expect(onExposure).toHaveBeenCalledWith("hero_positioning", "1");
    for (const spy of spies) expect(spy).not.toHaveBeenCalled();
  });

  it("renders control and reports nothing when evaluation throws", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const onExposure = vi.fn();
    const broken = {
      evaluate: () => {
        throw new Error("boom");
      },
      reportExposure: onExposure,
    };
    const { result } = renderHook(() => useExperiment("hero_positioning", "control", broken));
    expect(result.current).toBe("control");
    expect(onExposure).not.toHaveBeenCalled();
    expect(warn).toHaveBeenCalled();
  });
});
