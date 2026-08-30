/**
 * Browser E2E (Playwright): Onboarding journey — UX E2E #1050.
 *
 * The real Vite build in Chromium, with the Electron shell faked via an
 * injected `window.aaagents` (see consoleHarness). Mirrors the J1 journey
 * (`src/test/journeys/onboarding.journey.test.tsx`) at full browser fidelity.
 *
 * Skips itself when the console is auth-gated on the branch under test (the
 * desktop bypass from `fix/desktop-console-login-wall` makes it reachable).
 */
import { test, expect } from "@playwright/test";
import { installDesktopBridge, openConsole } from "./consoleHarness";
import * as fx from "../fixtures/consoleFixtures";

test.describe("E2E · Onboarding", () => {
  test("first run → wizard → Ollama → launch → console", async ({ page }) => {
    await installDesktopBridge(page, { hasKeychain: false, ollamaOk: true });
    test.skip(!(await openConsole(page)), "console is auth-gated on this branch (needs desktop bypass)");

    await expect(page.getByText(/set up autonomous_/i)).toBeVisible();
    await page.getByLabel("name").fill(fx.operator.name);
    await page.getByRole("button", { name: /continue/i }).click();

    await expect(page.getByText(/connect your broker/i)).toBeVisible();
    await page.getByLabel("alpaca-key-id").fill(fx.sampleKeys.alpacaKeyId);
    await page.getByLabel("alpaca-secret").fill(fx.sampleKeys.alpacaSecret);
    await page.getByRole("button", { name: /validate & continue/i }).click();

    await expect(page.getByText(/choose your llm/i)).toBeVisible();
    await page.getByRole("button", { name: /local \(ollama\)/i }).click();
    await page.getByRole("button", { name: /^continue$/i }).click();

    await expect(page.getByText(/you're ready/i)).toBeVisible();
    await page.getByRole("button", { name: /launch autonomous_/i }).click();

    await expect(page.getByPlaceholder(/message the engine/i)).toBeVisible();
  });

  test("Alpaca rejects the keys → error, no advance", async ({ page }) => {
    await installDesktopBridge(page, { hasKeychain: false, alpacaOk: false, alpacaStatus: 403 });
    test.skip(!(await openConsole(page)), "console is auth-gated on this branch (needs desktop bypass)");

    await page.getByLabel("name").fill(fx.operator.name);
    await page.getByRole("button", { name: /continue/i }).click();
    await page.getByLabel("alpaca-key-id").fill("bad");
    await page.getByLabel("alpaca-secret").fill("bad");
    await page.getByRole("button", { name: /validate & continue/i }).click();

    await expect(page.getByText(/alpaca rejected these keys/i)).toBeVisible();
    await expect(page.getByText(/connect your broker/i)).toBeVisible();
  });
});
