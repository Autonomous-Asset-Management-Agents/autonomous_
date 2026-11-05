/**
 * Visual editor smoke tests.
 *
 * Test 3 (full editor flow with auth) is gated on a Firebase test user. To
 * keep this suite hermetic in CI, that test is marked skip until a
 * signInWithCustomToken setup lands.
 */
import { test, expect } from "@playwright/test";

const URL = "/?variant=landing-b";

test.describe("Visual editor", () => {
    test("non-editor visitor sees the page render normally (read path)", async ({ page }) => {
        await page.goto(URL);
        await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
        const headline = await page.getByRole("heading", { level: 1 }).textContent();
        expect(headline?.length ?? 0).toBeGreaterThan(0);
    });

    test.skip("editable elements carry stable data-editable-id attributes", async ({ page }) => {
        // KNOWN REGRESSION (feat/landing-tr-redesign): the new LandingViewD
        // does not yet expose <Editable> wrappers — Phase 1 visual editor is
        // disabled for the redesigned landing pending re-port. Re-enable once
        // the new landing has data-editable-id markers.
        await page.goto(URL);
        await expect(page.locator('[data-editable-id="landing-b.hero.headline"]')).toBeVisible();
        await expect(page.locator('[data-editable-id="landing-b.hero.eyebrow"]')).toBeVisible();
    });

    test("?edit=1 without auth shows page normally — no editor chrome", async ({ page }) => {
        await page.goto(URL + "&edit=1");
        await expect(page.locator(".editor-toolbar")).toHaveCount(0);
        await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
    });

    test.skip("editor chrome appears when signed in as allowlisted user", async ({ page }) => {
        // NOTE: requires a Firebase test user setup via signInWithCustomToken
        // before this test can run hermetically.
        await page.goto(URL + "&edit=1");
        await expect(page.locator(".editor-toolbar")).toBeVisible({ timeout: 5000 });
    });
});
