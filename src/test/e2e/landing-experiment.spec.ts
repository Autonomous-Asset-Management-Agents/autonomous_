/**
 * #4469 / #4869 (ADR-026 Weg D): A/B test of the hero message without flicker.
 *
 * In production the `edge` function writes the assigned variant into the HTML as
 * <meta name="aaa-experiments">. The dev server has no function, so this spec puts the element
 * into the served HTML the same way (route interception) and checks what the browser does with
 * it: the variant is the first hero the visitor ever sees, CLS < 0.1, and the page sends no
 * measurement request and stores nothing. Assignment and exposure counting are tested in
 * functions/test/edge.test.mjs. `/` redirects to /console on localhost (App.tsx `isOssHost`), so
 * the landing is visited via /preview, which renders the same LandingViewE.
 */
import { test, expect, type Page } from "@playwright/test";

const CONTROL = /Your digital asset/;
const POSITIONING = /Your strategy\./;

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

async function serveWithVariant(page: Page, content: string | null) {
    await page.route("**/preview", async (route) => {
        const response = await route.fetch();
        let body = await response.text();
        if (content) body = body.replace("</head>", `<meta name="aaa-experiments" content="${content}"></head>`);
        await route.fulfill({ response, body });
    });
}

const CASES = [
    { meta: null, shows: CONTROL, never: POSITIONING, label: "no variant from the server → control" },
    { meta: "hero_positioning=positioning", shows: POSITIONING, never: CONTROL, label: "positioning" },
];

test.describe("Landing — flicker-free hero experiment (#4469, #4869)", () => {
    for (const c of CASES) {
        test(`${c.label} is the first hero the visitor sees, CLS < 0.1`, async ({ page, context }) => {
            await observeHeroAndLayoutShift(page);
            await serveWithVariant(page, c.meta);
            const measurement: string[] = [];
            page.on("request", (r) => {
                if (r.url().includes("/api/send")) measurement.push(r.url());
            });

            await page.goto("/preview");
            await expect(page.locator(".lb-hero h1")).toHaveText(c.shows);

            const texts = await page.evaluate(() => window.__heroTexts);
            expect(texts.length).toBeGreaterThan(0);
            for (const text of texts) {
                expect(text).toMatch(c.shows);
                expect(text).not.toMatch(c.never);
            }

            await page.waitForLoadState("networkidle");
            await page.waitForTimeout(1000);
            const cls = await page.evaluate(() => window.__cls);
            test.info().annotations.push({ type: "cls", description: cls.toFixed(4) });
            expect(cls).toBeLessThan(0.1);

            expect(measurement).toEqual([]);
            expect(await context.cookies()).toEqual([]);
            const storageKeys = await page.evaluate(() => [
                ...Object.keys(window.localStorage),
                ...Object.keys(window.sessionStorage),
            ]);
            expect(storageKeys.filter((k) => /experiment|growthbook|hero_positioning/i.test(k))).toEqual([]);
        });
    }
});
