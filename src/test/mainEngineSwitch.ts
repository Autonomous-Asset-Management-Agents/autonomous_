// #5178: shared test driver for the Paper/Live switch of the main engine. The switch is raised from
// the account areas under System → Trading keys (TradingKeysCard) — the former LiveTradingSwitchCard
// tiles are gone. Not a test file itself (no `.test.` in the name).
import { screen, fireEvent, waitFor } from "@testing-library/react";
import { expect } from "vitest";

/** The critical pill in the Live area (exact name — the modal's confirm is "Enable live trading"). */
export const ENABLE_LIVE_PILL = "ENABLE LIVE TRADING";
/** The confirm button inside the global LiveConsentModal. */
export const MODAL_ENABLE = "Enable live trading";

/** Click "ENABLE LIVE TRADING" once the gate lets it (account seeded, live keys probed). */
export async function pressEnableLive(): Promise<void> {
  const pill = await screen.findByRole("button", { name: ENABLE_LIVE_PILL });
  await waitFor(() => expect(pill).not.toBeDisabled());
  fireEvent.click(pill);
}

/** Click "Switch main engine to paper" once the gate lets it (main engine seeded live). */
export async function pressSwitchToPaper(): Promise<void> {
  const back = await screen.findByRole("button", { name: /switch main engine to paper/i });
  await waitFor(() => expect(back).not.toBeDisabled());
  fireEvent.click(back);
}
