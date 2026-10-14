/**
 * #4469 (ADR-026 §3): A/B test of the hero message without flicker.
 *
 * The dev server gets a GrowthBook features payload with the experiment "hero_positioning" via
 * VITE_EXPERIMENTS (playwright.config.ts); `?hero_positioning=<index>` forces a variation. An init
 * script records every text the hero headline ever shows — from its first insertion into the DOM —
 * and sums the layout shifts, so "the other variant was never rendered" and "CLS < 0.1" are
 * measured, not assumed. Exposure events go to the intercepted ingest host; nothing leaves the
 * machine. `/` redirects to /console on localhost (App.tsx `isOssHost`), so the landing is visited
 * via /preview, which renders the same LandingViewE.
 */
import { test, expect, type Page } from "@playwright/test";

const ANALYTICS_HOST = process.env.VITE_ANALYTICS_HOST ?? "https://analytics.e2e.invalid";
const INGEST = `${ANALYTICS_HOST.replace(/\/+$/, "")}/api/send`;

const CONTROL = /Your digital asset/;
const POSITIONING = /Your strategy\./;

type Sent = { type: string; payload: { name?: string; data?: Record<string, string> } };

declare global {
    interface Window {
        __heroTexts: string[];
        __cls: number;
    }
}

async function observeHeroAndLayoutShift(page: Page) {
    await page.addInitScript(() => {
        window.__heroTexts = [];
        window.__cls = 0;
        const record = () => {
            const h1 = document.querySelector(".lb-hero h1");
            const text = h1?.textContent ?? "";
            if (text && window.__heroTexts[window.__heroTexts.length - 1] !== text) {
                window.__heroTexts.push(text);
            }
        };
        new MutationObserver(record).observe(document, {
            childList: true,
            subtree: true,
            characterData: true,
        });
        new PerformanceObserver((list) => {
            for (const entry of list.getEntries() as (PerformanceEntry & {
                value: number;
                hadRecentInput: boolean;
            })[]) {
                if (!entry.hadRecentInput) window.__cls += entry.value;
            }
        }).observe({ type: "layout-shift", buffered: true });
    });
}

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

const CASES = [
    { index: "0", shows: CONTROL, never: POSITIONING },
    { index: "1", shows: POSITIONING, never: CONTROL },
];

test.describe("Landing — flicker-free hero experiment (#4469)", () => {
    for (const c of CASES) {
        test(`variation ${c.index} is the first hero the visitor sees, CLS < 0.1`, async ({ page, context }) => {
            await observeHeroAndLayoutShift(page);
            const sent = await interceptIngest(page);

            await page.goto(`/preview?hero_positioning=${c.index}`);
            await expect(page.locator(".lb-hero h1")).toHaveText(c.shows);

            const texts = await page.evaluate(() => window.__heroTexts);
            expect(texts.length).toBeGreaterThan(0);
            for (const text of texts) {
                expect(text).toMatch(c.shows);
                expect(text).not.toMatch(c.never);
            }

            await expect
                .poll(() => sent.find((e) => e.payload.name === "experiment_viewed")?.payload.data)
                .toEqual({ experiment_id: "hero_positioning", variation_id: c.index });

            await page.waitForLoadState("networkidle");
            await page.waitForTimeout(1000);
            const cls = await page.evaluate(() => window.__cls);
            test.info().annotations.push({ type: "cls", description: cls.toFixed(4) });
            expect(cls).toBeLessThan(0.1);

            expect(await context.cookies()).toEqual([]);
            const storageKeys = await page.evaluate(() => [
                ...Object.keys(window.localStorage),
                ...Object.keys(window.sessionStorage),
            ]);
            expect(storageKeys.filter((k) => /experiment|growthbook|hero_positioning/i.test(k))).toEqual([]);
        });
    }
});
