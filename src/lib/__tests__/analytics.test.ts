// #4467 (ADR-026): cookieless tracking client for the landing. Without build variables it is a
// no-op (OSS build ships no tracking) and says so once; with them it posts to the self-hosted
// Umami ingest endpoint and never touches cookies, localStorage or sessionStorage.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { createAnalytics } from "../analytics";

const HOST = "https://analytics.example.test";
const WEBSITE_ID = "7f3c1c1e-5a0b-4c55-9d1e-2f6b0f1b9a01";

const fetchMock = vi.fn<typeof fetch>(() => Promise.resolve(new Response("{}")));

const storageSpies = () => [
  vi.spyOn(window.localStorage, "setItem"),
  vi.spyOn(window.localStorage, "getItem"),
  vi.spyOn(window.sessionStorage, "setItem"),
  vi.spyOn(window.sessionStorage, "getItem"),
  vi.spyOn(Document.prototype, "cookie", "set"),
  vi.spyOn(Document.prototype, "cookie", "get"),
];

const sentBody = (call: number) => {
  const init = fetchMock.mock.calls[call][1] as RequestInit | undefined;
  return JSON.parse(String(init?.body));
};

beforeEach(() => {
  fetchMock.mockClear();
  window.fetch = fetchMock as unknown as typeof fetch;
});
afterEach(() => vi.restoreAllMocks());

describe("analytics without build variables (OSS build)", () => {
  it("is a no-op and warns exactly once", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const a = createAnalytics({});
    a.trackPageview();
    a.trackCta("request_access");
    a.trackDownload("windows");
    expect(fetchMock).not.toHaveBeenCalled();
    expect(warn).toHaveBeenCalledTimes(1);
    expect(String(warn.mock.calls[0][0])).toContain("VITE_ANALYTICS_HOST");
  });

  it("is a no-op when only the host is set (no website id)", () => {
    vi.spyOn(console, "warn").mockImplementation(() => {});
    createAnalytics({ VITE_ANALYTICS_HOST: HOST }).trackPageview();
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

describe("analytics with build variables", () => {
  const env = { VITE_ANALYTICS_HOST: `${HOST}/`, VITE_ANALYTICS_WEBSITE_ID: WEBSITE_ID };

  it("posts a pageview to <host>/api/send without credentials", () => {
    createAnalytics(env).trackPageview();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init = {}] = fetchMock.mock.calls[0];
    expect(url).toBe(`${HOST}/api/send`);
    expect(init.method).toBe("POST");
    expect(init.credentials).toBe("omit");
    expect(init.keepalive).toBe(true);
    const body = sentBody(0);
    expect(body.type).toBe("event");
    expect(body.payload.website).toBe(WEBSITE_ID);
    expect(body.payload.url).toBe(window.location.pathname + window.location.search);
    expect(body.payload.name).toBeUndefined();
  });

  it("sends cta_click{cta} and download{os} as named events", () => {
    const a = createAnalytics(env);
    a.trackCta("request_access");
    a.trackDownload("mac");
    expect(sentBody(0).payload).toMatchObject({ name: "cta_click", data: { cta: "request_access" } });
    expect(sentBody(1).payload).toMatchObject({ name: "download", data: { os: "mac" } });
  });

  it("sends an experiment exposure as experiment_viewed{experiment_id, variation_id} (#4469)", () => {
    createAnalytics(env).trackExposure("hero_positioning", "1");
    expect(sentBody(0).payload).toMatchObject({
      name: "experiment_viewed",
      data: { experiment_id: "hero_positioning", variation_id: "1" },
    });
  });

  it("sends no screen size (no fingerprinting beyond the HTTP request, ADR-026 §3)", () => {
    createAnalytics(env).trackPageview();
    expect(sentBody(0).payload.screen).toBeUndefined();
  });

  it("never reads or writes cookies, localStorage or sessionStorage", async () => {
    const spies = storageSpies();
    const a = createAnalytics(env);
    a.trackPageview();
    a.trackCta("github");
    a.trackDownload("windows");
    await Promise.resolve();
    for (const spy of spies) expect(spy).not.toHaveBeenCalled();
    expect(window.localStorage.length).toBe(0);
    expect(window.sessionStorage.length).toBe(0);
  });

  it("swallows a failed send with a warning instead of throwing", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    fetchMock.mockImplementationOnce(() => Promise.reject(new TypeError("blocked")));
    expect(() => createAnalytics(env).trackPageview()).not.toThrow();
    await vi.waitFor(() => expect(warn).toHaveBeenCalled());
  });
});
