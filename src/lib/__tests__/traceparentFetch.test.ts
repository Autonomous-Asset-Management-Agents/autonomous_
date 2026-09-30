// INF-13 OBS-5 (#2638): the frontend must attach a W3C traceparent to EVERY
// engine request so UI-click -> engine-handler is one trace. Because 6+ call
// sites use raw `fetch` (bypassing fetchJson), the injection point is a global
// window.fetch patch gated to the engine origin.
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../api", () => ({ getApiBase: () => "http://127.0.0.1:8001" }));

import { genTraceparent, installTraceparentFetch } from "../traceparentFetch";

const TP_RE = /^00-[0-9a-f]{32}-[0-9a-f]{16}-01$/;
const orig = vi.fn(() => Promise.resolve(new Response("{}")));

beforeAll(() => {
  // install once over our mock so the wrapper delegates to `orig`
  window.fetch = orig as unknown as typeof fetch;
  installTraceparentFetch();
});
beforeEach(() => orig.mockClear());

function lastInitHeaders(): Headers {
  const init = orig.mock.calls[0][1] as RequestInit | undefined;
  return new Headers(init?.headers);
}

describe("genTraceparent", () => {
  it("produces a well-formed W3C traceparent", () => {
    expect(genTraceparent().header).toMatch(TP_RE);
  });
  it("is unique per call", () => {
    expect(genTraceparent().header).not.toEqual(genTraceparent().header);
  });
});

describe("installTraceparentFetch (engine-origin gated)", () => {
  it("injects traceparent on an engine-origin request (incl. raw fetch)", async () => {
    await window.fetch("http://127.0.0.1:8001/top-picks");
    expect(lastInitHeaders().get("traceparent")).toMatch(TP_RE);
  });

  it("does NOT inject on a non-engine origin", async () => {
    await window.fetch("https://my-project.firebaseio.com/x.json");
    expect(lastInitHeaders().get("traceparent")).toBeNull();
  });

  it("does not overwrite a pre-set traceparent", async () => {
    await window.fetch("http://127.0.0.1:8001/x", {
      headers: { traceparent: "00-preset-value-01" },
    });
    expect(lastInitHeaders().get("traceparent")).toBe("00-preset-value-01");
  });
});
