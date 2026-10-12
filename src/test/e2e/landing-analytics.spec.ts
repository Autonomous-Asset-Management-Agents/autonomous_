/**
 * #4467 (ADR-026): the landing measures visits and conversions without cookies.
 *
 * The dev server gets VITE_ANALYTICS_HOST / VITE_ANALYTICS_WEBSITE_ID from playwright.config.ts;
 * the ingest endpoint is intercepted here, so no request leaves the machine. `/` redirects to
 * /console on localhost (OSS host, App.tsx `isOssHost`), so the landing is visited via /preview,
 * which renders the same LandingViewE.
 */
import { test, expect, type Page, type Request } from "@playwright/test";

const ANALYTICS_HOST = process.env.VITE_ANALYTICS_HOST ?? "https://analytics.e2e.invalid";
const INGEST = `${ANALYTICS_HOST.replace(/\/+$/, "")}/api/send`;
const GOOGLE_TRACKING = /googletagmanager\.com|google-analytics\.com/;

type Sent = { type: string; payload: { name?: string; data?: Record<string, string> } };

async function interceptIngest(page: Page): Promise<Sent[]> {
    const sent: Sent[] = [];
    await page.route(INGEST, async (route) => {
        const headers = {
            "access-control-allow-origin": "*",
            "access-control-allow-headers": "content-type",
            "access-control-allow-methods": "POST, OPTIONS",
        };
        if (route.request().method() === "POST") sent.push(route.request().postDataJSON() as Sent);
        await route.fulfill({ status: 200, headers, body: "{}" });
    });
    return sent;
}

test.describe("Landing — cookieless analytics (#4467)", () => {
    test("pageview + conversions reach the self-hosted backend, nothing is stored", async ({ page, context }) => {
        const sent = await interceptIngest(page);
        const googleRequests: string[] = [];
        page.on("request", (r: Request) => {
            if (GOOGLE_TRACKING.test(r.url())) googleRequests.push(r.url());
        });

        await page.goto("/preview");
        await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
        await expect.poll(() => sent.filter((e) => !e.payload.name).length).toBeGreaterThanOrEqual(1);

        await page.getByLabel("Download — choose your edition").click();
        await expect
            .poll(() => sent.find((e) => e.payload.name === "download")?.payload.data?.os)
            .toMatch(/^(mac|windows)$/);

        // "Request access" sits in the block overlays, which have no visible opener on the current
        // landing; dispatch the click so it bubbles through the root handler like a real one.
        // mailto: must not navigate the test page away — only the event matters here.
        await page.locator("#overlaySafe a", { hasText: "Request access" }).evaluate((a) => {
            a.addEventListener("click", (e) => e.preventDefault());
            a.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true }));
        });
        await expect
            .poll(() => sent.find((e) => e.payload.name === "cta_click")?.payload.data?.cta)
            .toBe("request_access");

        expect(await context.cookies()).toEqual([]);
        const storageKeys = await page.evaluate(() => [
            ...Object.keys(window.localStorage),
            ...Object.keys(window.sessionStorage),
        ]);
        expect(storageKeys.filter((k) => /umami|analytics/i.test(k))).toEqual([]);
        expect(googleRequests).toEqual([]);
    });
});
