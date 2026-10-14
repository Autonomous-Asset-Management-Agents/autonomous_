/* eslint-disable @typescript-eslint/no-explicit-any */
/**
 * LandingViewD — TR-style landing page ported from .tmp-design.html.
 *
 * Visually 1:1 with the source HTML at .tmp-design.html. All styles are scoped
 * under `.landing-d-root` (see src/styles/landing-d.css) to avoid collision
 * with the console's shadcn/Tailwind styles. The original LandingViewB.tsx
 * remains untouched for rollback.
 *
 * The source HTML embeds inline <script> blocks that:
 *   - init Lenis smooth-scroll
 *   - apply magnetic-button effect on CTAs
 *   - split headline text into per-word spans for reveal animation
 *   - reveal-on-scroll via IntersectionObserver
 *   - drive --lb-hp scroll-scrub variable for the hero pin
 *   - progressively reveal hero terminal chat as scroll progresses
 *   - render Block 2 senate vote ledger from a VOTES const
 *   - tick a live auto-halt counter (days/hours/min/sec since 2026-04-15 13:00 UTC)
 *   - dismiss the top risk banner via localStorage
 * (#4514 removed the Block 1 profit counter and the subpage overlays.)
 *
 * All of those behaviors are reproduced below as React useEffect hooks.
 */
import { useEffect, useRef, useState, FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { GERMAN_ENABLED } from "@/lib/languageGate";
import { Button } from "@/components/ui/button";
import { signInWithPopup } from "firebase/auth";

import {
  PricingCard,
} from "@/pages/PricingMaster";
import {
  getPricingData,
  sharedPricingStyles
} from "@/lib/pricing-config";

import { auth, googleProvider } from "@/lib/firebase";
import Lenis from "lenis";
import { scrollToHash } from "@/lib/scrollToHash";
import { toast } from "sonner";
import "@/styles/landing-d.css";
import "@/console/console.css";
import { EquityChart } from "@/console/shared/EquityChart";
import {
    IconDashboard,
    IconQueue,
    IconPositions,
    IconReports,
    IconAudit,
    IconSettings,
    IconChat,
    IconChevronRight,
    IconBolt,
} from "@/console/shared/Icons";
import { fmtEUR } from "@/console/lib/format";
import { greeting } from "@/console/lib/greeting";
import { platformDownload } from "@/lib/appVersion";
import { trackPageview, trackCta, trackDownload } from "@/lib/analytics";
import { useExperiment } from "@/hooks/useExperiment";
import { mitMarke } from "./mitMarke";

type ChatMessage =
    | { role: "user"; content: string }
    | { role: "agent"; name: string; content: string };

const MOCK_DATA = [
    { t: new Date(2026, 5, 1), eur: 100000 },
    { t: new Date(2026, 5, 2), eur: 101200 },
    { t: new Date(2026, 5, 3), eur: 100800 },
    { t: new Date(2026, 5, 4), eur: 102300 },
    { t: new Date(2026, 5, 5), eur: 103100 },
    { t: new Date(2026, 5, 6), eur: 102700 },
    { t: new Date(2026, 5, 7), eur: 104500 },
    { t: new Date(2026, 5, 8), eur: 105800 },
    { t: new Date(2026, 5, 9), eur: 105200 },
    { t: new Date(2026, 5, 10), eur: 107100 },
    { t: new Date(2026, 5, 11), eur: 108400 },
    { t: new Date(2026, 5, 12), eur: 107900 },
    { t: new Date(2026, 5, 13), eur: 109600 },
    { t: new Date(2026, 5, 14), eur: 111200 },
    { t: new Date(2026, 5, 15), eur: 110700 },
    { t: new Date(2026, 5, 16), eur: 113400 },
    { t: new Date(2026, 5, 17), eur: 115200 },
    { t: new Date(2026, 5, 18), eur: 114600 },
    { t: new Date(2026, 5, 19), eur: 117800 },
    { t: new Date(2026, 5, 20), eur: 120110 },
    { t: new Date(2026, 5, 21), eur: 122450 },
];

// #4514: one repository URL for the header GitHub icon and the hero's "View the code".
const REPO_URL = "https://github.com/Autonomous-Asset-Management-Agents/autonomous_";

// #4514 (owner 11.10.): Block 1 example panel. Illustrative constants, deliberately not
// coupled to the live defaults.
const EXAMPLE_RULES = { maxPositions: 10, dailyVolTargetPct: 1.5, minHoldDays: 20 };
const EXAMPLE_WEIGHTS: Array<[string, number]> = [
    ["DrawdownGuard", 0.6],
    ["Regime", 0.5],
    ["Momentum", 0.45],
    ["VIX", 0.45],
    ["Fundamentals", 0.25],
    ["News", 0.2],
    ["Specialist", 0.15],
    ["LSTM", 0.15],
];

// #4514: hero console KPI tiles — states, no return or P/L figures.
const HERO_KPIS = { openPositions: 7, agentsActive: 14, agentsVoting: 8, cashEur: 49200 };

// Hero terminal — illustrative log of one motion through the real pipeline.
// Names + thresholds map to the live codebase: Stock Specialists → Coordinator
// → Senate (12 senators) → Compliance Guardian → cuFOLIO → Order Executor.
const SEED: ChatMessage[] = [
    { role: "user", content: "walk me through one decision" },
    { role: "agent", name: "system", content: "paper-trading · full S&P 500 universe · on-prem" },
    { role: "agent", name: "system", content: "Watchlist signal models spawned · one per symbol" },
    { role: "agent", name: "analyst", content: "AAPL · public sources merged · report ready" },
    { role: "agent", name: "coord", content: "specialist signal clears the sector gate" },
    { role: "agent", name: "coord", content: "motion opened · long AAPL · ensemble in session" },
    { role: "agent", name: "comply", content: "within mandate · no veto" },
    { role: "agent", name: "bear", content: "downside contained · no veto" },
    { role: "agent", name: "quant", content: "risk within budget · no veto" },
    { role: "agent", name: "alloc", content: "still fits the book" },
    { role: "agent", name: "bull", content: "case structurally credible" },
    { role: "agent", name: "macro", content: "regime neutral" },
    { role: "agent", name: "tech", content: "trend intact · no breakout fail" },
    { role: "agent", name: "moment", content: "positive drift · volume confirms" },
    { role: "agent", name: "rebal", content: "weight slot available" },
    { role: "agent", name: "coord", content: "weighted consensus over buy threshold · approved" },
    { role: "user", content: "who can stop this?" },
    { role: "agent", name: "system", content: "three hard-veto roles: bear · quant · compliance" },
    { role: "agent", name: "optim", content: "sizing inside caps · concentration ok" },
    { role: "agent", name: "exec", content: "order routed · paper account · partial fills tracked" },
    { role: "user", content: "what is logged exactly?" },
    { role: "agent", name: "audit", content: "every classifier · score · weight · reasoning sealed" },
    { role: "agent", name: "audit", content: "gatekeeper decision · final action · regulator-ready" },
    { role: "agent", name: "audit", content: "tamper-evident chain · prev-record linked · append-only" },
    { role: "user", content: "and if something breaks?" },
    { role: "agent", name: "system", content: "kill-switch trips · mass-cancel · halted" },
    { role: "agent", name: "system", content: "manual reset required · no auto-resume" },
    { role: "user", content: "average latency?" },
    { role: "agent", name: "system", content: "motion → fill · sub-second · all checks synchronous" },
    { role: "agent", name: "coord", content: "post-trade · slippage tracked · attribution queued" },
    { role: "agent", name: "coord", content: "monitoring · next cycle on schedule" },
];

const MIN_MSGS = 5;
const MAX_MSGS = SEED.length;

// Block 2 vote ledger — names, votes, reasons, and per-seat static weights.
const VOTES: Array<{ name: string; vote: "yes" | "no"; reason: string; conf: number }> = [
    { name: "iota", vote: "yes", reason: "iron dome · compliance ok", conf: 0.80 },
    { name: "alpha", vote: "yes", reason: "bear guard · drawdown headroom", conf: 0.75 },
    { name: "delta", vote: "yes", reason: "quant risk · within R02 cap", conf: 0.65 },
    { name: "gamma", vote: "yes", reason: "portfolio architect · in budget", conf: 0.60 },
    { name: "epsilon", vote: "yes", reason: "macro oracle · regime neutral", conf: 0.55 },
    { name: "beta", vote: "yes", reason: "bull advocate · score 0.66", conf: 0.50 },
    { name: "theta", vote: "yes", reason: "rebalancer · weight slot open", conf: 0.50 },
    { name: "zeta", vote: "yes", reason: "technical · trend intact", conf: 0.45 },
    { name: "eta", vote: "yes", reason: "momentum · positive drift", conf: 0.45 },
    { name: "kappa", vote: "no", reason: "catalyst · no edge filing", conf: 0.40 },
    { name: "lambda", vote: "yes", reason: "setup · base intact", conf: 0.40 },
    { name: "mu", vote: "no", reason: "contrarian · sentiment hot", conf: 0.35 },
];

// Reference: paper-trading bringup, Apr 15 2026 13:00 UTC
// (project_aaagents_session_2026_04_15 — first bringup of the live engine on Alpaca paper).
const HALT_REF = Date.UTC(2026, 3, 15, 13, 0, 0);

function pad(n: number): string {
    return String(n).padStart(2, "0");
}

function formatPrompt(m: ChatMessage): string {
    if (m.role === "user") return "user  >";
    return ((m.name || "agent").padEnd(6).slice(0, 7)) + " >";
}

export default function LandingViewE() {
    const navigate = useNavigate();
    // Language toggle in the nav (tower launch) — i18n only; the landing copy
    // itself stays English, the choice carries over to localized pages.
    const { t, i18n } = useTranslation();
    // Pricing copy comes from the catalogue too (i18n step 1, part 2).
    const pricing = getPricingData(t);
    // #4469 (ADR-026): hero headline A/B test, assigned before the first paint. Control unless the
    // build carries a GrowthBook payload for "hero_positioning".
    const heroCopy =
        useExperiment("hero_positioning") === "positioning" ? "landing.hero.exp.positioning" : "landing.hero";
    const [pricingMode, setPricingMode] = useState("Junior");
    const rootRef = useRef<HTMLDivElement | null>(null);
    const scrubRef = useRef<HTMLDivElement | null>(null);
    const chatLogRef = useRef<HTMLDivElement | null>(null);
    const lenisRef = useRef<any>(null);

    // Risk banner dismissal
    const [bannerOpen, setBannerOpen] = useState<boolean>(() => {
        try {
            return localStorage.getItem("aaa_risk_banner_dismissed") !== "1";
        } catch {
            return true;
        }
    });

    // Hero terminal chat — progressive reveal driven by scroll progress + user
    // input persisted as additional messages.
    const [shownCount, setShownCount] = useState<number>(MIN_MSGS);
    const [userMessages, setUserMessages] = useState<ChatMessage[]>([]);
    const [chatInput, setChatInput] = useState<string>("");
    const [scrollProgress, setScrollProgress] = useState<number>(0);
    const [terminalScale, setTerminalScale] = useState<number>(1);
    const [isMobileOrTablet, setIsMobileOrTablet] = useState<boolean>(false);
    const terminalRef = useRef<HTMLDivElement | null>(null);

    // Compliance audit-feed counter — seeded from elapsed seconds since
    // bringup so the headline number looks plausible and ticks upwards live.
    const [auditCount, setAuditCount] = useState<number>(() => {
        const elapsed = Math.max(0, (Date.now() - HALT_REF) / 1000);
        return 142_000 + Math.floor(elapsed * 0.42);
    });

    // Block 3 auto-halt counter (live, since 2026-04-15 13:00 UTC)
    const [halt, setHalt] = useState<{ d: number; h: number; m: number; s: number }>(() => {
        const diff = Math.max(0, (Date.now() - HALT_REF) / 1000);
        const d = Math.floor(diff / 86400);
        const r1 = diff - d * 86400;
        const h = Math.floor(r1 / 3600);
        const r2 = r1 - h * 3600;
        const m = Math.floor(r2 / 60);
        const s = Math.floor(r2 - m * 60);
        return { d, h, m, s };
    });

    // Scroll-scrub opacity/scale maps
    const logoOpacity = Math.max(0, 1 - scrollProgress / 0.35);
    const logoScale = 1 - scrollProgress * 0.15;
    const appOpacity = Math.min(1, Math.max(0, (scrollProgress - 0.15) / 0.25));
    const greetingOpacity = Math.min(1, Math.max(0, (scrollProgress - 0.25) / 0.20));
    const equityOpacity = Math.min(1, Math.max(0, (scrollProgress - 0.35) / 0.25));
    const chartProgress = Math.min(1, Math.max(0, (scrollProgress - 0.45) / 0.40));
    const kpiOpacity = Math.min(1, Math.max(0, (scrollProgress - 0.70) / 0.25));
    const disclosureOpacity = Math.min(1, Math.max(0, (scrollProgress - 0.80) / 0.20));

    // Risk banner — restored dismissed state from localStorage via useState initializer

    const dismissBanner = () => {
        setBannerOpen(false);
        try {
            localStorage.setItem("aaa_risk_banner_dismissed", "1");
        } catch {
            /* ignore */
        }
    };

    // #4467 (ADR-026): one cookieless pageview per landing visit; no-op without build variables.
    useEffect(() => {
        trackPageview();
    }, []);

    // Hero scroll-scrub — drives --lb-hp custom property 0→1 and progressively
    // reveals more of the SEED chat log as the user scrolls past the hero pin.
    useEffect(() => {
        const scrub = scrubRef.current;
        const root = rootRef.current;
        if (!scrub || !root) return;
        let ticking = false;
        const update = () => {
            const rect = scrub.getBoundingClientRect();
            const total = scrub.offsetHeight - window.innerHeight;
            let raw = 0;
            const isMobileOrTablet = window.innerWidth <= 900;
            if (isMobileOrTablet) {
                raw = Math.min(Math.max(window.scrollY / 250, 0), 1);
            } else if (total > 0) {
                raw = Math.min(Math.max(-rect.top / total, 0), 1);
            }
            const p = Math.min(raw / 0.7, 1);
            root.style.setProperty("--lb-hp", p.toFixed(4));
            setScrollProgress(raw);

            // Progressive chat reveal — same denominator (full scrub),
            // mapped 0..1 to MIN_MSGS..MAX_MSGS.
            const target = Math.round(MIN_MSGS + (MAX_MSGS - MIN_MSGS) * raw);
            setShownCount((prev) => (prev !== target ? target : prev));

            ticking = false;
        };
        const onScroll = () => {
            if (!ticking) {
                requestAnimationFrame(update);
                ticking = true;
            }
        };
        window.addEventListener("scroll", onScroll, { passive: true });
        window.addEventListener("resize", update);
        update();
        return () => {
            window.removeEventListener("scroll", onScroll);
            window.removeEventListener("resize", update);
        };
    }, []);

    // Dynamically scale the console dashboard on mobile and tablet to look exactly like desktop
    useEffect(() => {
        const el = terminalRef.current;
        if (!el) return;
        const updateScale = () => {
            const currentWidth = el.offsetWidth;
            const mobileOrTablet = window.innerWidth <= 900;
            setIsMobileOrTablet(mobileOrTablet);
            if (mobileOrTablet) {
                setTerminalScale(currentWidth / 800);
            } else {
                setTerminalScale(1);
            }
        };
        const observer = new ResizeObserver(() => {
            updateScale();
        });
        observer.observe(el);
        updateScale();
        window.addEventListener("resize", updateScale);
        return () => {
            observer.disconnect();
            window.removeEventListener("resize", updateScale);
        };
    }, []);

    // Auto-scroll chat log to bottom whenever the visible chat changes.
    useEffect(() => {
        const el = chatLogRef.current;
        if (el) el.scrollTop = el.scrollHeight;
    }, [shownCount, userMessages]);

    // Lenis smooth-scroll init.
    useEffect(() => {
        const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
        if (reduced) return;
        let raf = 0;
        let lenis: any = null;
        try {
            lenis = new Lenis({
                duration: 1.15,
                easing: (t: number) => Math.min(1, 1.001 - Math.pow(2, -10 * t)),
                smoothWheel: true,
                wheelMultiplier: 1.0,
                touchMultiplier: 1.4,
            });
        } catch {
            // Lenis import failed (e.g. package not yet installed) — fall back
            // to native scrolling silently.
            return;
        }
        const tick = (time: number) => {
            lenis.raf(time);
            raf = requestAnimationFrame(tick);
        };
        raf = requestAnimationFrame(tick);
        lenisRef.current = lenis;
        (window as any).__lenis = lenis;
        return () => {
            cancelAnimationFrame(raf);
            try {
                lenis.destroy?.();
            } catch {
                /* ignore */
            }
            lenisRef.current = null;
            try {
                delete (window as any).__lenis;
            } catch {
                /* ignore */
            }
        };
    }, []);

    // Deep-link scroll: /preview#block-editions (the Live Demo's "Download" CTA)
    // must actually land on the Editions section. The native hash jump is a no-op
    // here — the section mounts async and Lenis owns the scroll — so resolve it
    // ourselves, retrying briefly until the target section is in the DOM. Runs
    // once on mount after Lenis has had a tick to initialise.
    useEffect(() => {
        const hash = window.location.hash;
        if (!hash || hash === "#") return;
        let tries = 0;
        let timer: ReturnType<typeof setTimeout> | undefined;
        const attempt = () => {
            if (scrollToHash(hash, lenisRef.current)) return;
            if (tries++ < 20) timer = setTimeout(attempt, 100);
        };
        timer = setTimeout(attempt, 80);
        return () => {
            if (timer) clearTimeout(timer);
        };
    }, []);

    // Magnetic buttons — wraps children in .mag-inner and translates both the
    // outer button and the inner label towards the cursor when within range.
    useEffect(() => {
        const root = rootRef.current;
        if (!root) return;
        const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
        const isFinePointer = window.matchMedia("(hover:hover) and (pointer:fine)").matches;
        if (!isFinePointer || reduced) return;

        const cleanups: Array<() => void> = [];
        const targets = root.querySelectorAll<HTMLElement>(
            ".lb-cta-primary, .lb-cta-ghost, .blk-cta, .lb-send",
        );
        const strength = 0.35;
        const range = 60;

        targets.forEach((el) => {
            // Wrap children in .mag-inner once
            let inner = el.querySelector<HTMLElement>(":scope > .mag-inner");
            if (!inner) {
                inner = document.createElement("span");
                inner.className = "mag-inner";
                while (el.firstChild) inner.appendChild(el.firstChild);
                el.appendChild(inner);
            }
            el.classList.add("mag");

            const onMove = (e: MouseEvent) => {
                const r = el.getBoundingClientRect();
                const cx = r.left + r.width / 2;
                const cy = r.top + r.height / 2;
                const ddx = e.clientX - cx;
                const ddy = e.clientY - cy;
                const d = Math.hypot(ddx, ddy);
                if (d > Math.max(r.width, r.height) / 2 + range) {
                    el.style.transform = "";
                    if (inner) inner.style.transform = "";
                    return;
                }
                el.style.transform = `translate3d(${ddx * strength * 0.45}px, ${ddy * strength * 0.45}px, 0)`;
                if (inner)
                    inner.style.transform = `translate3d(${ddx * strength * 0.7}px, ${ddy * strength * 0.7}px, 0)`;
            };
            const onEnter = () => el.classList.add("is-mag-active");
            const onLeave = () => {
                el.classList.remove("is-mag-active");
                el.style.transform = "";
                if (inner) inner.style.transform = "";
            };
            el.addEventListener("mousemove", onMove);
            el.addEventListener("mouseenter", onEnter);
            el.addEventListener("mouseleave", onLeave);
            cleanups.push(() => {
                el.removeEventListener("mousemove", onMove);
                el.removeEventListener("mouseenter", onEnter);
                el.removeEventListener("mouseleave", onLeave);
            });
        });

        return () => {
            cleanups.forEach((c) => c());
        };
    }, []);

    // Word-by-word reveal — runs once on mount. Walks every text node inside
    // selected headlines and wraps each word in <span class="w"><span class="w-inner">…</span></span>.
    useEffect(() => {
        const root = rootRef.current;
        if (!root) return;

        function splitWords(el: HTMLElement) {
            if ((el as any).__split) return;
            (el as any).__split = true;
            const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT, null);
            const texts: Text[] = [];
            let n: Node | null;

            while ((n = walker.nextNode())) texts.push(n as Text);
            texts.forEach((node) => {
                const parent = node.parentNode;
                if (!parent) return;
                const frag = document.createDocumentFragment();
                const parts = (node.nodeValue || "").split(/(\s+)/);
                parts.forEach((p) => {
                    if (p === "") return;
                    if (/^\s+$/.test(p)) {
                        frag.appendChild(document.createTextNode(p));
                    } else {
                        const w = document.createElement("span");
                        w.className = "w";
                        const inner = document.createElement("span");
                        inner.className = "w-inner";
                        inner.textContent = p;
                        w.appendChild(inner);
                        frag.appendChild(w);
                    }
                });
                parent.replaceChild(frag, node);
            });
            el.querySelectorAll<HTMLElement>(".w > .w-inner").forEach((wi, i) => {
                wi.style.transitionDelay = i * 0.045 + "s";
            });
        }

        const HEADLINE_SEL =
            ".lb-hero h1, .lb-hero-h2, .blk h2, .lb-section.minimal h2, .lb-section h2.lb-reveal";
        root.querySelectorAll<HTMLElement>(HEADLINE_SEL).forEach((h) => {
            if (!h.hasAttribute("data-words")) h.setAttribute("data-words", "");
        });
        root.querySelectorAll<HTMLElement>("[data-words]").forEach(splitWords);

        // IntersectionObserver — flips .is-revealed / .is-in
        const io = new IntersectionObserver(
            (entries) => {
                entries.forEach((e) => {
                    if (e.isIntersecting) {
                        e.target.classList.add("is-revealed", "is-in");
                        io.unobserve(e.target);
                    }
                });
            },
            { threshold: 0.05, rootMargin: "0px 0px -8% 0px" },
        );
        root.querySelectorAll<HTMLElement>("[data-words], .lb-img-scale").forEach((el) => io.observe(el));

        // Force-reveal anything already in viewport on load
        requestAnimationFrame(() => {
            root.querySelectorAll<HTMLElement>("[data-words]:not(.is-revealed)").forEach((el) => {
                const r = el.getBoundingClientRect();
                if (r.top < window.innerHeight && r.bottom > 0) {
                    el.classList.add("is-revealed");
                }
            });
        });

        // Auto-tag skyline images with .lb-img-scale
        root
            .querySelectorAll<HTMLElement>(".lb-skyline-img, .lb-skyline-divider, [data-img-scale]")
            .forEach((el) => {
                el.classList.add("lb-img-scale");
                if (!el.classList.contains("is-in")) io.observe(el);
            });

        // tr-body stagger reveal (separate observer used by source)
        const bodies = root.querySelectorAll<HTMLElement>(".tr-body");
        let io2: IntersectionObserver | null = null;
        if (bodies.length && "IntersectionObserver" in window) {
            io2 = new IntersectionObserver(
                (entries) => {
                    entries.forEach((e) => {
                        if (e.isIntersecting) {
                            (e.target as HTMLElement).classList.add("is-in");
                            io2!.unobserve(e.target);
                        }
                    });
                },
                { threshold: 0.25, rootMargin: "0px 0px -10% 0px" },
            );
            bodies.forEach((b) => io2!.observe(b));
        }

        // lb-reveal handler
        const reveals = root.querySelectorAll<HTMLElement>(".lb-reveal");
        const ioR = new IntersectionObserver(
            (entries) => {
                entries.forEach((e) => {
                    if (e.isIntersecting) {
                        e.target.classList.add("lb-in-view");
                        ioR.unobserve(e.target);
                    }
                });
            },
            { threshold: 0.08, rootMargin: "0px 0px -10% 0px" },
        );
        reveals.forEach((el) => ioR.observe(el));

        return () => {
            io.disconnect();
            if (io2) io2.disconnect();
            ioR.disconnect();
        };
    }, []);

    // Generic scroll-reveal for tr-body .pt / tr-cta-row / tr-stage figures /
    // tr-ledger / tr-safety / kpi cards / faq items.
    // Mirrors the second IIFE in the source HTML — injects a small <style>
    // block with .scr-rv / .scr-rv-soft / .scr-rv-l / .scr-rv-r / .scr-words
    // utility classes, tags eligible elements, then observes them.
    useEffect(() => {
        const root = rootRef.current;
        if (!root) return;
        const reduceMo = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
        if (reduceMo) return;

        const css = `
            .landing-d-root .scr-rv{
                opacity:0; transform:translateY(28px);
                transition: opacity .9s cubic-bezier(.2,.7,.2,1), transform 1.0s cubic-bezier(.2,.7,.2,1);
                will-change: opacity, transform;
            }
            .landing-d-root .scr-rv.scr-on{ opacity:1; transform:translateY(0); }
            .landing-d-root .scr-rv-soft{
                opacity:0; transform:translateY(14px) scale(.985);
                transition: opacity 1.0s cubic-bezier(.2,.7,.2,1), transform 1.1s cubic-bezier(.2,.7,.2,1);
            }
            .landing-d-root .scr-rv-soft.scr-on{ opacity:1; transform:translateY(0) scale(1); }
            .landing-d-root .scr-rv-l{
                opacity:0; transform:translateX(-32px);
                transition: opacity .9s cubic-bezier(.2,.7,.2,1), transform 1.0s cubic-bezier(.2,.7,.2,1);
            }
            .landing-d-root .scr-rv-l.scr-on{ opacity:1; transform:translateX(0); }
            .landing-d-root .scr-rv-r{
                opacity:0; transform:translateX(32px);
                transition: opacity .9s cubic-bezier(.2,.7,.2,1), transform 1.0s cubic-bezier(.2,.7,.2,1);
            }
            .landing-d-root .scr-rv-r.scr-on{ opacity:1; transform:translateX(0); }
            .landing-d-root .scr-words .scr-w{
                display:inline-block; opacity:0;
                transform: translateY(40px);
                transition: opacity .9s cubic-bezier(.2,.7,.2,1), transform 1.0s cubic-bezier(.2,.7,.2,1);
                will-change: transform, opacity;
            }
            .landing-d-root .scr-words.scr-on .scr-w{ opacity:1; transform:translateY(0); }
        `;
        const styleTag = document.createElement("style");
        styleTag.textContent = css;
        document.head.appendChild(styleTag);

        const TARGETS: Array<{ sel: string; type: string }> = [
            { sel: ".landing-d-root .tr-body .pt", type: "scr-rv" },
            { sel: ".landing-d-root .tr-cta-row", type: "scr-rv" },
            { sel: ".landing-d-root .tr-stage .figure", type: "scr-rv-soft" },
            { sel: ".landing-d-root .tr-stage > svg", type: "scr-rv-soft" },
            { sel: ".landing-d-root .tr-stage .range", type: "scr-rv" },
            { sel: ".landing-d-root .tr-ledger", type: "scr-rv-soft" },
            { sel: ".landing-d-root .tr-safety", type: "scr-rv-soft" },
            { sel: ".landing-d-root .tr-safety .layer", type: "scr-rv" },
            { sel: ".landing-d-root .tr-safety .threat", type: "scr-rv" },
            { sel: ".landing-d-root .pf-stat", type: "scr-rv" },
            { sel: ".landing-d-root .lb-faq-item", type: "scr-rv" },
            { sel: ".landing-d-root .lb-section h2:not(.is-revealed):not([data-words])", type: "scr-rv" },
        ];

        const tagged = new Set<HTMLElement>();
        TARGETS.forEach(({ sel, type }) => {
            document.querySelectorAll<HTMLElement>(sel).forEach((el) => {
                if (tagged.has(el)) return;
                if (el.classList.contains("lb-reveal") || el.classList.contains("is-revealed")) return;
                el.classList.add(type);
                tagged.add(el);
            });
        });

        // Word-by-word for .tr-headline (excluding hero h1 which already animates).
        // Source uses an innerHTML round-trip; here we walk children and split
        // text-node children directly so we keep <br> nodes intact and never
        // touch innerHTML — same visual result, no XSS surface.
        document.querySelectorAll<HTMLElement>(".landing-d-root .tr-headline").forEach((h) => {
            if (h.matches(".is-revealed") || h.hasAttribute("data-words")) return;
            const newKids: Node[] = [];
            Array.from(h.childNodes).forEach((node) => {
                if (node.nodeType === Node.TEXT_NODE) {
                    const text = (node.nodeValue || "");
                    text.split(/(\s+)/).forEach((token) => {
                        if (token === "") return;
                        if (!token.trim()) {
                            newKids.push(document.createTextNode(token));
                        } else {
                            const span = document.createElement("span");
                            span.className = "scr-w";
                            span.textContent = token;
                            newKids.push(span);
                        }
                    });
                } else {
                    // preserve <br> and other element children as-is
                    newKids.push(node);
                }
            });
            // Replace children atomically.
            while (h.firstChild) h.removeChild(h.firstChild);
            newKids.forEach((k) => h.appendChild(k));
            h.classList.add("scr-words");
            h.querySelectorAll<HTMLElement>(".scr-w").forEach((w, i) => {
                w.style.transitionDelay = i * 0.06 + "s";
            });
        });

        const ioRv = new IntersectionObserver(
            (entries) => {
                entries.forEach((e) => {
                    if (!e.isIntersecting) return;
                    const el = e.target as HTMLElement;
                    const group = el.parentElement;
                    if (group) {
                        const sibs = Array.from(group.children).filter((c) =>
                            c.classList.contains("scr-rv") ||
                            c.classList.contains("scr-rv-soft") ||
                            c.classList.contains("scr-rv-l") ||
                            c.classList.contains("scr-rv-r"),
                        );
                        const idx = Math.max(0, sibs.indexOf(el));
                        el.style.transitionDelay = idx * 0.08 + "s";
                    }
                    el.classList.add("scr-on");
                    ioRv.unobserve(el);
                });
            },
            { rootMargin: "0px 0px -8% 0px", threshold: 0.12 },
        );

        document
            .querySelectorAll<HTMLElement>(
                ".landing-d-root .scr-rv, .landing-d-root .scr-rv-soft, .landing-d-root .scr-rv-l, .landing-d-root .scr-rv-r, .landing-d-root .scr-words",
            )
            .forEach((el) => ioRv.observe(el));

        // Force-reveal items already in viewport on load
        requestAnimationFrame(() => {
            document
                .querySelectorAll<HTMLElement>(
                    ".landing-d-root .scr-rv, .landing-d-root .scr-rv-soft, .landing-d-root .scr-rv-l, .landing-d-root .scr-rv-r, .landing-d-root .scr-words",
                )
                .forEach((el) => {
                    if (el.classList.contains("scr-on")) return;
                    const r = el.getBoundingClientRect();
                    if (r.top < window.innerHeight * 0.95 && r.bottom > 0) {
                        el.classList.add("scr-on");
                    }
                });
        });

        return () => {
            ioRv.disconnect();
            styleTag.remove();
        };
    }, []);

    // Auto-halt counter — ticks every second.
    useEffect(() => {
        const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
        if (reduced) return;
        const tick = () => {
            const diff = Math.max(0, (Date.now() - HALT_REF) / 1000);
            const d = Math.floor(diff / 86400);
            const r1 = diff - d * 86400;
            const h = Math.floor(r1 / 3600);
            const r2 = r1 - h * 3600;
            const m = Math.floor(r2 / 60);
            const s = Math.floor(r2 - m * 60);
            setHalt({ d, h, m, s });
        };
        const id = window.setInterval(tick, 1000);
        return () => window.clearInterval(id);
    }, []);

    // Audit-feed counter — slow incrementing tick (~2-3 records/sec)
    useEffect(() => {
        const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
        if (reduced) return;
        const id = window.setInterval(() => {
            setAuditCount((c) => c + 1 + Math.floor(Math.random() * 3));
        }, 1500);
        return () => window.clearInterval(id);
    }, []);

    // Adaptive top-banner: bar bg perfectly matches whatever section /
    // gradient strip is currently scrolled under it; text color is the
    // contrast-opposite. During the .tr-fade gradient transitions
    // between dark and light blocks, we sample the probe's vertical
    // position within the fade strip and interpolate both colors so
    // the bar visually blends with the gradient instead of snapping.
    // RAF-throttled for smooth scroll performance.
    useEffect(() => {
        const banner = document.querySelector<HTMLElement>(".lb-risk-banner");
        if (!banner) return;
        const link = banner.querySelector<HTMLElement>(".lb-ticker-link");
        const lerp = (a: number, b: number, t: number) =>
            Math.round(a + (b - a) * t);
        const apply = (bg: number[], fg: number[]) => {
            banner.style.backgroundColor = `rgb(${bg[0]}, ${bg[1]}, ${bg[2]})`;
            if (link) link.style.color = `rgb(${fg[0]}, ${fg[1]}, ${fg[2]})`;
        };
        const update = () => {
            const probeY = banner.getBoundingClientRect().bottom + 1;
            // Iterate fade + section elements by bounding rect — `elementFromPoint`
            // skips `.tr-fade` because it has `pointer-events: none`.
            const candidates = document.querySelectorAll<HTMLElement>(
                ".tr-fade, .tr-section, .lb-hero, .lb-footer",
            );
            let target: HTMLElement | null = null;
            for (const el of Array.from(candidates)) {
                const r = el.getBoundingClientRect();
                if (r.top <= probeY && r.bottom > probeY) {
                    if (el.classList.contains("tr-fade")) { target = el; break; }
                    if (!target) target = el;
                }
            }
            if (!target) return;
            // Bar bg endpoints match section bg EXACTLY: pure white #fff
            // (rgb 255,255,255) and pure black #000 (rgb 0,0,0). Text uses
            // a softer near-black (#0a0a0a = rgb 10,10,10) on light bg so
            // it doesn't read as harsh, but the bar bg itself is pure to
            // avoid a visible seam where the bar meets the section.
            const WHITE_BG = [255, 255, 255];
            const BLACK_BG = [0, 0, 0];
            const DARK_TEXT = [10, 10, 10];
            const LIGHT_TEXT = [255, 255, 255];
            if (target.classList.contains("tr-fade")) {
                // Interpolate through the gradient strip
                const r = target.getBoundingClientRect();
                const t = Math.max(0, Math.min(1, (probeY - r.top) / Math.max(1, r.height)));
                const lightToDark = target.classList.contains("tr-fade-light-to-dark");
                const bgStart = lightToDark ? WHITE_BG : BLACK_BG;
                const bgEnd = lightToDark ? BLACK_BG : WHITE_BG;
                const fgStart = lightToDark ? DARK_TEXT : LIGHT_TEXT;
                const fgEnd = lightToDark ? LIGHT_TEXT : DARK_TEXT;
                const bg = [lerp(bgStart[0], bgEnd[0], t), lerp(bgStart[1], bgEnd[1], t), lerp(bgStart[2], bgEnd[2], t)];
                const fg = [lerp(fgStart[0], fgEnd[0], t), lerp(fgStart[1], fgEnd[1], t), lerp(fgStart[2], fgEnd[2], t)];
                apply(bg, fg);
            } else {
                // Solid section — match its computed bg and contrast text.
                const bgStr = getComputedStyle(target).backgroundColor;
                const isDark =
                    target.classList.contains("dark") ||
                    target.classList.contains("lb-footer") ||
                    bgStr === "rgb(0, 0, 0)";
                apply(isDark ? BLACK_BG : WHITE_BG, isDark ? LIGHT_TEXT : DARK_TEXT);
            }
        };
        let rafId = 0;
        const onScroll = () => {
            if (rafId) return;
            rafId = requestAnimationFrame(() => { update(); rafId = 0; });
        };
        update();
        window.addEventListener("scroll", onScroll, { passive: true });
        return () => {
            window.removeEventListener("scroll", onScroll);
            if (rafId) cancelAnimationFrame(rafId);
        };
    }, []);

    // Click delegation on the root — #4467: conversions carry data-cta and are counted here.
    // (#4514 removed the subpage overlays and with them the data-open / data-close branch.)
    const handleRootClick = (e: React.MouseEvent<HTMLDivElement>) => {
        const t = e.target as Element | null;
        if (!t) return;
        const cta = (t.closest("[data-cta]") as HTMLElement | null)?.getAttribute("data-cta");
        if (cta === "download") trackDownload(platformDownload().os);
        else if (cta) trackCta(cta);
    };

    // Build current chat log from SEED slice + user messages
    const chatLog: ChatMessage[] = SEED.slice(0, shownCount).concat(userMessages);
    const visibleLog = chatLog.slice(-33);

    const handleChatSubmit = (e: FormEvent) => {
        e.preventDefault();
        const text = chatInput.trim();
        if (!text) return;
        const userMsg: ChatMessage = { role: "user", content: text };
        setUserMessages((prev) => [...prev, userMsg]);
        setChatInput("");
        window.setTimeout(() => {
            setUserMessages((prev) => [
                ...prev,
                { role: "agent", name: "coord", content: "noted · routing to analyst pool" },
            ]);
        }, 450);
    };

    return (
        <div className="landing-d-root" id="lbRoot" ref={rootRef} onClick={handleRootClick}>
            {/* Top OSS banner — sticky ticker */}
            <div
                className="lb-risk-banner"
                id="lbRiskBanner"
                style={{
                    position: "sticky",
                    top: 0,
                    zIndex: 1000,
                    overflow: "hidden",
                    borderBottom: "1px solid #ececec",
                    background: "#fff",
                    fontSize: 13,
                }}
            >
                {/* Go-live (tower launch): the ticker announces the limited tower
                    first edition and deep-links to /appliance. */}
                <a
                    href="/appliance"
                    onClick={(e) => {
                        e.preventDefault();
                        navigate("/appliance");
                    }}
                    className="lb-ticker-link"
                >
                    <div className="lb-ticker-track">
                        {/* Duplicate the text for seamless loop */}
                        {[0, 1].map((i) => (
                            <span key={i} className="lb-ticker-item" aria-hidden={i === 1 ? "true" : undefined}>
                                {t("landing.ticker")}
                                &nbsp;&nbsp;&nbsp;·&nbsp;&nbsp;&nbsp;
                                {t("landing.ticker")}
                                &nbsp;&nbsp;&nbsp;·&nbsp;&nbsp;&nbsp;
                            </span>
                        ))}
                    </div>
                </a>
            </div>

            {/* Nav */}
            <nav className="lb-nav">
                <div className="lb-nav-logo-box">
                    <img src="/aaagents_logo_linkedin.png" alt="AAAgents Logo" style={{ height: "32px", width: "auto" }} />
                </div>
                <div className="lb-nav-social">
                    {/* Removed legacy CONSOLE link */}
                    <a
                        href="https://www.linkedin.com/company/aaa-autonomous-asset-management-agents/posts/?feedView=all"
                        target="_blank"
                        rel="noopener"
                        className="lb-nav-social-link"
                        aria-label={t("landing.nav.linkedinAria")}
                        title={t("landing.nav.linkedinAria")}
                    >
                        {/* LinkedIn icon */}
                        <svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
                            <path d="M20.447 20.452h-3.554v-5.569c0-1.328-.027-3.037-1.852-3.037-1.853 0-2.136 1.445-2.136 2.939v5.667H9.351V9h3.414v1.561h.046c.477-.9 1.637-1.85 3.37-1.85 3.601 0 4.267 2.37 4.267 5.455v6.286zM5.337 7.433a2.062 2.062 0 01-2.063-2.065 2.064 2.064 0 112.063 2.065zm1.782 13.019H3.555V9h3.564v11.452zM22.225 0H1.771C.792 0 0 .774 0 1.729v20.542C0 23.227.792 24 1.771 24h20.451C23.2 24 24 23.227 24 22.271V1.729C24 .774 23.2 0 22.222 0h.003z"/>
                        </svg>
                    </a>
                    <a
                        href={REPO_URL}
                        target="_blank"
                        rel="noopener"
                        className="lb-nav-social-link"
                        data-cta="github"
                        aria-label={t("landing.nav.githubAria")}
                        title={t("landing.nav.githubAria")}
                    >
                        {/* GitHub icon */}
                        <svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
                            <path d="M12 .5C5.65.5.5 5.65.5 12c0 5.08 3.29 9.39 7.86 10.91.58.1.79-.25.79-.56 0-.28-.01-1.02-.02-2-3.2.7-3.87-1.54-3.87-1.54-.52-1.33-1.28-1.69-1.28-1.69-1.05-.72.08-.7.08-.7 1.16.08 1.77 1.19 1.77 1.19 1.03 1.77 2.7 1.26 3.36.96.1-.75.4-1.26.73-1.55-2.55-.29-5.24-1.28-5.24-5.69 0-1.26.45-2.28 1.19-3.09-.12-.29-.52-1.46.11-3.05 0 0 .97-.31 3.18 1.18.92-.26 1.91-.39 2.9-.39.99 0 1.98.13 2.9.39 2.21-1.49 3.18-1.18 3.18-1.18.63 1.59.23 2.76.11 3.05.74.81 1.19 1.83 1.19 3.09 0 4.42-2.69 5.39-5.25 5.68.41.36.78 1.06.78 2.13 0 1.54-.01 2.78-.01 3.16 0 .31.21.67.8.56C20.22 21.39 23.5 17.08 23.5 12 23.5 5.65 18.35.5 12 .5z" />
                        </svg>
                    </a>

                    {/* Language toggle — gated OFF until the full main-page translation
                        ships (operator 14.08.: no mixed-language experience). */}
                    {GERMAN_ENABLED && (
                    <button
                        type="button"
                        className="lb-nav-social-link"
                        aria-label={t(i18n.resolvedLanguage === "de" ? "landing.nav.toEnglish" : "landing.nav.toGerman")}
                        title={t(i18n.resolvedLanguage === "de" ? "landing.nav.toEnglish" : "landing.nav.toGerman")}
                        style={{ background: "none", border: "none", cursor: "pointer" }}
                        onClick={(e) => {
                            e.stopPropagation();
                            i18n.changeLanguage(i18n.resolvedLanguage === "de" ? "en" : "de");
                        }}
                    >
                        {i18n.resolvedLanguage === "de" ? (
                            // Union Jack, monochrome (simplified)
                            <svg width="22" height="15" viewBox="0 0 20 14" aria-hidden="true">
                                <rect width="20" height="14" rx="2" fill="#6f6f6f" />
                                <path d="M0 0 L20 14 M20 0 L0 14" stroke="#e5e5e5" strokeWidth="2.6" />
                                <path d="M10 0 V14 M0 7 H20" stroke="#e5e5e5" strokeWidth="4" />
                                <path d="M10 0 V14 M0 7 H20" stroke="#2b2b2b" strokeWidth="2.2" />
                            </svg>
                        ) : (
                            // German flag, monochrome
                            <svg width="22" height="15" viewBox="0 0 20 14" aria-hidden="true">
                                <rect width="20" height="14" rx="2" fill="#e5e5e5" />
                                <rect width="20" height="4.67" rx="2" fill="#2b2b2b" />
                                <rect y="4.67" width="20" height="4.66" fill="#6f6f6f" />
                            </svg>
                        )}
                    </button>
                    )}

                    {/* Demo link → public live-demo (Epic #1582) */}
                    <button
                        className="lb-nav-demo-btn"
                        aria-label={t("landing.nav.demoAria")}
                        title={t("landing.nav.demoAria")}
                        onClick={(e) => {
                            e.stopPropagation();
                            navigate("/live-demo");
                        }}
                    >
                        {t("landing.nav.demo")}
                    </button>
                </div>
            </nav>

            {/* Mobile-only open-source pill — hidden, removed per request */}

            {/* Hero */}
            <div className="lb-hero-scrub" id="lbScrub" ref={scrubRef}>
                <section
                    className="lb-hero"
                    style={{
                        padding:
                            "clamp(40px,6vw,80px) var(--lb-gutter,40px) clamp(40px,6vw,80px)",
                    }}
                >
                    <div
                        className="lb-hero-inner"
                        style={{
                            display: "block",
                            maxWidth: 1280,
                            margin: "0 auto",
                            position: "relative",
                            zIndex: 3,
                        }}
                    >
                        <h1
                            className="tr-headline is-revealed"
                            data-words=""
                            style={{
                                maxWidth: "none",
                                fontSize: "clamp(56px,7vw,108px)",
                                margin: "0 0 clamp(16px,2vw,28px)",
                            }}
                        >
                            {t(`${heroCopy}.h1a`)}<br />{t(`${heroCopy}.h1b`)}
                        </h1>
                        <div className="lb-hero-grid">
                            <div
                                className="lb-hero-text"
                                style={{
                                    display: "flex",
                                    flexDirection: "column",
                                    gap: 18,
                                    maxWidth: 540,
                                }}
                            >
                                <p
                                    className="lb-hero-sub"
                                    style={{
                                        margin: 0,
                                        fontFamily: "'Inter',system-ui,sans-serif",
                                        fontWeight: 700,
                                        letterSpacing: "-0.02em",
                                        lineHeight: 1.1,
                                        color: "#0a0a0a",
                                        fontSize: "clamp(26px,3vw,38px)",
                                        whiteSpace: "nowrap",
                                    }}
                                >{t("landing.hero.sub")}</p>
                                <p
                                    style={{
                                        fontSize: 16,
                                        lineHeight: 1.55,
                                        color: "#666",
                                        margin: "18px 0 0",
                                        maxWidth: "46ch",
                                    }}
                                >
                                    <b style={{ color: "#0a0a0a", fontWeight: 600 }}>autonomous<span style={{ color: "var(--lb-accent, #00c27a)" }}>_</span></b>{" "}
                                    {t("landing.hero.body")}
                                </p>
                                <div
                                    className="lb-hero-ctas"
                                    style={{
                                        display: "flex",
                                        flexDirection: "column",
                                        alignItems: "flex-start",
                                        gap: 10,
                                        marginTop: 28,
                                    }}
                                >
                                    <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                                        {/* Hero CTAs — same black `lb-cta-social` pill (size/colour unchanged),
                                            only label + link differ: LIVE DEMO → the demo page, Download → the
                                            landing's Editions section (choose one of the three variants). */}
                                        <a
                                            className="lb-cta-social"
                                            href="/live-demo"
                                            onClick={(e) => {
                                                e.preventDefault();
                                                navigate("/live-demo");
                                            }}
                                            data-cta="live_demo"
                                            aria-label={t("landing.hero.ctaDemoAria")}
                                        >{t("landing.hero.ctaDemo")}</a>
                                        <a
                                            className="lb-cta-social"
                                            href="#block-editions"
                                            onClick={(e) => {
                                                e.preventDefault();
                                                scrollToHash("#block-editions", lenisRef.current);
                                            }}
                                            data-cta="download"
                                            aria-label={t("landing.hero.ctaDownloadAria")}
                                        >{t("landing.hero.ctaDownload")}</a>
                                        {/* #4514: the code itself, same URL as the header GitHub icon */}
                                        <a
                                            className="lb-cta-social"
                                            href={REPO_URL}
                                            target="_blank"
                                            rel="noopener"
                                            data-cta="github_code"
                                        >{t("landing.hero.ctaCode")}</a>
                                    </div>
                                    {/* Tower flag (operator 16.08.) — the ONLY new element in the hero.
                                        Full-green like the kill switch, the product itself on a white
                                        disc so the hardware reads instantly, stretched to the CTA row's
                                        width above it. Nothing existing is restyled. */}
                                    <a
                                        href="/appliance"
                                        onClick={(e) => {
                                            e.preventDefault();
                                            navigate("/appliance");
                                        }}
                                        aria-label={t("landing.hero.flagAria")}
                                        style={{
                                            alignSelf: "stretch",
                                            display: "flex",
                                            alignItems: "center",
                                            gap: 12,
                                            padding: "8px 16px 8px 8px",
                                            borderRadius: 999,
                                            background: "#00c27a",
                                            color: "#001b10",
                                            textDecoration: "none",
                                            boxShadow: "0 10px 30px rgba(0,194,122,.35)",
                                        }}
                                    >
                                        <span
                                            style={{
                                                background: "#fff",
                                                borderRadius: 999,
                                                width: 44,
                                                height: 44,
                                                flex: "0 0 44px",
                                                display: "flex",
                                                alignItems: "center",
                                                justifyContent: "center",
                                            }}
                                        >
                                            <img
                                                src="/appliance-tower.webp"
                                                alt=""
                                                style={{ height: 34, width: "auto" }}
                                            />
                                        </span>
                                        <span style={{ minWidth: 0 }}>
                                            <span
                                                style={{
                                                    display: "block",
                                                    fontSize: 9.5,
                                                    fontWeight: 800,
                                                    letterSpacing: ".14em",
                                                    textTransform: "uppercase",
                                                    opacity: 0.72,
                                                    whiteSpace: "nowrap",
                                                }}
                                            >{t("landing.hero.flagKicker")}</span>
                                            <span
                                                style={{
                                                    display: "block",
                                                    fontSize: 14,
                                                    fontWeight: 800,
                                                    letterSpacing: "-0.01em",
                                                    whiteSpace: "nowrap",
                                                }}
                                            >
                                                {t("landing.hero.flagName")}
                                            </span>
                                        </span>
                                        <span style={{ marginLeft: "auto", paddingLeft: 8, fontWeight: 800, fontSize: 16 }}>
                                            →
                                        </span>
                                    </a>
                                </div>
                            </div>
                            <div
                                ref={terminalRef}
                                className="lb-hero-terminal aaa-console !p-0 flex flex-col bg-black relative"
                                style={{
                                    marginTop: -60,
                                    alignSelf: "start",
                                    ["--terminal-scale" as any]: terminalScale
                                }}
                            >
                                {/* Initial Brand Wordmark (Fades out on scroll) */}
                                {logoOpacity > 0 && (
                                                                     <div
                                        className="absolute inset-0 bg-black z-50 pointer-events-none aaa-console-logo-container"
                                        style={{
                                            opacity: logoOpacity,
                                            transform: `scale(${logoScale})`,
                                            transition: "opacity 0.05s linear, transform 0.05s linear"
                                        }}
                                    >
                                        <div className="aaa-console-logo font-bold tracking-tight text-white select-none leading-none" data-words>
                                            autonomous<span className="aaa-blink" style={{ color: "#00c27a" }}>_</span>
                                        </div>
                                    </div>
                                )}

                                {/* Mock Console App Shell (Fades in on scroll) */}
                                <div
                                    className="flex-grow flex flex-col min-h-0 origin-top-left"
                                    style={{
                                        opacity: appOpacity,
                                        transition: "opacity 0.05s linear",
                                        width: isMobileOrTablet ? 800 : "100%",
                                        height: isMobileOrTablet ? 600 : "100%",
                                        transform: isMobileOrTablet ? `scale(${terminalScale})` : "none",
                                        position: isMobileOrTablet ? "absolute" : "relative",
                                        left: 0,
                                        top: 0,
                                        overflow: "hidden"
                                    }}
                                >
                                    {/* Window Titlebar */}
                                    <div className="h-9 shrink-0 flex items-center bg-black/60 border-b border-white/5 backdrop-blur-md select-none px-3 justify-between">
                                        <div className="flex items-center gap-2">
                                            <img src="/favicon.svg" alt="autonomous_" className="w-[18px] h-[18px] rounded-[4px]" />
                                            <span className="text-[13px] font-semibold tracking-tight text-white/90 leading-none">
                                                autonomous<span style={{ color: "#00c27a" }}>_</span>
                                            </span>
                                        </div>
                                        <div className="text-[11px] text-white/30 num">
                                            PAPER · Alpaca
                                        </div>
                                        <div className="flex items-center gap-1.5">
                                            <span className="w-2.5 h-2.5 rounded-full bg-white/10"></span>
                                            <span className="w-2.5 h-2.5 rounded-full bg-white/10"></span>
                                            <span className="w-2.5 h-2.5 rounded-full bg-white/10"></span>
                                        </div>
                                    </div>

                                    {/* Main layout container */}
                                    <div className="flex-grow flex min-h-0 bg-[#121214]">
                                        {/* Sidebar */}
                                        <div className="flex w-[150px] shrink-0 flex-col bg-black/40 border-r border-white/5 p-3 space-y-3">
                                            {/* Chat Button */}
                                            <button className="w-full flex items-center gap-2 px-2.5 py-2 rounded-lg text-[11px] bg-white/[0.04] text-white/80 border border-white/10 hover:bg-white/[0.07] transition-all">
                                                <IconChat width={13} height={13} />
                                                <span className="flex-grow text-left font-medium">Chat</span>
                                                <IconChevronRight width={9} height={9} className="opacity-40" />
                                            </button>
                                            <div className="h-px bg-white/5" />
                                            {/* Navigation items */}
                                            <nav className="flex-1 space-y-0.5">
                                                {[
                                                    { id: "overview", label: "Overview", Icon: IconDashboard, active: true },
                                                    { id: "decisions", label: "Decisions", Icon: IconQueue },
                                                    { id: "positions", label: "Positions", Icon: IconPositions },
                                                    { id: "reports", label: "Reports", Icon: IconReports },
                                                    { id: "simulation", label: "Simulation", Icon: IconBolt },
                                                    { id: "audit", label: "Audit chain", Icon: IconAudit },
                                                    { id: "settings", label: "Settings", Icon: IconSettings }
                                                ].map((item) => (
                                                    <button
                                                        key={item.id}
                                                        className={`w-full flex items-center gap-2 px-2.5 py-1.5 rounded-lg text-[11px] transition-all ${
                                                            item.active
                                                                ? "bg-white/8 text-white/92 border border-white/10"
                                                                : "text-white/55 hover:text-white/92 hover:bg-white/5 border border-transparent"
                                                        }`}
                                                    >
                                                        <item.Icon width={12} height={12} className={item.active ? "text-white/92" : ""} />
                                                        <span className="flex-grow text-left">{item.label}</span>
                                                    </button>
                                                ))}
                                            </nav>
                                            <div className="h-px bg-white/5" />
                                            {/* Sidebar status footer */}
                                            <div className="text-[9px] text-white/30 space-y-1">
                                                <div className="flex justify-between">
                                                    <span>Engine</span>
                                                    <span className="text-[#5be584]">running</span>
                                                </div>
                                                <div className="flex justify-between">
                                                    <span>Trading</span>
                                                    <span className="text-white/55">Paper</span>
                                                </div>
                                            </div>
                                        </div>

                                        {/* Console Overview Content */}
                                        <div className="flex-grow overflow-y-auto p-4 space-y-4 bg-black select-none">
                                            {/* Header greeting */}
                                            <div
                                                style={{
                                                    opacity: greetingOpacity,
                                                    transform: `translateY(${(1 - greetingOpacity) * 10}px)`,
                                                    transition: "opacity 0.1s linear, transform 0.1s linear"
                                                }}
                                            >
                                                <div className="eyebrow text-[9px] mb-1">Portfolio · Live · Paper trading</div>
                                                <div className="text-[20px] md:text-[24px] font-bold text-white/92 leading-tight">
                                                    {greeting()}
                                                </div>
                                                <div className="text-[11px] text-white/40 mt-1">
                                                    The round table raised 1 decision today.
                                                </div>
                                            </div>

                                            {/* Total Equity Surface */}
                                            <div
                                                className="surface p-4 relative overflow-hidden"
                                                style={{
                                                    opacity: equityOpacity,
                                                    transform: `translateY(${(1 - equityOpacity) * 10}px)`,
                                                    transition: "opacity 0.1s linear, transform 0.1s linear"
                                                }}
                                            >
                                                <div className="halo-soft absolute -top-16 -right-16 w-48 h-48 pointer-events-none" />
                                                <div className="flex justify-between items-start">
                                                    <div>
                                                        <div className="eyebrow text-[9px] mb-1">Total equity · EUR</div>
                                                        {/* #4514: equity only — no daily P/L, no return since inception, no benchmark */}
                                                        <div className="flex items-baseline gap-2 flex-wrap">
                                                            <div className="num text-[24px] font-bold text-white/92">
                                                                {fmtEUR(122450.00).replace("€", "€ ")}
                                                            </div>
                                                        </div>
                                                    </div>
                                                    {/* Range buttons */}
                                                    <div className="flex items-center gap-1">
                                                        {["1W", "1M", "ALL"].map((r) => (
                                                            <span
                                                                key={r}
                                                                className={`text-[9px] px-1.5 py-0.5 rounded transition-colors ${
                                                                    r === "1W" ? "text-[#00c27a] bg-[#00c27a]/12" : "text-white/40 border border-white/5"
                                                                }`}
                                                            >
                                                                {r}
                                                            </span>
                                                        ))}
                                                    </div>
                                                </div>

                                                {/* Equity curve SVG Chart */}
                                                <div className="mt-3 equity-grid rounded-lg p-1">
                                                    <EquityChart
                                                        data={MOCK_DATA.slice(0, Math.max(2, Math.round(2 + (MOCK_DATA.length - 2) * chartProgress)))}
                                                        height={100}
                                                        glowColor="#00c27a"
                                                        showGrid={false}
                                                        showAxes={false}
                                                    />
                                                </div>
                                            </div>

                                            {/* KPI Cards Grid */}
                                            <div
                                                className="grid grid-cols-2 gap-2"
                                                style={{
                                                    opacity: kpiOpacity,
                                                    transform: `translateY(${(1 - kpiOpacity) * 10}px)`,
                                                    transition: "opacity 0.1s linear, transform 0.1s linear"
                                                }}
                                            >
                                                {[
                                                    { label: t("landing.hero.console.kpi.positions"), value: String(HERO_KPIS.openPositions), hint: t("landing.hero.console.kpi.positionsHint", { count: EXAMPLE_RULES.maxPositions }) },
                                                    { label: t("landing.hero.console.kpi.agents"), value: String(HERO_KPIS.agentsActive), hint: t("landing.hero.console.kpi.agentsHint") },
                                                    { label: t("landing.hero.console.kpi.voting"), value: String(HERO_KPIS.agentsVoting), hint: t("landing.hero.console.kpi.votingHint") },
                                                    { label: t("landing.hero.console.kpi.cash"), value: fmtEUR(HERO_KPIS.cashEur, { compact: true }), hint: t("landing.hero.console.kpi.cashHint") }
                                                ].map((k) => (
                                                    <div key={k.label} className="surface-flat p-2.5 rounded-lg">
                                                        <div className="eyebrow text-[8px] mb-1">{k.label}</div>
                                                        <div className="num text-[14px] md:text-[16px] font-semibold flex items-baseline text-white/92">
                                                            {k.value}
                                                        </div>
                                                        <div className="text-[8px] text-white/30 mt-0.5">{k.hint}</div>
                                                    </div>
                                                ))}
                                            </div>

                                            {/* EU AI Act Art. 50 — AI-interaction transparency */}
                                            <div
                                                className="text-[9px] text-white/30 pt-2 border-t border-white/5 space-y-1"
                                                style={{
                                                    opacity: disclosureOpacity,
                                                    transform: `translateY(${(1 - disclosureOpacity) * 5}px)`,
                                                    transition: "opacity 0.1s linear, transform 0.1s linear"
                                                }}
                                            >
                                                <p>You are interacting with an AI system. Responses are AI-generated, not investment advice. To chat, click "Chat" or ask the agents anything in the console.</p>
                                            </div>
                                        </div>
                                    </div>
                                </div>
                            </div>
                        </div>
                    </div>
                    <div className="lb-hero-inner-2" aria-hidden="true">
                        <div className="lb-hero-inner-2-track">
                            <h2 className="lb-hero-h2">
                                <b>autonomous<span style={{ color: "var(--lb-accent, #00c27a)" }}>_</span></b><br /> {t("landing.hero.rot1")} <br />{t("landing.hero.rot2")} <br />{t("landing.hero.rot3")}
                            </h2>
                        </div>
                    </div>
                </section>
            </div>

            {/* BLOCK 1 — YOUR RULES. #4514: a static example of rules and agent weights replaces
                the performance chart (no return figures on the landing). */}
            <section className="tr-section" id="block-profit">
                <div className="tr-grid">
                    <div className="tr-stage">
                        <div className="pex">
                            <div>
                                <div className="pex-lbl">{t("landing.profit.example.rulesTitle")}</div>
                                {[
                                    [t("landing.profit.example.maxPositions"), String(EXAMPLE_RULES.maxPositions)],
                                    [t("landing.profit.example.volSizing"), t("landing.profit.example.on")],
                                    [t("landing.profit.example.dailyVol"), `${EXAMPLE_RULES.dailyVolTargetPct} %`],
                                    [t("landing.profit.example.minHold"), t("landing.profit.example.days", { count: EXAMPLE_RULES.minHoldDays })],
                                ].map(([k, v]) => (
                                    <div key={k} className="pex-row"><span className="pex-k">{k}</span><span className="pex-v">{v}</span></div>
                                ))}
                            </div>
                            <div>
                                <div className="pex-lbl">{t("landing.profit.example.weightsTitle")}</div>
                                {EXAMPLE_WEIGHTS.map(([name, w]) => (
                                    <div key={name} className="pex-row"><span className="pex-k">{name}</span><span className="pex-v">{w.toFixed(2)}</span></div>
                                ))}
                            </div>
                        </div>
                    </div>

                    <div className="tr-text">
                        <h2 className="tr-headline">{t("landing.profit.h2")}</h2>
                        <p className="tr-intro">
                            {mitMarke(t("landing.profit.intro"))}
                        </p>
                    </div>

                    <div className="tr-body">
                        <p className="pt">
                            <b>{t("landing.profit.p1.lead")}</b>{" "}{t("landing.profit.p1.text")}
                        </p>
                        <p className="pt">
                            <b>{t("landing.profit.p2.lead")}</b>{" "}{t("landing.profit.p2.text")}
                        </p>
                        <p className="pt">
                            <b>{t("landing.profit.p3.lead")}</b>{" "}{t("landing.profit.p3.text")}
                        </p>
                        <p className="pt">
                            <b>{t("landing.profit.p4.lead")}</b>{" "}{t("landing.profit.p4.text")}
                        </p>
                    </div>
                </div>
            </section>

            {/* BLOCK 3 — COMPLIANCE (light) */}
            <section className="tr-section" id="block-auto">
                <div className="tr-grid reverse">
                    <div className="tr-text">
                        <h2 className="tr-headline">{t("landing.safe.h2")}</h2>
                        <p className="tr-intro">
                            {mitMarke(t("landing.safe.intro"))}
                        </p>
                    </div>

                    <div className="tr-body">
                        <p className="pt">
                            <b>{t("landing.safe.p1.lead")}</b>{" "}{t("landing.safe.p1.text")}
                        </p>
                        <p className="pt">
                            <b>{t("landing.safe.p2.lead")}</b>{" "}{t("landing.safe.p2.text")}
                        </p>
                        <p className="pt">
                            <b>{t("landing.safe.p3.lead")}</b>{" "}{t("landing.safe.p3.text")}
                        </p>
                        <p className="pt">
                            <b>{t("landing.safe.p4.lead")}</b>{" "}{t("landing.safe.p4.text")}
                        </p>
                    </div>

                    <div className="tr-stage">
                        <div className="auditflow">
                            <div className="af2-head">
                                <div className="af2-lbl">AUDIT CHAIN · SIMULATED</div>
                                <div className="af2-count">
                                    <div className="af2-big">{auditCount.toLocaleString("en-US")}</div>
                                    <div className="af2-delta">records sealed</div>
                                </div>
                            </div>
                            <div className="af2-flow">
                                <span className="af2-track" aria-hidden="true"></span>
                                <span className="af2-pulse" aria-hidden="true"></span>
                                <div className="af2-node">
                                    <span className="af2-node-num">01</span>
                                    <div className="af2-node-name">decision</div>
                                    <div className="af2-node-tag">+ reasoning string</div>
                                </div>
                                <div className="af2-node">
                                    <span className="af2-node-num">02</span>
                                    <div className="af2-node-name">hash chain</div>
                                    <div className="af2-node-tag">prev-record linked</div>
                                </div>
                                <div className="af2-node">
                                    <span className="af2-node-num">03</span>
                                    <div className="af2-node-name">dual-write</div>
                                    <div className="af2-node-tag">2 sinks in parallel</div>
                                </div>
                                <div className="af2-node af2-node-final">
                                    <span className="af2-node-num">✓</span>
                                    <div className="af2-node-name">sealed</div>
                                    <div className="af2-node-tag">tamper-evident</div>
                                </div>
                            </div>
                            <div className="af2-stores">
                                <div className="af2-store"><span className="af2-store-dot"></span><span>JSONL hash-chain · local</span></div>
                                <div className="af2-store"><span className="af2-store-dot"></span><span>{t("landing.audit.graphic.sink2")}</span></div>
                            </div>
                        </div>
                    </div>
                </div>
            </section>

            {/* BLOCK 2 — LESS RISK (light) */}
            <section
                className="tr-section"
                id="block-safe"
            >
                <div className="tr-grid">
                    <div className="tr-stage">
                        <div className="rpipe">
                            <div className="rpipe-lbl">RISK PIPELINE · LIVE</div>
                            <div className="rpipe-stack">
                                <span className="rpipe-track" aria-hidden="true"></span>
                                <span className="rpipe-pulse" aria-hidden="true"></span>
                                <div className="rpipe-layer" title="Aggregates the per-symbol signal models and routes a motion to the voting ensemble.">
                                    <span className="rpipe-num">01</span>
                                    <div className="rpipe-body">
                                        <div className="rpipe-name">Coordinator</div>
                                        <div className="rpipe-tag">per-symbol signals · normalised</div>
                                    </div>
                                    <span className="rpipe-state">pass</span>
                                </div>
                                <div className="rpipe-layer" title={t("landing.safe.pipeline.votingTitle")}>
                                    <span className="rpipe-num">02</span>
                                    <div className="rpipe-body">
                                        <div className="rpipe-name">{t("landing.safe.pipeline.votingName")}</div>
                                        <div className="rpipe-tag">{t("landing.safe.pipeline.votingTag")}</div>
                                    </div>
                                    <span className="rpipe-state">pass</span>
                                </div>
                                <div className="rpipe-layer" title="Validates mandate, restricted symbols, and concentration before any order leaves the platform.">
                                    <span className="rpipe-num">03</span>
                                    <div className="rpipe-body">
                                        <div className="rpipe-name">Compliance gate</div>
                                        <div className="rpipe-tag">synchronous · pre-trade</div>
                                    </div>
                                    <span className="rpipe-state">pass</span>
                                </div>
                                <div className="rpipe-layer" title="Portfolio allocation engine. Enforces position sizing, sector concentration caps, and tail-risk budget before routing.">
                                    <span className="rpipe-num">04</span>
                                    <div className="rpipe-body">
                                        <div className="rpipe-name">Optimizer</div>
                                        <div className="rpipe-tag">sizing · concentration · tail</div>
                                    </div>
                                    <span className="rpipe-state">pass</span>
                                </div>
                                <div className="rpipe-layer rpipe-out" title="Broker adapter. Paper-trading by default; live trading requires a deliberate, WORM-verified confirmation.">
                                    <span className="rpipe-num">→</span>
                                    <div className="rpipe-body">
                                        <div className="rpipe-name">Broker</div>
                                        <div className="rpipe-tag">paper · WORM-verified for live</div>
                                    </div>
                                    <span className="rpipe-state rpipe-state-out">routed</span>
                                </div>
                            </div>
                            <div className="rpipe-foot">
                                <span className="rpipe-killswitch">
                                    <span className="rpipe-ks-dot" aria-hidden="true"></span>
                                    Kill-switch · armed · outside the pipeline
                                </span>
                                <span className="rpipe-killswitch" title="Cross-cutting risk-limit engine that enforces your own limits.">
                                    <span className="rpipe-ks-dot rpipe-ks-dot-rm" aria-hidden="true"></span>
                                    Risk Manager · cross-cutting
                                </span>
                            </div>
                        </div>
                    </div>

                    <div className="tr-text">
                        <h2 className="tr-headline">{t("landing.audit.h2")}</h2>
                        <p className="tr-intro">
                            {mitMarke(t("landing.audit.intro"))}
                        </p>
                    </div>

                    <div className="tr-body">
                        <p className="pt">
                            <b>{t("landing.audit.p1.lead")}</b>{" "}{t("landing.audit.p1.text")}
                        </p>
                        <p className="pt">
                            <b>{t("landing.audit.p2.lead")}</b>{" "}{t("landing.audit.p2.text")}
                        </p>
                        <p className="pt">
                            <b>{t("landing.audit.p3.lead")}</b>{" "}{t("landing.audit.p3.text")}
                        </p>
                        <p className="pt">
                            <b>{t("landing.audit.p4.lead")}</b>{" "}{t("landing.audit.p4.text")}
                        </p>
                    </div>
                </div>
            </section>

            {/* Skyline divider */}
            <div className="skyline-divider" id="skylineDivider">
                <img src="/assets/skyline-nyc.jpg" alt="" />
            </div>

            {/* BLOCK 4 — MODULARITY / COMPATIBILITY (now dark) — HIDDEN BY USER REQUEST
            <section className="tr-section dark" id="block-modular">
                ... (modular section content) ...
            </section>
            */}

            {/* BLOCK 5 — EDITIONS: positioning cards + Full Transparence + matrix */}
            <section className="tr-section dark" style={{ padding: '120px 0' }}>
                <div id="block-editions" style={{ maxWidth: 1200, margin: "0 auto", width: "100%" }}>

                    <div className="text-center mb-16">
                         <h2 className="tr-headline" style={{
                           marginBottom: "12px",
                           textAlign: "center",
                           fontSize: '48px',
                           fontWeight: 800,
                           letterSpacing: '-0.02em'
                         }}>
                           {t("landing.editions.h2")}<span style={{ color: '#fff' }}>.</span>
                         </h2>
                    </div>

                    <div className="ed-tiers" style={{
                      display: 'grid',
                      gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))',
                      gap: '24px'
                    }}>
                        <PricingCard tier={pricing.private} mode={pricingMode} setMode={setPricingMode} />
                        <PricingCard tier={pricing.professional} isProfessional />
                        <PricingCard tier={pricing.institutional} isInstitutional />
                    </div>

                    {/* Go-live (tower launch): the hardware edition — a full-width banner
                        card in the ed-tier visual language, deep-linking to /appliance. */}
                    <a
                        href="/appliance"
                        onClick={(e) => {
                            e.preventDefault();
                            navigate("/appliance");
                        }}
                        aria-label={t("landing.editions.towerAria")}
                        style={{ textDecoration: "none", color: "inherit", display: "block", marginTop: 24 }}
                    >
                        {/* Full-story layout (operator pick 14.08., proposal 1): headline
                            with green "new", the tower page's three story tiles in full
                            length, footer row price left / Learn More (Contact-Us format)
                            right. */}
                        <article
                            className="ed-tier"
                            style={{
                                background: "rgba(255, 255, 255, 0.015)",
                                boxShadow: "0 0 40px rgba(0, 194, 122, 0.10)",
                                borderRadius: "18px",
                                padding: "48px 40px",
                                position: "relative",
                                overflow: "hidden",
                                cursor: "pointer",
                            }}
                        >
                            <span style={{ fontFamily: "var(--lb-mono)", fontSize: "13px", letterSpacing: "2px", fontWeight: 600, textTransform: "uppercase", color: "#00c27a", display: "block", marginBottom: "12px" }}>{t("landing.editions.towerKicker")}</span>
                            <h3 style={{ fontSize: "33px", fontWeight: 700, margin: 0 }}>
                                {t("landing.editions.towerH3a")} <span style={{ color: "#00c27a" }}>{t("landing.editions.towerNew")}</span> autonomous
                                <span style={{ color: "#00c27a" }}>_</span> {t("landing.editions.towerH3b")}
                            </h3>
                            <div className="grid grid-cols-1 md:grid-cols-3" style={{ gap: "32px", marginTop: "28px" }}>
                                {[
                                    [t("landing.editions.tile1.title"), t("landing.editions.tile1.body")],
                                    [t("landing.editions.tile2.title"), t("landing.editions.tile2.body")],
                                    [t("landing.editions.tile3.title"), t("landing.editions.tile3.body")],
                                ].map(([title, body]) => (
                                    <div key={title}>
                                        <h4 style={{ fontSize: "16px", fontWeight: 700, margin: "0 0 8px" }}>{title}</h4>
                                        <p style={{ fontSize: "14.5px", lineHeight: 1.6, color: "rgba(255,255,255,0.62)", margin: 0 }}>{body}</p>
                                    </div>
                                ))}
                            </div>
                            {/* Footer: on mobile the exact editions-card pattern (price +
                                sub, green meta line below, full-width py-8 CTA); on md+ the
                                approved banner row (price left, 300px Learn More right). */}
                            <div className="flex flex-col md:flex-row md:items-center md:justify-between gap-6" style={{ marginTop: "40px", borderTop: "1px solid rgba(255,255,255,0.08)", paddingTop: "32px" }}>
                                <div>
                                    <div className="flex items-baseline gap-2 mb-1">
                                        <span className="text-4xl font-black text-white">{t("appliance.hero.price")}</span>
                                        <span className="hidden md:inline text-xs text-[#555]">{t("landing.editions.metaDesktop")}</span>
                                    </div>
                                    <div className="md:hidden text-[12px] font-semibold text-[#00c27a] tracking-wide">{t("landing.editions.metaMobile")}</div>
                                </div>
                                <span className="h-[51px] rounded-full font-bold text-sm transition-all bg-[#00c27a] text-black hover:bg-[#00e08d] shadow-[0_0_20px_rgba(0,194,122,0.3)] inline-flex items-center justify-center w-full md:w-[300px] shrink-0">{t("landing.editions.learnMore")}</span>
                            </div>
                        </article>
                    </a>

                    <div className="ed-foot" style={{ marginTop: '64px' }}>{t("landing.editions.foot")}</div>
                </div>
            </section>

            {/* BLOCK 6 — ABOUT / FOUNDERS */}
            <section className="tr-section dark" id="block-about">
                <div className="tr-grid reverse">
                    <div className="tr-stage">
                        <div className="about-box">
                            {/* Georg */}
                            <a href="https://www.linkedin.com/in/georg-apeldorn-640160335/" target="_blank" rel="noopener noreferrer" className="founder-card">
                                <div className="founder-info">
                                    <div className="founder-name">Georg Apeldorn</div>
                                    <div className="founder-role">{t("landing.about.roleGeorg")}</div>
                                </div>
                                <svg className="founder-li" viewBox="0 0 24 24" width="20" height="20" fill="currentColor">
                                    <path d="M20.447 20.452h-3.554v-5.569c0-1.328-.027-3.037-1.852-3.037-1.853 0-2.136 1.445-2.136 2.939v5.667H9.351V9h3.414v1.561h.046c.477-.9 1.637-1.85 3.37-1.85 3.601 0 4.267 2.37 4.267 5.455v6.286zM5.337 7.433a2.062 2.062 0 01-2.063-2.065 2.064 2.064 0 112.063 2.065zm1.782 13.019H3.555V9h3.564v11.452zM22.225 0H1.771C.792 0 0 .774 0 1.729v20.542C0 23.227.792 24 1.771 24h20.451C23.2 24 24 23.227 24 22.271V1.729C24 .774 23.2 0 22.222 0h.003z"/>
                                </svg>
                            </a>
                            {/* Andreas */}
                            <a href="https://www.linkedin.com/in/andreas-e-apeldorn-00a8a924/" target="_blank" rel="noopener noreferrer" className="founder-card">
                                <div className="founder-info">
                                    <div className="founder-name">Andreas Apeldorn</div>
                                    <div className="founder-role">{t("landing.about.roleAndreas")}</div>
                                </div>
                                <svg className="founder-li" viewBox="0 0 24 24" width="20" height="20" fill="currentColor">
                                    <path d="M20.447 20.452h-3.554v-5.569c0-1.328-.027-3.037-1.852-3.037-1.853 0-2.136 1.445-2.136 2.939v5.667H9.351V9h3.414v1.561h.046c.477-.9 1.637-1.85 3.37-1.85 3.601 0 4.267 2.37 4.267 5.455v6.286zM5.337 7.433a2.062 2.062 0 01-2.063-2.065 2.064 2.064 0 112.063 2.065zm1.782 13.019H3.555V9h3.564v11.452zM22.225 0H1.771C.792 0 0 .774 0 1.729v20.542C0 23.227.792 24 1.771 24h20.451C23.2 24 24 23.227 24 22.271V1.729C24 .774 23.2 0 22.222 0h.003z"/>
                                </svg>
                            </a>
                        </div>
                    </div>

                    <div className="tr-text">
                        <h2 className="tr-headline" style={{ marginBottom: "24px" }}>{t("landing.about.h2")} <b>autonomous<span style={{ color: "var(--lb-accent, #00c27a)" }}>_</span></b></h2>
                        <p className="tr-intro" style={{ fontSize: "18px", lineHeight: 1.6, color: "#a0a0a0" }}>
                            <b>autonomous<span style={{ color: "var(--lb-accent, #00c27a)" }}>_</span></b>{" "}{t("landing.about.p1")}
                        </p>
                        <p className="tr-intro" style={{ fontSize: "18px", lineHeight: 1.6, color: "#a0a0a0", marginTop: "16px" }}>{t("landing.about.p2")}</p>
                        <div style={{ marginTop: "40px" }}>
                            <p style={{ fontSize: "13px", lineHeight: 1.5, color: "#666", borderTop: "1px solid rgba(255,255,255,0.1)", paddingTop: "16px", margin: 0 }}>
                                <i>{t("landing.about.disclaimer")}</i>
                            </p>
                        </div>
                    </div>
                </div>
            </section>

            {/* Footer */}
            <footer className="lb-footer" style={{ padding: "80px var(--lb-gutter) 40px" }}>
                <div className="lb-footer-grid">
                    <div>
                        <div
                            style={{
                                fontWeight: 800,
                                fontSize: 18,
                                color: "#fff",
                                letterSpacing: "0.5px",
                                fontFamily: "var(--lb-mono)",
                            }}
                        >
                            aaagents<span style={{ color: "#00c27a" }}>_</span>
                        </div>
                        <p
                            style={{
                                maxWidth: "42ch",
                                marginTop: 16,
                                fontSize: 13,
                                lineHeight: 1.6,
                            }}
                        >{t("landing.footer.tagline")}</p>
                    </div>
                    <div></div>
                    <div>
                        <h4>{t("landing.footer.help")}</h4>
                        <a href="/support">{t("landing.footer.support")}</a>
                    </div>
                    <div>
                        <h4>{t("landing.footer.legal")}</h4>
                        <a href="/legal/imprint">{t("landing.footer.imprint")}</a>
                        <a href="/legal/privacy">{t("landing.footer.privacy")}</a>
                        <a href="/legal/risk-disclosure">{t("landing.footer.risk")}</a>
                    </div>
                </div>
                <p className="lb-disclaimer">{t("landing.footer.riskNote")}</p>
                <div className="lb-bottom">
                    <span>{t("landing.footer.copyright")}</span>
                    <span>autonomous-trading.de</span>
                </div>
            </footer>
        </div>
    );
}
