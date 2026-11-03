// #4869 (#4581 S1, ADR-026 Weg D): the landing variant is chosen by the `edge` function and
// written into <meta name="aaa-experiments" content="key=value;…">. The browser only reads that
// element: no hashing, no window.location, no exposure report, no cookie and no Web Storage.
// Without the element (static hosting today, OSS build) every experiment renders control.
import { afterEach, describe, expect, it, vi } from "vitest";
import { renderHook } from "@testing-library/react";

import { createExperiments, experimentsFromDocument, parseExperimentsMeta } from "@/lib/experiments";
import { useExperiment } from "../useExperiment";

const storageSpies = () => [
  vi.spyOn(window.localStorage, "setItem"),
  vi.spyOn(window.localStorage, "getItem"),
  vi.spyOn(window.sessionStorage, "setItem"),
  vi.spyOn(window.sessionStorage, "getItem"),
  vi.spyOn(Document.prototype, "cookie", "set"),
  vi.spyOn(Document.prototype, "cookie", "get"),
];

afterEach(() => {
  vi.restoreAllMocks();
  document.head.querySelectorAll('meta[name="aaa-experiments"]').forEach((m) => m.remove());
  window.history.replaceState(null, "", "/");
});

describe("parseExperimentsMeta", () => {
  it("reads key=value pairs separated by ';'", () => {
    expect(parseExperimentsMeta("hero_positioning=transparency;other=b")).toEqual({
      hero_positioning: "transparency",
      other: "b",
    });
  });

  it("ignores empty and malformed entries", () => {
    expect(parseExperimentsMeta(null)).toEqual({});
    expect(parseExperimentsMeta("")).toEqual({});
    expect(parseExperimentsMeta("broken;=x;y=;a=b")).toEqual({ a: "b" });
  });
});

describe("experiment assignment", () => {
  it("returns the variant the server wrote", () => {
    const r = createExperiments({ content: "hero_positioning=positioning" }).evaluate("hero_positioning");
    expect(r).toEqual({ value: "positioning", inExperiment: true });
  });

  it("falls back to control with one warning when the server assigned nothing", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const experiments = createExperiments({ content: null });
    expect(experiments.evaluate("hero_positioning")).toEqual({ value: "control", inExperiment: false });
    experiments.evaluate("hero_positioning");
    expect(warn).toHaveBeenCalledTimes(1);
  });

  it("reads the <meta name=aaa-experiments> element of the document", () => {
    const meta = document.createElement("meta");
    meta.name = "aaa-experiments";
    meta.content = "hero_positioning=transparency";
    document.head.appendChild(meta);
    expect(experimentsFromDocument(document).evaluate("hero_positioning").value).toBe("transparency");
  });

  it("ignores the query string — the server alone assigns (and handles ?hero_positioning=<i>)", () => {
    vi.spyOn(console, "warn").mockImplementation(() => {});
    window.history.replaceState(null, "", "/preview?hero_positioning=1");
    expect(experimentsFromDocument(document).evaluate("hero_positioning").value).toBe("control");
  });

  it("never reads or writes cookies, localStorage or sessionStorage", () => {
    const spies = storageSpies();
    createExperiments({ content: "hero_positioning=positioning" }).evaluate("hero_positioning");
    for (const spy of spies) expect(spy).not.toHaveBeenCalled();
  });
});

describe("useExperiment", () => {
  it("returns the assignment on the first render and never switches", () => {
    const experiments = createExperiments({ content: "hero_positioning=positioning" });
    const seen: string[] = [];
    const { rerender } = renderHook(() => {
      const v = useExperiment("hero_positioning", "control", experiments);
      seen.push(v);
      return v;
    });
    rerender();
    expect(seen.length).toBeGreaterThan(1);
    expect(new Set(seen)).toEqual(new Set(["positioning"]));
  });

  it("renders control when evaluation throws", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const broken = {
      evaluate: () => {
        throw new Error("boom");
      },
    };
    const { result } = renderHook(() => useExperiment("hero_positioning", "control", broken));
    expect(result.current).toBe("control");
    expect(warn).toHaveBeenCalled();
  });
});
