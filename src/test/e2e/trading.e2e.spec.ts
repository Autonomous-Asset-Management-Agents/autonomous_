/**
 * Browser E2E (Playwright): Operational-trading journey — UX E2E #1050.
 *
 * The real Vite build in Chromium with the engine HTTP intercepted from the
 * shared fixtures (see consoleHarness). Mirrors the J4 journey
 * (`src/test/journeys/trading.journey.test.tsx`) at full browser fidelity.
 *
 * Skips itself when the console is auth-gated on the branch under test.
 */
import { test, expect } from "@playwright/test";
import { installDesktopBridge, routeEngine, openConsole } from "./consoleHarness";

test.describe("E2E · Operational trading", () => {
  test("the engine book renders across Positions and Reports", async ({ page }) => {
    await installDesktopBridge(page, { hasKeychain: true });
    await routeEngine(page);
    test.skip(!(await openConsole(page)), "console is auth-gated on this branch (needs desktop bypass)");

    await page.getByRole("button", { name: /positions/i }).click();
    await expect(page.getByText("AAPL").first()).toBeVisible();
    await expect(page.getByText("NVDA").first()).toBeVisible();

    await page.getByRole("button", { name: /reports/i }).click();
    await expect(page.getByText("TSLA").first()).toBeVisible();
  });

  test("a warming-up engine shows honest empty states", async ({ page }) => {
    await installDesktopBridge(page, { hasKeychain: true });
    await routeEngine(page, { emptyBook: true });
    test.skip(!(await openConsole(page)), "console is auth-gated on this branch (needs desktop bypass)");

    await page.getByRole("button", { name: /positions/i }).click();
    await expect(page.getByText(/no open positions/i)).toBeVisible();
  });
});
