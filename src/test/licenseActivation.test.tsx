import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";

/**
 * LicenseActivation (GTM-2 #1809 T4) — the offline key-paste panel of the paid
 * Private/Senior path. Pasting a key calls activateLicense (engine verifies the Ed25519
 * signature offline); success refetches the entitlement + fires onActivated, failure
 * surfaces a visible note. It must NEVER fail silently, and never call activate on blank.
 */
const activateLicense = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, activateLicense: (...a: unknown[]) => activateLicense(...a) };
});

import { LicenseActivation } from "@/console/desktop/LicenseActivation";

const paste = (v: string) =>
  fireEvent.change(screen.getByPlaceholderText(/license key/i), { target: { value: v } });
const activate = () => fireEvent.click(screen.getByRole("button", { name: /^activate$/i }));

describe("LicenseActivation — offline key-paste", () => {
  beforeEach(() => activateLicense.mockReset());

  it("renders the paste field, the Activate button, and the offline explainer", () => {
    render(<LicenseActivation refetch={vi.fn()} />);
    expect(screen.getByPlaceholderText(/license key/i)).toBeTruthy();
    expect(screen.getByRole("button", { name: /^activate$/i })).toBeTruthy();
    expect(screen.getByText(/verified.*offline/i)).toBeTruthy();
  });

  it("does not call activate on a blank key — shows a hint instead", () => {
    render(<LicenseActivation refetch={vi.fn()} />);
    activate();
    expect(activateLicense).not.toHaveBeenCalled();
    expect(screen.getByText(/paste your license key/i)).toBeTruthy();
  });

  it("valid key: activates, refetches, fires onActivated, shows success", async () => {
    activateLicense.mockResolvedValue({ ok: true, tier: "PRO" });
    const refetch = vi.fn().mockResolvedValue(undefined);
    const onActivated = vi.fn();
    render(<LicenseActivation refetch={refetch} onActivated={onActivated} />);
    paste("AAA-SENIOR-token");
    activate();
    await waitFor(() => expect(refetch).toHaveBeenCalled());
    expect(activateLicense).toHaveBeenCalledWith("AAA-SENIOR-token");
    expect(onActivated).toHaveBeenCalled();
    expect(screen.getByText(/senior.*active|activated/i)).toBeTruthy();
  });

  it("invalid key: surfaces a visible failure note (never silent)", async () => {
    activateLicense.mockResolvedValue({ ok: false });
    render(<LicenseActivation refetch={vi.fn()} />);
    paste("garbage");
    activate();
    expect(await screen.findByText(/could not be verified/i)).toBeTruthy();
  });
});
