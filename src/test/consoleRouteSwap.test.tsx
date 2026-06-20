import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, it, expect, vi } from "vitest";

/**
 * G3-final (#1050): the route swap. `/console` now renders the ported operator
 * console (was the legacy Dashboard alias); the temporary `/console/app` mount
 * redirects to `/console`. Heavy app deps are mocked so this stays a focused
 * routing test — the console's own behaviour is covered by its page tests.
 */
// Auth is irrelevant to routing — passthrough PrivateRoute so the test asserts
// pure route→element mapping (no firebase/domain-gate complexity).
vi.mock("@/components/PrivateRoute", () => ({
  PrivateRoute: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}));
vi.mock("firebase/auth", () => ({
  signOut: vi.fn(),
  getAuth: vi.fn(),
  onAuthStateChanged: vi.fn(() => vi.fn()),
  GoogleAuthProvider: vi.fn(),
}));
vi.mock("@/lib/firebase", () => ({ trackVariantImpression: vi.fn() }));
vi.mock("@/hooks/useDesignVariant", () => ({
  useDesignVariant: () => ({ variant: "v1" }),
  DesignVariant: {},
}));
vi.mock("@/lib/editor/useEditMode", () => ({
  useEditMode: () => ({ active: false, user: null }),
}));
vi.mock("@/console/ConsoleApp", () => ({ default: () => <div>CONSOLE-MARKER</div> }));
vi.mock("@/pages/Dashboard", () => ({ default: () => <div>DASHBOARD-MARKER</div> }));

import { AppContent } from "../App";

const at = (path: string) =>
  render(
    <MemoryRouter initialEntries={[path]}>
      <AppContent />
    </MemoryRouter>,
  );

describe("G3-final route swap", () => {
  it("/console renders the operator console, not the legacy Dashboard", async () => {
    at("/console");
    expect(await screen.findByText("CONSOLE-MARKER")).toBeTruthy();
    expect(screen.queryByText("DASHBOARD-MARKER")).toBeNull();
  });

  it("the legacy /console/app redirects to /console (the console)", async () => {
    at("/console/app");
    expect(await screen.findByText("CONSOLE-MARKER")).toBeTruthy();
  });

  it("/dashboard still renders the Dashboard", async () => {
    at("/dashboard");
    expect(await screen.findByText("DASHBOARD-MARKER")).toBeTruthy();
  });
});
