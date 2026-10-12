import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, it, expect, vi } from "vitest";

/**
 * #4467 (ADR-026 §6): the landing variant measures through the cookieless client
 * (src/lib/analytics.ts), not GA4. AppContent must not report the landing-b impression to
 * Firebase Analytics — that call is what pulls gtag.js from googletagmanager.com. The console
 * variants keep their GA4 impression unchanged.
 */
const h = vi.hoisted(() => ({ variant: "v1", track: vi.fn() }));

vi.mock("@/components/PrivateRoute", () => ({
  PrivateRoute: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}));
vi.mock("firebase/auth", () => ({
  signOut: vi.fn(),
  getAuth: vi.fn(),
  onAuthStateChanged: vi.fn(() => vi.fn()),
  GoogleAuthProvider: vi.fn(),
}));
vi.mock("@/lib/firebase", () => ({ trackVariantImpression: h.track }));
vi.mock("@/hooks/useDesignVariant", () => ({
  useDesignVariant: () => ({ variant: h.variant }),
  DesignVariant: {},
}));
vi.mock("@/lib/editor/useEditMode", () => ({
  useEditMode: () => ({ active: false, user: null }),
}));
vi.mock("@/console/ConsoleApp", () => ({ default: () => <div>CONSOLE-MARKER</div> }));

import { AppContent } from "../App";

const renderConsole = () =>
  render(
    <MemoryRouter initialEntries={["/console"]}>
      <AppContent />
    </MemoryRouter>,
  );

describe("GA4 variant impression (#4467)", () => {
  beforeEach(() => h.track.mockClear());

  it("is not sent for the landing variant", async () => {
    h.variant = "landing-b";
    renderConsole();
    expect(await screen.findByText("CONSOLE-MARKER")).toBeTruthy();
    expect(h.track).not.toHaveBeenCalled();
  });

  it("is still sent for the console variant", async () => {
    h.variant = "v1";
    renderConsole();
    expect(await screen.findByText("CONSOLE-MARKER")).toBeTruthy();
    expect(h.track).toHaveBeenCalledWith("v1");
  });
});
