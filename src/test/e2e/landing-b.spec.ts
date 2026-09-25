/**
 * Visual + interaction smoke tests for the landing-b variant — now backed by
 * LandingViewD (TR-style redesign). Filename kept as `landing-b.spec.ts`
 * because the variant string is still `landing-b` (see useDesignVariant.ts);
 * the visual it renders changed in feat/landing-tr-redesign.
 *
 * Old waitlist + agent-chat-API assertions removed — the new design has
 * no waitlist form and the chat input is purely client-side (stub reply).
 */
import { test, expect } from "@playwright/test";

const URL = "/?variant=landing-b";

const VIEWPORTS = [
    { name: "mobile", width: 375, height: 812 },
    { name: "mobile-l", width: 430, height: 900 },
    { name: "tablet", width: 768, height: 1024 },
    { name: "laptop", width: 1280, height: 800 },
    { name: "desktop", width: 1440, height: 900 },
    { name: "wide", width: 1920, height: 1080 },
];

test.describe("Landing — visual", () => {
    for (const vp of VIEWPORTS) {
        test(`renders at ${vp.name} (${vp.width}x${vp.height})`, async ({ page }) => {
            await page.setViewportSize({ width: vp.width, height: vp.height });
            await page.goto(URL);
            await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
            await page.waitForTimeout(400);
            await expect(page).toHaveScreenshot(`landing-b-${vp.name}.png`, {
                fullPage: false,
                animations: "disabled",
                maxDiffPixelRatio: 0.02,
            });
        });
    }
});

test.describe("Landing — chrome", () => {
    test("hero headline renders", async ({ page }) => {
        await page.goto(URL);
        await expect(page.getByRole("heading", { level: 1 })).toContainText(/Autonomous/i);
    });

    test("nav exposes a GitHub link to the org", async ({ page }) => {
        await page.goto(URL);
        const gh = page.locator('a.lb-nav-github[href*="github.com/Autonomous-Asset-Management-Agents"]');
        await expect(gh).toBeVisible();
    });

    test("footer Imprint/Privacy/Risk links route inside the SPA", async ({ page }) => {
        await page.goto(URL);
        for (const path of ["/legal/imprint", "/legal/privacy", "/legal/risk-disclosure"]) {
            await expect(page.locator(`a[href="${path}"]`)).toHaveCount(1);
        }
    });
});

test.describe("Landing — interactions", () => {
    test("risk banner can be dismissed and stays dismissed on reload", async ({ page }) => {
        await page.goto(URL);
        const banner = page.locator("#lbRiskBanner");
        await expect(banner).toBeVisible();
        await page.locator("#lbRiskClose").click();
        await expect(banner).toBeHidden();
        await page.reload();
        // localStorage flag persists → banner stays hidden.
        await expect(page.locator("#lbRiskBanner")).toBeHidden();
    });

    test("clicking a block CTA opens the matching overlay; ESC closes it", async ({ page }) => {
        await page.goto(URL);
        await page.locator('[data-open="profit"]').first().click();
        const overlay = page.locator("#overlayProfit");
        await expect(overlay).toHaveClass(/open/);
        await page.keyboard.press("Escape");
        await expect(overlay).not.toHaveClass(/open/);
    });

    test("chat input emits a stub reply (no API calls required)", async ({ page }) => {
        await page.goto(URL);
        const input = page.getByPlaceholder(/ask the agents/i);
        await input.fill("hello senate");
        await input.press("Enter");
        // The stub reply lands within ~600ms; allow generous timeout.
        await expect(page.locator(".lb-log .lb-line")).toContainText(/noted/i, { timeout: 3000 });
    });

    test("auto-halt counter renders four numeric segments (days/h/m/s)", async ({ page }) => {
        await page.goto(URL);
        const counter = page.locator(".sv-rec-counter");
        await expect(counter).toBeVisible();
        await expect(counter.locator("b#svRecDays")).toHaveText(/^\d+$/);
        await expect(counter.locator("b#svRecHrs")).toHaveText(/^\d{2}$/);
        await expect(counter.locator("b#svRecMin")).toHaveText(/^\d{2}$/);
        await expect(counter.locator("b#svRecSec")).toHaveText(/^\d{2}$/);
    });
});
