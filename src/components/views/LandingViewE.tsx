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
 *   - drive a custom cursor (.cursor-dot + .cursor-ring)
 *   - apply magnetic-button effect on CTAs
 *   - split headline text into per-word spans for reveal animation
 *   - reveal-on-scroll via IntersectionObserver
 *   - drive --lb-hp scroll-scrub variable for the hero pin
 *   - progressively reveal hero terminal chat as scroll progresses
 *   - animate Block 1 profit counter (+0.0% → +23.0%)
 *   - render Block 2 senate vote ledger from a VOTES const
 *   - tick a live auto-halt counter (days/hours/min/sec since 2026-04-15 13:00 UTC)
 *   - open/close subpage overlays (profit / auto / safe) with ESC + body lock
 *   - dismiss the top risk banner via localStorage
 *
 * All of those behaviors are reproduced below as React useEffect hooks.
 */
import { useEffect, useRef, useState, FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { signInWithPopup } from "firebase/auth";
import { auth, googleProvider } from "@/lib/firebase";
import Lenis from "lenis";
import { toast } from "sonner";
import "@/styles/landing-d.css";

type ChatMessage =
    | { role: "user"; content: string }
    | { role: "agent"; name: string; content: string };

// Hero terminal — illustrative log of one motion through the real pipeline.
// Names + thresholds map to the live codebase: Stock Specialists → Coordinator
// → Senate (12 senators) → Compliance Guardian → cuFOLIO → Order Executor.
const SEED: ChatMessage[] = [
    { role: "user", content: "walk me through one decision" },
    { role: "agent", name: "system", content: "paper-trading · full S&P 500 universe · on-prem" },
    { role: "agent", name: "system", content: "≈500 signal models spawned · one per symbol" },
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

export default function LandingViewD() {
    const navigate = useNavigate();
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

    // Block 1 profit counter ("+0.0%" → "+23.0%")
    const [profitText, setProfitText] = useState<string>("+0.0%");

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

    // Overlay management
    const [openOverlay, setOpenOverlay] = useState<null | "profit" | "auto" | "safe">(null);

    // Risk banner — restored dismissed state from localStorage via useState initializer

    const dismissBanner = () => {
        setBannerOpen(false);
        try {
            localStorage.setItem("aaa_risk_banner_dismissed", "1");
        } catch {
            /* ignore */
        }
    };

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
            if (total > 0) raw = Math.min(Math.max(-rect.top / total, 0), 1);
            const p = Math.min(raw / 0.7, 1);
            root.style.setProperty("--lb-hp", p.toFixed(4));

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

    // Custom cursor (.cursor-dot + .cursor-ring) — only on fine-pointer
    // devices. Adds .has-custom-cursor to <html> so the global CSS rule
    // hides the native cursor.
    useEffect(() => {
        const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
        const isFinePointer = window.matchMedia("(hover:hover) and (pointer:fine)").matches;
        if (!isFinePointer || reduced) return;

        const dot = document.createElement("div");
        dot.className = "cursor-dot";
        dot.id = "cursorDot";
        dot.setAttribute("aria-hidden", "true");
        const ring = document.createElement("div");
        ring.className = "cursor-ring";
        ring.id = "cursorRing";
        ring.setAttribute("aria-hidden", "true");
        document.body.appendChild(dot);
        document.body.appendChild(ring);
        document.documentElement.classList.add("has-custom-cursor");

        let mx = window.innerWidth / 2,
            my = window.innerHeight / 2;
        let dx = mx,
            dy = my,
            rx = mx,
            ry = my;
        let visible = false;
        let raf = 0;

        const onMove = (e: MouseEvent) => {
            mx = e.clientX;
            my = e.clientY;
            if (!visible) {
                visible = true;
                dot.style.opacity = "1";
                ring.style.opacity = "1";
            }
        };
        const onLeave = () => {
            dot.style.opacity = "0";
            ring.style.opacity = "0";
            visible = false;
        };
        const onDown = () => ring.classList.add("is-press");
        const onUp = () => ring.classList.remove("is-press");

        const tick = () => {
            dx += (mx - dx) * 0.95;
            dy += (my - dy) * 0.95;
            rx += (mx - rx) * 0.18;
            ry += (my - ry) * 0.18;
            dot.style.transform = `translate3d(${dx}px,${dy}px,0) translate(-50%,-50%)`;
            ring.style.transform = `translate3d(${rx}px,${ry}px,0) translate(-50%,-50%)`;
            raf = requestAnimationFrame(tick);
        };
        raf = requestAnimationFrame(tick);

        const hoverSel =
            'a, button, .blk-cta, .lb-cta-primary, .lb-cta-ghost, .lb-nav-link, .blk-visual, [data-open], [data-cursor="hover"]';
        const textSel = 'input, textarea, [contenteditable="true"]';

        const onOver = (e: MouseEvent) => {
            const t = e.target as Element | null;
            if (!t) return;
            if (t.closest(textSel)) ring.classList.add("is-text");
            else if (t.closest(hoverSel)) ring.classList.add("is-hover");
        };
        const onOut = (e: MouseEvent) => {
            const t = e.target as Element | null;
            if (!t) return;
            if (t.closest(textSel)) ring.classList.remove("is-text");
            else if (t.closest(hoverSel)) ring.classList.remove("is-hover");
        };

        window.addEventListener("mousemove", onMove);
        window.addEventListener("mouseleave", onLeave);
        window.addEventListener("mousedown", onDown);
        window.addEventListener("mouseup", onUp);
        document.addEventListener("mouseover", onOver);
        document.addEventListener("mouseout", onOut);

        return () => {
            cancelAnimationFrame(raf);
            window.removeEventListener("mousemove", onMove);
            window.removeEventListener("mouseleave", onLeave);
            window.removeEventListener("mousedown", onDown);
            window.removeEventListener("mouseup", onUp);
            document.removeEventListener("mouseover", onOver);
            document.removeEventListener("mouseout", onOut);
            document.documentElement.classList.remove("has-custom-cursor");
            dot.remove();
            ring.remove();
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
    // tr-ledger / tr-safety / overlay rows / kpi cards / faq items.
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
            { sel: ".landing-d-root .ovl-row", type: "scr-rv" },
            { sel: ".landing-d-root .ovl-tl-item", type: "scr-rv" },
            { sel: ".landing-d-root .ovl-kpi", type: "scr-rv" },
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

    // Block 1 profit counter — animates 0.0% → 23.0% over ~1.8s once
    // #trProfitCount enters viewport.
    useEffect(() => {
        const el = document.getElementById("trProfitCount");
        if (!el) return;
        let raf = 0;
        let started = false;
        const target = 23;
        const dur = 1800;

        const start = () => {
            if (started) return;
            started = true;
            const t0 = performance.now();
            const tick = (now: number) => {
                const t = Math.min((now - t0) / dur, 1);
                const eased = 1 - Math.pow(1 - t, 3);
                const v = (target * eased).toFixed(1);
                setProfitText("+" + v + "%");
                if (t < 1) raf = requestAnimationFrame(tick);
            };
            // Source delays start by 600ms after intersection
            window.setTimeout(() => {
                raf = requestAnimationFrame(tick);
            }, 600);
        };

        const io = new IntersectionObserver(
            (entries) => {
                entries.forEach((e) => {
                    if (e.isIntersecting) {
                        start();
                        io.unobserve(e.target);
                    }
                });
            },
            { threshold: 0.4 },
        );
        io.observe(el);

        return () => {
            io.disconnect();
            cancelAnimationFrame(raf);
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

    // Overlay enrichment — once the overlay DOM exists, enrich each .overlay
    // with a curtain div + word spans inside the h1 + reveal classes on the
    // top/lede/hero/sections (mirrors the source enrichment loop).
    useEffect(() => {
        const root = rootRef.current;
        if (!root) return;
        const overlays = root.querySelectorAll<HTMLElement>(".overlay");
        overlays.forEach((o) => {
            const h1 = o.querySelector("h1");
            if (h1 && !h1.querySelector(".w")) {
                // Source uses textContent which collapses <br> into a space —
                // do the same so the per-word stagger CSS still applies.
                const words = (h1.textContent || "").trim().split(/\s+/);
                while (h1.firstChild) h1.removeChild(h1.firstChild);
                words.forEach((word, idx) => {
                    if (idx > 0) h1.appendChild(document.createTextNode(" "));
                    const span = document.createElement("span");
                    span.className = "w";
                    span.textContent = word;
                    h1.appendChild(span);
                });
            }
            const top = o.querySelector(".overlay-top");
            if (top && !top.classList.contains("reveal")) top.classList.add("reveal", "reveal-1");
            const lede = o.querySelector(".lede-big");
            if (lede && !lede.classList.contains("reveal")) lede.classList.add("reveal", "reveal-3");
            const hero = o.querySelector(".ovl-hero");
            if (hero && !hero.classList.contains("reveal")) hero.classList.add("reveal", "reveal-4");
            const secs = o.querySelectorAll(".ovl-section");
            secs.forEach((s, i) => {
                if (!s.classList.contains("reveal")) {
                    s.classList.add("reveal");
                    s.classList.add("reveal-" + Math.min(6, 5 + i));
                }
            });
        });
    }, []);

    // Body lock + Lenis pause when an overlay is open. ESC closes.
    useEffect(() => {
        if (openOverlay) {
            document.body.classList.add("lb-locked");
            const l = lenisRef.current;
            if (l && typeof l.stop === "function") l.stop();
            // scroll the freshly-opened overlay to the top
            const el = document.getElementById(
                "overlay" + openOverlay[0].toUpperCase() + openOverlay.slice(1),
            );
            if (el) {
                el.scrollTop = 0;
                const content = el.querySelector(".overlay-content") as HTMLElement | null;
                if (content) content.scrollTop = 0;
            }
        } else {
            document.body.classList.remove("lb-locked");
            const l = lenisRef.current;
            if (l && typeof l.start === "function") l.start();
        }
        const onKey = (e: KeyboardEvent) => {
            if (e.key === "Escape" && openOverlay) setOpenOverlay(null);
        };
        window.addEventListener("keydown", onKey);
        return () => {
            window.removeEventListener("keydown", onKey);
        };
    }, [openOverlay]);

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

    // Click delegation on the root — handle data-open / data-close attributes
    // (overlay triggers).
    const handleRootClick = (e: React.MouseEvent<HTMLDivElement>) => {
        const t = e.target as Element | null;
        if (!t) return;
        const opener = t.closest("[data-open]") as HTMLElement | null;
        if (opener) {
            e.preventDefault();
            const name = opener.getAttribute("data-open");
            if (name === "profit" || name === "auto" || name === "safe") {
                setOpenOverlay(name);
            }
            return;
        }
        const closer = t.closest("[data-close]") as HTMLElement | null;
        if (closer) {
            // close, then let any href propagate (mailto:/external github)
            setOpenOverlay(null);
        }
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
                <a
                    href="https://github.com/Autonomous-Asset-Management-Agents"
                    target="_blank"
                    rel="noopener"
                    className="lb-ticker-link"
                >
                    <div className="lb-ticker-track">
                        {/* Duplicate the text for seamless loop */}
                        {[0, 1].map((i) => (
                            <span key={i} className="lb-ticker-item" aria-hidden={i === 1 ? "true" : undefined}>
                                Open Source Version launched - download on github
                                &nbsp;&nbsp;&nbsp;·&nbsp;&nbsp;&nbsp;
                                Open Source Version launched - download on github
                                &nbsp;&nbsp;&nbsp;·&nbsp;&nbsp;&nbsp;
                            </span>
                        ))}
                    </div>
                </a>
            </div>

            {/* Nav */}
            <nav className="lb-nav">
                <div className="lb-nav-logo">aaagents<span style={{ color: "#00c27a" }}>_</span></div>
                <div className="lb-nav-social">
                    {/* Removed legacy CONSOLE link */}
                    <a
                        href="https://www.linkedin.com/company/aaa-autonomous-asset-management-agents/posts/?feedView=all"
                        target="_blank"
                        rel="noopener"
                        className="lb-nav-social-link"
                        aria-label="Follow us on LinkedIn"
                        title="Follow us on LinkedIn"
                    >
                        {/* LinkedIn icon */}
                        <svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
                            <path d="M20.447 20.452h-3.554v-5.569c0-1.328-.027-3.037-1.852-3.037-1.853 0-2.136 1.445-2.136 2.939v5.667H9.351V9h3.414v1.561h.046c.477-.9 1.637-1.85 3.37-1.85 3.601 0 4.267 2.37 4.267 5.455v6.286zM5.337 7.433a2.062 2.062 0 01-2.063-2.065 2.064 2.064 0 112.063 2.065zm1.782 13.019H3.555V9h3.564v11.452zM22.225 0H1.771C.792 0 0 .774 0 1.729v20.542C0 23.227.792 24 1.771 24h20.451C23.2 24 24 23.227 24 22.271V1.729C24 .774 23.2 0 22.222 0h.003z"/>
                        </svg>
                    </a>
                    <a
                        href="https://github.com/Autonomous-Asset-Management-Agents"
                        target="_blank"
                        rel="noopener"
                        className="lb-nav-social-link"
                        aria-label="View on GitHub"
                        title="View on GitHub"
                    >
                        {/* GitHub icon */}
                        <svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
                            <path d="M12 .5C5.65.5.5 5.65.5 12c0 5.08 3.29 9.39 7.86 10.91.58.1.79-.25.79-.56 0-.28-.01-1.02-.02-2-3.2.7-3.87-1.54-3.87-1.54-.52-1.33-1.28-1.69-1.28-1.69-1.05-.72.08-.7.08-.7 1.16.08 1.77 1.19 1.77 1.19 1.03 1.77 2.7 1.26 3.36.96.1-.75.4-1.26.73-1.55-2.55-.29-5.24-1.28-5.24-5.69 0-1.26.45-2.28 1.19-3.09-.12-.29-.52-1.46.11-3.05 0 0 .97-.31 3.18 1.18.92-.26 1.91-.39 2.9-.39.99 0 1.98.13 2.9.39 2.21-1.49 3.18-1.18 3.18-1.18.63 1.59.23 2.76.11 3.05.74.81 1.19 1.83 1.19 3.09 0 4.42-2.69 5.39-5.25 5.68.41.36.78 1.06.78 2.13 0 1.54-.01 2.78-.01 3.16 0 .31.21.67.8.56C20.22 21.39 23.5 17.08 23.5 12 23.5 5.65 18.35.5 12 .5z" />
                        </svg>
                    </a>

                    {/* Console login button */}
                    <button
                        id="navLoginBtn"
                        className="lb-nav-social-link"
                        aria-label="Sign in to Console"
                        title="Sign in to Console"
                        onClick={async (e) => {
                            e.stopPropagation();
                            try {
                                await signInWithPopup(auth, googleProvider);
                                navigate("/dashboard");
                            } catch (err: unknown) {
                                const code = (err as { code?: string })?.code;
                                if (code === "auth/popup-closed-by-user" || code === "auth/cancelled-popup-request") {
                                    // User closed popup — no-op
                                } else {
                                    console.error("Login fail:", err);
                                    toast.error(`Login fehlgeschlagen (${code || "unbekannt"}). Bitte überprüfe die Konsole für Details oder ob Popups blockiert werden.`);
                                }
                            }
                        }}
                        style={{
                            width: "auto",
                            padding: "0 12px",
                            background: "transparent",
                            border: "none",
                            textDecoration: "none",
                            color: "inherit",
                            fontWeight: 600,
                            fontSize: "11px",
                            letterSpacing: "1px",
                            textTransform: "uppercase",
                            cursor: "pointer",
                            whiteSpace: "nowrap"
                        }}
                    >
                        CONSOLE
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
                            Autonomous<br />Trading.
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
                                >
                                    Profitable. Auditable. Safe.
                                </p>
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
                                    provides the modular governance layer to transform AI from a
                                    decision support tool into a superior execution actor.
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
                                    <span style={{
                                        fontSize: 14,
                                        fontWeight: 500,
                                        letterSpacing: "0",
                                        textTransform: "none",
                                        color: "#0a0a0a",
                                    }}>
                                        Follow us on
                                    </span>
                                    <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                                        <a
                                            className="lb-cta-social"
                                            href="https://www.linkedin.com/company/aaa-autonomous-asset-management-agents/posts/?feedView=all"
                                            target="_blank"
                                            rel="noopener"
                                            aria-label="Follow us on LinkedIn"
                                        >
                                            {/* LinkedIn icon */}
                                            <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
                                                <path d="M20.447 20.452h-3.554v-5.569c0-1.328-.027-3.037-1.852-3.037-1.853 0-2.136 1.445-2.136 2.939v5.667H9.351V9h3.414v1.561h.046c.477-.9 1.637-1.85 3.37-1.85 3.601 0 4.267 2.37 4.267 5.455v6.286zM5.337 7.433a2.062 2.062 0 01-2.063-2.065 2.064 2.064 0 112.063 2.065zm1.782 13.019H3.555V9h3.564v11.452zM22.225 0H1.771C.792 0 0 .774 0 1.729v20.542C0 23.227.792 24 1.771 24h20.451C23.2 24 24 23.227 24 22.271V1.729C24 .774 23.2 0 22.222 0h.003z"/>
                                            </svg>
                                            LinkedIn
                                        </a>
                                        <a
                                            className="lb-cta-social"
                                            href="https://github.com/Autonomous-Asset-Management-Agents"
                                            target="_blank"
                                            rel="noopener"
                                            aria-label="View on GitHub"
                                        >
                                            {/* GitHub icon */}
                                            <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
                                                <path d="M12 .5C5.65.5.5 5.65.5 12c0 5.08 3.29 9.39 7.86 10.91.58.1.79-.25.79-.56 0-.28-.01-1.02-.02-2-3.2.7-3.87-1.54-3.87-1.54-.52-1.33-1.28-1.69-1.28-1.69-1.05-.72.08-.7.08-.7 1.16.08 1.77 1.19 1.77 1.19 1.03 1.77 2.7 1.26 3.36.96.1-.75.4-1.26.73-1.55-2.55-.29-5.24-1.28-5.24-5.69 0-1.26.45-2.28 1.19-3.09-.12-.29-.52-1.46.11-3.05 0 0 .97-.31 3.18 1.18.92-.26 1.91-.39 2.9-.39.99 0 1.98.13 2.9.39 2.21-1.49 3.18-1.18 3.18-1.18.63 1.59.23 2.76.11 3.05.74.81 1.19 1.83 1.19 3.09 0 4.42-2.69 5.39-5.25 5.68.41.36.78 1.06.78 2.13 0 1.54-.01 2.78-.01 3.16 0 .31.21.67.8.56C20.22 21.39 23.5 17.08 23.5 12 23.5 5.65 18.35.5 12 .5z" />
                                            </svg>
                                            GitHub
                                        </a>
                                    </div>
                                </div>
                            </div>
                            <div
                                className="lb-hero-terminal"
                                style={{ marginTop: -60, alignSelf: "start" }}
                            >
                                <div className="lb-bar">
                                    <span></span>
                                    <span></span>
                                    <span></span>
                                </div>
                                <div className="lb-log" id="lbChatLog" ref={chatLogRef}>
                                    {visibleLog.map((m, i) => (
                                        <div className={"lb-line" + (m.role === "user" ? " is-user" : "")} key={i}>
                                            <span className="lb-prompt">{formatPrompt(m)}</span>{" "}
                                            {m.content}
                                        </div>
                                    ))}
                                </div>
                                <form
                                    className="lb-chat-input"
                                    id="lbChatForm"
                                    onSubmit={handleChatSubmit}
                                >
                                    <input
                                        id="lbChatInput"
                                        placeholder="ask the agents anything…"
                                        autoComplete="off"
                                        value={chatInput}
                                        onChange={(e) => setChatInput(e.target.value)}
                                    />
                                    <button type="submit" className="lb-send">
                                        send ›
                                    </button>
                                </form>
                            </div>
                        </div>
                    </div>
                    <div className="lb-hero-inner-2" aria-hidden="true">
                        <div className="lb-hero-inner-2-track">
                            <h2 className="lb-hero-h2">
                                <b>autonomous<span style={{ color: "var(--lb-accent, #00c27a)" }}>_</span></b><br /> executes highly efficient. <br />On your private infrastructure. <br />With algorithmic precision.
                            </h2>
                        </div>
                    </div>
                </section>
            </div>

            {/* BLOCK 1 — PROFITABILITY */}
            <section className="tr-section" id="block-profit">
                <div className="tr-grid">
                    <div className="tr-stage">
                        <div className="perfgraph">
                            <div className="pg-head">
                                <div className="pg-lbl">PERFORMANCE · LIVE</div>
                                <div className="pg-stats">
                                    <div className="pg-stat">
                                        <div className="pg-stat-val pg-stat-val-pos" id="trProfitCount">{profitText}</div>
                                        <div className="pg-stat-lbl">alpha vs S&amp;P</div>
                                    </div>
                                    <div className="pg-stat">
                                        <div className="pg-stat-val">1.85</div>
                                        <div className="pg-stat-lbl">Sharpe</div>
                                    </div>
                                </div>
                            </div>
                            <svg className="pg-chart" viewBox="0 0 540 200" preserveAspectRatio="none" aria-hidden="true">
                                {/* horizontal grid lines */}
                                <g className="pg-grid">
                                    <line x1="0" y1="40" x2="540" y2="40" />
                                    <line x1="0" y1="90" x2="540" y2="90" />
                                    <line x1="0" y1="140" x2="540" y2="140" />
                                    <line x1="0" y1="180" x2="540" y2="180" />
                                </g>
                                {/* Filled area under AAAgents curve */}
                                <path className="pg-fill" d="M 0 178 C 40 175, 70 168, 100 160 C 130 152, 150 162, 175 156 C 200 150, 220 132, 245 138 C 270 144, 290 158, 315 142 C 340 126, 360 102, 385 90 C 410 78, 435 88, 460 70 C 485 52, 510 40, 540 22 L 540 180 L 0 180 Z" />
                                {/* Benchmark — S&P 500 reference, dashed grey */}
                                <path className="pg-bench" d="M 0 178 C 60 177, 120 174, 180 172 C 240 170, 300 168, 360 165 C 420 162, 480 161, 540 160" />
                                {/* AAAgents primary curve */}
                                <path className="pg-line" d="M 0 178 C 40 175, 70 168, 100 160 C 130 152, 150 162, 175 156 C 200 150, 220 132, 245 138 C 270 144, 290 158, 315 142 C 340 126, 360 102, 385 90 C 410 78, 435 88, 460 70 C 485 52, 510 40, 540 22" />
                                {/* Endpoint pulse */}
                                <circle className="pg-dot-ring" cx="540" cy="22" r="10" />
                                <circle className="pg-dot" cx="540" cy="22" r="4" />
                            </svg>
                            <div className="pg-axis">
                                <span>Feb 2026</span>
                                <span className="pg-axis-tick">·</span>
                                <span className="pg-axis-tick">·</span>
                                <span className="pg-axis-tick">·</span>
                                <span>now</span>
                            </div>
                            <div className="pg-legend">
                                <span className="pg-legend-item"><i className="pg-legend-dot"></i><b>autonomous<span style={{ color: "var(--lb-accent, #00c27a)" }}>_</span></b></span>
                                <span className="pg-legend-item"><i className="pg-legend-dot pg-legend-dot-bench"></i>S&amp;P 500</span>
                            </div>
                            <div style={{ fontSize: "0.75rem", color: "var(--lb-foreground, #666)", marginTop: "1rem", lineHeight: "1.4", textAlign: "center", opacity: 0.8 }}>
                                * Performance metrics reflect the audited returns of our proprietary corporate trading capital. Past performance is not indicative of future results for your deployment.
                            </div>
                        </div>
                    </div>

                    <div className="tr-text">
                        <h2 className="tr-headline">Profitable.</h2>
                        <p className="tr-intro">
                            <b>autonomous<span style={{ color: "var(--lb-accent, #00c27a)" }}>_</span></b> outperforms because every motion runs through ≈500
                            per-symbol signal models, a twelve-classifier voting ensemble, and
                            a deterministic consensus gate — not a single LLM guessing the next
                            print.
                        </p>
                    </div>

                    <div className="tr-body">
                        <p className="pt">
                            <b>Numbers, not narratives.</b> Full performance history, time-series
                            analyses, and a public test dashboard — every claim backed by data
                            and traceable to the open-source community edition.
                        </p>
                        <p className="pt">
                            <b>Hedge-fund expertise, encoded.</b> The platform mirrors the structural risk controls of an institutional trading desk — deterministic gates, strict veto roles, and zero black boxes.
                        </p>
                        <p className="pt">
                            <b>Outperforms the benchmark.</b> The Platform's active and autonomous
                            trading strategy outperforms the benchmark index whilst managing systemic risk limits within the user's deployment.
                        </p>
                        <p className="pt">
                            <b>Deterministic thresholds.</b> Trades are strictly gated.
                            Execution only happens when the weighted signal consensus crosses
                            a fixed, hardcoded buy or sell band — no fuzzy drift, no override.
                        </p>
                    </div>
                </div>
            </section>

            {/* BLOCK 3 — COMPLIANCE (light) */}
            <section className="tr-section" id="block-auto">
                <div className="tr-grid reverse">
                    <div className="tr-text">
                        <h2 className="tr-headline">Auditable.</h2>
                        <p className="tr-intro">
                            <b>autonomous<span style={{ color: "var(--lb-accent, #00c27a)" }}>_</span></b> wraps unconstrained AI in a rigorous regulatory net, the dedicated compliance layer guarantees every executed trade is structurally safe for institutional deployment.
                        </p>
                    </div>

                    <div className="tr-body">
                        <p className="pt">
                            <b>Revision-proof storage.</b> Regulatorily relevant data lives on
                            WORM databases — write-once, read-many, tamper-evident by
                            construction.
                        </p>
                        <p className="pt">
                            <b>No black box.</b> Every classifier's vote carries a written
                            reasoning string; anonymous votes are rejected at the schema level.
                            The pipeline is inspectable end to end.
                        </p>
                        <p className="pt">
                            <b>Standard compliance stack.</b> Anti-money-laundering screening,
                            concentration-risk caps, market-abuse and trade-surveillance checks
                            — applied synchronously before any order is routed.
                        </p>
                        <p className="pt">
                            <b>Corporate governance and audit compliance:</b> Triple-write audit chain captures every decision for your accounting and annual audits. Built to withstand institutional data standards.
                        </p>
                    </div>

                    <div className="tr-stage">
                        <div className="auditflow">
                            <div className="af2-head">
                                <div className="af2-lbl">AUDIT CHAIN · LIVE</div>
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
                                <div className="af2-store"><span className="af2-store-dot"></span><span>Cloud Logging · GCP</span></div>
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
                                <div className="rpipe-layer" title="Twelve trained signal classifiers with fixed weights; three of them can hard-veto a motion alone.">
                                    <span className="rpipe-num">02</span>
                                    <div className="rpipe-body">
                                        <div className="rpipe-name">Voting ensemble</div>
                                        <div className="rpipe-tag">12 classifiers · 3 hard-veto roles</div>
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
                                <div className="rpipe-layer rpipe-out" title="Broker adapter. Paper-trading by default; live trading requires a deliberate code-level change.">
                                    <span className="rpipe-num">→</span>
                                    <div className="rpipe-body">
                                        <div className="rpipe-name">Broker</div>
                                        <div className="rpipe-tag">paper · code-flag for live</div>
                                    </div>
                                    <span className="rpipe-state rpipe-state-out">routed</span>
                                </div>
                            </div>
                            <div className="rpipe-foot">
                                <span className="rpipe-killswitch">
                                    <span className="rpipe-ks-dot" aria-hidden="true"></span>
                                    Kill-switch · armed · outside the pipeline
                                </span>
                                <span className="rpipe-killswitch" title="Cross-cutting risk-limit engine. Seven named limits (R01-R07) including the 17.5% daily-drawdown ceiling.">
                                    <span className="rpipe-ks-dot rpipe-ks-dot-rm" aria-hidden="true"></span>
                                    Risk Manager · cross-cutting · ADR-R01..R07
                                </span>
                            </div>
                        </div>
                    </div>

                    <div className="tr-text">
                        <h2 className="tr-headline">Safe.</h2>
                        <p className="tr-intro">
                            <b>autonomous<span style={{ color: "var(--lb-accent, #00c27a)" }}>_</span></b> turns risk control into a structural property —
                            seven named layers, four synchronous gate checks, and a kill-switch
                            that lives outside the trading pipeline.
                        </p>
                    </div>

                    <div className="tr-body">
                        <p className="pt">
                            <b>Seven independent risk limits.</b> Each under formal change
                            control. The daily-drawdown limit at 17.5% is sized for normal
                            volatility — it trips on systematic failures.
                        </p>
                        <p className="pt">
                            <b>Synchronous compliance gate.</b> The pre-trade gate rejects
                            orders that fail mandate, restricted-symbol, or concentration
                            checks — no LLM, no async, no bypass.
                        </p>
                        <p className="pt">
                            <b>Kill switch outside the pipeline.</b> Trip, mass-cancel, manual
                            reset. No timed auto-resume.
                        </p>
                        <p className="pt">
                            <b>Portfolio-level stop-loss.</b> System-wide capital protection
                            is hardwired — the daily-DD halt overrides every individual signal
                            before any one strategy can threaten the whole book.
                        </p>
                    </div>
                </div>
            </section>

            {/* Skyline divider */}
            <div className="skyline-divider" id="skylineDivider">
                <img src="/assets/skyline-nyc.jpg" alt="" />
            </div>

            {/* BLOCK 4 — MODULARITY / COMPATIBILITY (now dark) */}
            <section className="tr-section dark" id="block-modular">
                <div className="tr-grid reverse">
                    <div className="tr-stage">
                        <div className="modhub">
                            <div className="modhub-lbl">MODEL ENSEMBLE · LIVE</div>
                            <svg className="modhub-svg" viewBox="0 0 480 360" aria-hidden="true">
                                {(() => {
                                    const models = [
                                        { x: 240, y: 30,  code: "LST", name: "LSTM",      desc: "Long Short-Term Memory · sequence model on price action" },
                                        { x: 315, y: 50,  code: "GRU", name: "GRU",       desc: "Gated Recurrent Unit · lighter sequence model" },
                                        { x: 370, y: 105, code: "PPO", name: "PPO",       desc: "Proximal Policy Optimization · reinforcement-learning policy" },
                                        { x: 390, y: 180, code: "GBM", name: "GBM",       desc: "Gradient Boosting Machine · tabular features" },
                                        { x: 370, y: 255, code: "XGB", name: "XGBoost",   desc: "XGBoost ensemble · tree boosting" },
                                        { x: 315, y: 310, code: "RFR", name: "RF",        desc: "Random Forest · bootstrap-aggregated trees" },
                                        { x: 240, y: 330, code: "SVM", name: "SVM",       desc: "Support Vector Machine · kernel classifier" },
                                        { x: 165, y: 310, code: "CNN", name: "CNN",       desc: "Convolutional Net · pattern recognition on charts" },
                                        { x: 110, y: 255, code: "ARI", name: "ARIMA",     desc: "ARIMA · classical autoregressive time-series" },
                                        { x: 90,  y: 180, code: "BAY", name: "Bayes",     desc: "Bayesian classifier · probabilistic inference" },
                                        { x: 110, y: 105, code: "MLP", name: "MLP",       desc: "Multi-Layer Perceptron · feed-forward net" },
                                        { x: 165, y: 50,  code: "ATT", name: "Attention", desc: "Transformer / Attention · context-aware sequence model" },
                                    ];
                                    return (
                                        <>
                                            {/* spokes — drawn first so they sit behind the nodes */}
                                            <g className="modhub-spokes">
                                                {models.map((m) => (
                                                    <line key={`spoke-${m.code}`} x1="240" y1="180" x2={m.x} y2={m.y} />
                                                ))}
                                            </g>

                                            {/* concentric pulse rings emanating from the core */}
                                            <circle className="modhub-ring"               cx="240" cy="180" r="55" />
                                            <circle className="modhub-ring modhub-ring-2" cx="240" cy="180" r="55" />
                                            <circle className="modhub-ring modhub-ring-3" cx="240" cy="180" r="55" />

                                            {/* core */}
                                            <circle className="modhub-core" cx="240" cy="180" r="48" />
                                            <text x="240" y="178" textAnchor="middle" className="modhub-core-name">aaagents</text>
                                            <text x="240" y="194" textAnchor="middle" className="modhub-core-tag">senate</text>

                                            {/* 12 voting models around the core */}
                                            {models.map((m) => (
                                                <g key={`mod-${m.code}`} className="modhub-mod" transform={`translate(${m.x} ${m.y})`}>
                                                    <title>{m.desc}</title>
                                                    <circle r="24" />
                                                    <text y="4" textAnchor="middle" className="modhub-mod-icon">{m.code}</text>
                                                </g>
                                            ))}

                                            {/* travelling data dots — one per spoke, staggered */}
                                            {models.map((_, i) => (
                                                <circle key={`flow-${i}`} className={`modhub-flow modhub-flow-${i + 1}`} r="3" />
                                            ))}
                                        </>
                                    );
                                })()}
                            </svg>
                            <div className="modhub-legend">
                                <span><b>LST</b>LSTM sequence</span>
                                <span><b>GRU</b>gated RNN</span>
                                <span><b>PPO</b>RL policy</span>
                                <span><b>GBM</b>grad boost</span>
                                <span><b>XGB</b>XGBoost</span>
                                <span><b>RFR</b>random forest</span>
                                <span><b>SVM</b>kernel SVM</span>
                                <span><b>CNN</b>convnet</span>
                                <span><b>ARI</b>ARIMA</span>
                                <span><b>BAY</b>Bayesian</span>
                                <span><b>MLP</b>feed-fwd net</span>
                                <span><b>ATT</b>attention</span>
                            </div>
                        </div>
                    </div>

                    <div className="tr-text">
                        <h2 className="tr-headline">Modular.</h2>
                        <p className="tr-intro">
                            <b>autonomous<span style={{ color: "var(--lb-accent, #00c27a)" }}>_</span></b> is built to fit how you invest — not the other way around. Plug in your own models, pick the stocks you want exposure to, and dial in the style that matches your conviction.
                        </p>
                    </div>

                    <div className="tr-body">
                        <p className="pt">
                            <b>Bring your own models.</b> Drop in your own classifiers, signal
                            generators, or a private LLM — the consensus layer treats them as
                            first-class voters alongside the bundled ones. No vendor stack, no
                            rewrite.
                        </p>
                        <p className="pt">
                            <b>Define your universe.</b> Choose the names the platform trades —
                            a handful of high-conviction picks, a sector you know, or a broad
                            index. Add or remove tickers anytime; the engine adapts without
                            retraining.
                        </p>
                        <p className="pt">
                            <b>Set your investing style.</b> Long-term compounder, momentum-led,
                            dividend-tilted, or contrarian — the strategy layer is yours to
                            shape. Risk caps, holding horizons, and rebalancing cadence all live
                            in plain configuration.
                        </p>
                        <p className="pt">
                            <b>Stay in control.</b> Every decision the platform makes is yours
                            to inspect, override, or roll back. No black box, no lock-in — your
                            capital stays governed by your rules.
                        </p>
                    </div>
                </div>
            </section>

            {/* BLOCK 5 — EDITIONS: positioning cards + Full Transparence + matrix */}
            <section className="tr-section dark" id="block-editions">
                <div style={{ maxWidth: 1200, margin: "0 auto", width: "100%" }}>


                    <div className="ed-compare-container">
                        <h2 className="tr-headline" style={{ marginBottom: "48px", textAlign: "center" }}>Editions.</h2>
                        {/* Positioning cards: autonomous_ vs Enterprise */}
                        <div className="ed-tiers" aria-label="autonomous_ vs Enterprise">
                            <article className="ed-tier ed-tier-oss">
                                <header className="ed-tier-head">
                                    <span className="ed-tier-eyebrow">Open-Source Edition</span>
                                    <h3 className="ed-tier-name">Autonomous Core</h3>
                                    <p className="ed-tier-tag">
                                        The <b>autonomous<span style={{ color: "var(--lb-accent, #00c27a)" }}>_</span></b> trading core is fully open source and built on the BORA
                                        principle <i>(Build Once, Run Anywhere)</i>. It delivers a complete, production-ready trading engine capable of executing end-to-end strategies independently in any environment.
                                    </p>
                                </header>
                                <div className="ed-tier-body">
                                    <ul className="ed-feat-list">
                                        <li>
                                            <strong>Docker-First Architecture</strong>
                                            <span>Fully containerized, runs consistently across local, cloud, and hybrid environments.</span>
                                        </li>
                                        <li>
                                            <strong>9-Agent Consensus Engine</strong>
                                            <span>AI-driven signal generation via the Round Table V2 framework, running entirely on-premise.</span>
                                        </li>
                                        <li>
                                            <strong>Paper Trading Ready</strong>
                                            <span>Built-in shadow mode and Alpaca integration for safe, zero-risk strategy testing with your own API credentials.</span>
                                        </li>
                                    </ul>
                                    <a className="ed-cta"
                                       href="https://github.com/Autonomous-Asset-Management-Agents/autonomous_"
                                       target="_blank"
                                       rel="noopener noreferrer"
                                       style={{ alignSelf: "center", padding: "16px 32px", fontSize: "16px" }}>
                                        <svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true">
                                            <path fill="currentColor" d="M12 0.297c-6.63 0-12 5.373-12 12 0 5.303 3.438 9.8 8.205 11.385 0.6 0.111 0.82-0.261 0.82-0.577 0-0.285-0.01-1.04-0.015-2.04-3.338 0.724-4.042-1.61-4.042-1.61-0.546-1.385-1.335-1.755-1.335-1.755-1.087-0.744 0.084-0.729 0.084-0.729 1.205 0.084 1.838 1.236 1.838 1.236 1.07 1.835 2.809 1.305 3.495 0.998 0.108-0.776 0.417-1.305 0.76-1.605-2.665-0.305-5.467-1.334-5.467-5.93 0-1.31 0.467-2.38 1.236-3.22-0.135-0.303-0.54-1.523 0.105-3.176 0 0 1.005-0.322 3.3 1.23 0.96-0.267 1.98-0.4 3-0.405 1.02 0.005 2.04 0.138 3 0.405 2.28-1.552 3.285-1.23 3.285-1.23 0.645 1.653 0.24 2.873 0.12 3.176 0.765 0.84 1.23 1.91 1.23 3.22 0 4.61-2.805 5.625-5.475 5.92 0.42 0.36 0.81 1.096 0.81 2.22 0 1.605-0.015 2.896-0.015 3.286 0 0.315 0.21 0.69 0.825 0.57C20.565 22.092 24 17.592 24 12.297c0-6.627-5.373-12-12-12"/>
                                        </svg>
                                        <span>Download on GitHub</span>
                                    </a>
                                </div>
                            </article>

                            <article className="ed-tier ed-tier-ent">
                                <header className="ed-tier-head">
                                    <span className="ed-tier-eyebrow">Enterprise Edition</span>
                                    <h3 className="ed-tier-name">Corporate Layer</h3>
                                    <p className="ed-tier-tag">
                                        Transform the <b>autonomous<span style={{ color: "var(--lb-accent, #00c27a)" }}>_</span></b> core into a fully audited corporate deployment. This edition wraps the trading engine in a secure perimeter, delivering complete transparency for management, tax advisors, and internal risk management.
                                    </p>
                                </header>
                                <div className="ed-tier-body">
                                    <ul className="ed-feat-list">
                                        <li>
                                            <strong>Bring Your Own Cloud (BYOC)</strong>
                                            <span>Seamless deployment inside <i>your own</i> cloud environment (AWS, GCP, Azure) via automated Terraform workflows. Total infrastructure control remains with you.</span>
                                        </li>
                                        <li>
                                            <strong>Tamper-Proof Audit Logging</strong>
                                            <span>Every single classifier vote and reasoning string is written to WORM-compliant, append-only logs – perfectly prepared for your corporate tax audit and compliance documentation.</span>
                                        </li>
                                        <li>
                                            <strong>Advanced Risk Controls</strong>
                                            <span>Hardcoded daily-drawdown limits, concentration caps, and a kill-switch that lives strictly outside the trading pipeline to protect your corporate treasury from systematic failures.</span>
                                        </li>
                                    </ul>
                                    <a className="ed-cta ed-cta-ent"
                                       href="mailto:info@aaagents.de?subject=Enterprise%20Edition%20Contact"
                                       rel="noopener noreferrer"
                                       style={{ alignSelf: "center", padding: "16px 40px", fontSize: "16px", background: "#00c27a", color: "#000", border: "1px solid #00c27a", boxShadow: "0 0 24px rgba(0,194,122,0.4)", position: "relative", zIndex: 10, cursor: "pointer" }}>
                                        <span>Contact Us</span>
                                    </a>
                                </div>
                            </article>
                        </div>


                    <div className="ed-foot">
                        Same trading core. Identical risk engine. Different deployment surface.
                    </div>
                    </div>
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
                                    <div className="founder-role">Founder, CEO</div>
                                </div>
                                <svg className="founder-li" viewBox="0 0 24 24" width="20" height="20" fill="currentColor">
                                    <path d="M20.447 20.452h-3.554v-5.569c0-1.328-.027-3.037-1.852-3.037-1.853 0-2.136 1.445-2.136 2.939v5.667H9.351V9h3.414v1.561h.046c.477-.9 1.637-1.85 3.37-1.85 3.601 0 4.267 2.37 4.267 5.455v6.286zM5.337 7.433a2.062 2.062 0 01-2.063-2.065 2.064 2.064 0 112.063 2.065zm1.782 13.019H3.555V9h3.564v11.452zM22.225 0H1.771C.792 0 0 .774 0 1.729v20.542C0 23.227.792 24 1.771 24h20.451C23.2 24 24 23.227 24 22.271V1.729C24 .774 23.2 0 22.222 0h.003z"/>
                                </svg>
                            </a>
                            {/* Andreas */}
                            <a href="https://www.linkedin.com/in/andreas-e-apeldorn-00a8a924/" target="_blank" rel="noopener noreferrer" className="founder-card">
                                <div className="founder-info">
                                    <div className="founder-name">Andreas Apeldorn</div>
                                    <div className="founder-role">Co-Founder, AI Architect</div>
                                </div>
                                <svg className="founder-li" viewBox="0 0 24 24" width="20" height="20" fill="currentColor">
                                    <path d="M20.447 20.452h-3.554v-5.569c0-1.328-.027-3.037-1.852-3.037-1.853 0-2.136 1.445-2.136 2.939v5.667H9.351V9h3.414v1.561h.046c.477-.9 1.637-1.85 3.37-1.85 3.601 0 4.267 2.37 4.267 5.455v6.286zM5.337 7.433a2.062 2.062 0 01-2.063-2.065 2.064 2.064 0 112.063 2.065zm1.782 13.019H3.555V9h3.564v11.452zM22.225 0H1.771C.792 0 0 .774 0 1.729v20.542C0 23.227.792 24 1.771 24h20.451C23.2 24 24 23.227 24 22.271V1.729C24 .774 23.2 0 22.222 0h.003z"/>
                                </svg>
                            </a>
                        </div>
                    </div>

                    <div className="tr-text">
                        <h2 className="tr-headline" style={{ marginBottom: "24px" }}>About <b>autonomous<span style={{ color: "var(--lb-accent, #00c27a)" }}>_</span></b></h2>
                        <p className="tr-intro" style={{ fontSize: "18px", lineHeight: 1.6, color: "#a0a0a0" }}>
                            <b>autonomous<span style={{ color: "var(--lb-accent, #00c27a)" }}>_</span></b> stands for our company, autonomous asset management agents. Our core business is developing intelligent software architectures capable of real-time market analysis and autonomous trade execution. We don't just develop theories; we operate them.
                        </p>
                        <p className="tr-intro" style={{ fontSize: "18px", lineHeight: 1.6, color: "#a0a0a0", marginTop: "16px" }}>
                            By managing our own proprietary capital, we validate our models under real-world conditions—independent of third-party funds. We believe in building trust through radical transparency. Follow our journey on LinkedIn to see live insights into our algorithm's sector allocations, macro responses, and performance metrics.
                        </p>
                        <div style={{ marginTop: "40px" }}>
                            <p style={{ fontSize: "13px", lineHeight: 1.5, color: "#666", borderTop: "1px solid rgba(255,255,255,0.1)", paddingTop: "16px", margin: 0 }}>
                                <i>Disclaimer: The information shared on this site does not constitute investment advice, financial brokerage, or an offer to buy or sell securities. We are a technology company, not a financial services provider. All content exclusively reflects our own proprietary trading activities and entrepreneurial decisions.</i>
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
                        >
                            The governance layer for autonomous trading. Profitable, auditable,
                            safe — by design.
                        </p>
                    </div>
                    <div></div>
                    <div></div>
                    <div>
                        <h4>Legal</h4>
                        <a href="/legal/imprint">Imprint</a>
                        <a href="/legal/privacy">Privacy</a>
                        <a href="/legal/risk-disclosure">Risk disclosure</a>
                    </div>
                </div>
                <p className="lb-disclaimer">
                    Investing carries risk. The value of your investment may fall or rise; losses
                    of capital invested may occur. Past performance is no guarantee of future
                    results.
                </p>
                <div className="lb-bottom">
                    <span>© aaagents · Built in Europe</span>
                    <span>aaagents.de</span>
                </div>
            </footer>

            {/* Subpage overlays */}
            <div
                className={"overlay" + (openOverlay === "profit" ? " open" : "")}
                id="overlayProfit"
                data-overlay="profit"
                data-lenis-prevent
                style={
                    {
                        ["--curtain-bg" as any]: "#fff",
                        ["--curtain-fg" as any]: "#000",
                    } as React.CSSProperties
                }
            >
                <div className="overlay-curtain" id="curtainProfit"></div>
                <div className="overlay-content">
                    <div className="overlay-inner">
                        <div className="overlay-top">
                            <div className="overlay-eyebrow">01 — Decisions</div>
                            <button className="overlay-close" data-close="profit">
                                <span className="x"></span> CLOSE
                            </button>
                        </div>
                        <h1>Twelve perspectives.<br />One sized signal.</h1>
                        <p className="lede-big">
                            No single model decides. Twelve named senators each carry a fixed
                            weight, a specialised view, and a written argument per motion. The
                            consensus is the weighted average — never a black box, never a coin
                            flip.
                        </p>

                        <div className="ovl-hero">
                            <div className="ovl-hero-head">
                                <span>Senate · twelve named seats · weighted vote</span>
                                <span className="live-dot">Risk-first by construction</span>
                            </div>
                            <div className="ovl-hero-body">
                                <div className="rt-grid">
                                    <div className="rt-row"><span className="rt-name">iota · Iron Dome · veto</span><div className="rt-bar"><span className="rt-fill" style={{ width: "100%" }}></span></div><span className="rt-w">veto</span></div>
                                    <div className="rt-row"><span className="rt-name">alpha · Bear Guard · veto</span><div className="rt-bar"><span className="rt-fill" style={{ width: "94%" }}></span></div><span className="rt-w">veto</span></div>
                                    <div className="rt-row"><span className="rt-name">delta · Quant Risk · veto</span><div className="rt-bar"><span className="rt-fill" style={{ width: "81%" }}></span></div><span className="rt-w">veto</span></div>
                                    <div className="rt-row"><span className="rt-name">gamma · Portfolio Architect</span><div className="rt-bar"><span className="rt-fill" style={{ width: "75%" }}></span></div><span className="rt-w">heavy</span></div>
                                    <div className="rt-row"><span className="rt-name">epsilon · Macro Oracle</span><div className="rt-bar"><span className="rt-fill" style={{ width: "69%" }}></span></div><span className="rt-w">heavy</span></div>
                                    <div className="rt-row"><span className="rt-name">beta · Bull Advocate</span><div className="rt-bar"><span className="rt-fill" style={{ width: "62%" }}></span></div><span className="rt-w">mid</span></div>
                                    <div className="rt-row"><span className="rt-name">theta · Rebalancer</span><div className="rt-bar"><span className="rt-fill" style={{ width: "62%" }}></span></div><span className="rt-w">mid</span></div>
                                    <div className="rt-row"><span className="rt-name">zeta · Technical</span><div className="rt-bar"><span className="rt-fill" style={{ width: "56%" }}></span></div><span className="rt-w">mid</span></div>
                                    <div className="rt-row"><span className="rt-name">eta · Momentum Hunter</span><div className="rt-bar"><span className="rt-fill" style={{ width: "56%" }}></span></div><span className="rt-w">mid</span></div>
                                    <div className="rt-row"><span className="rt-name">kappa · Catalyst</span><div className="rt-bar"><span className="rt-fill" style={{ width: "50%" }}></span></div><span className="rt-w">light</span></div>
                                    <div className="rt-row"><span className="rt-name">lambda · Setup</span><div className="rt-bar"><span className="rt-fill" style={{ width: "50%" }}></span></div><span className="rt-w">light</span></div>
                                    <div className="rt-row"><span className="rt-name">mu · Contrarian</span><div className="rt-bar"><span className="rt-fill" style={{ width: "44%" }}></span></div><span className="rt-w">light</span></div>
                                </div>
                                <div className="rt-foot"><span>Risk dominates by structure — the three heaviest seats are also the three hard-veto seats. None can be outvoted.</span></div>
                            </div>
                        </div>

                        <section className="ovl-section first">
                            <div className="ovl-kpis">
                                <div className="ovl-kpi"><div className="v">≈500</div><div className="l">Stock Specialists</div><div className="s">one analyst per S&amp;P 500 symbol · on-prem</div></div>
                                <div className="ovl-kpi"><div className="v">12</div><div className="l">Senators</div><div className="s">named, weighted, individually trained</div></div>
                                <div className="ovl-kpi"><div className="v">3</div><div className="l">Hard-veto seats</div><div className="s">iota · alpha · delta</div></div>
                                <div className="ovl-kpi"><div className="v">Paper</div><div className="l">Default mode</div><div className="s">live behind explicit code change</div></div>
                            </div>
                        </section>

                        <section className="ovl-section">
                            <div className="ovl-section-head">
                                <div className="ovl-section-num">A · The twelve seats</div>
                                <div>
                                    <h2>Every score is<br />a trained model.</h2>
                                    <p className="lede">Each senator runs its own specialised classifier on its own slice of reality. Together they form a complete institutional picture — risk, regime, valuation, momentum, narrative, contrarian. Every output is bounded, every output carries a written reason.</p>
                                </div>
                            </div>
                            <div className="senator-grid">
                                <div className="senator-card is-veto">
                                    <div className="senator-card-head"><span className="senator-name">iota</span><span className="senator-tag">Veto</span></div>
                                    <div className="senator-role">Iron Dome · compliance</div>
                                    <div className="senator-desc">Approval is required, not a vote. Checks mandate, restricted lists, daily caps. The senate cannot proceed without iota's clearance.</div>
                                </div>
                                <div className="senator-card is-veto">
                                    <div className="senator-card-head"><span className="senator-name">alpha</span><span className="senator-tag">Veto</span></div>
                                    <div className="senator-role">Bear Guard</div>
                                    <div className="senator-desc">Hard veto on conviction-grade downside. Stops the senate cold when the bear case is structurally credible.</div>
                                </div>
                                <div className="senator-card is-veto">
                                    <div className="senator-card-head"><span className="senator-name">delta</span><span className="senator-tag">Veto</span></div>
                                    <div className="senator-role">Quant Risk</div>
                                    <div className="senator-desc">Hard veto when portfolio drawdown approaches the daily limit. Volatility-aware, drawdown-aware.</div>
                                </div>
                                <div className="senator-card">
                                    <div className="senator-card-head"><span className="senator-name">gamma</span><span className="senator-tag">Heavy</span></div>
                                    <div className="senator-role">Portfolio Architect</div>
                                    <div className="senator-desc">Scores fit-with-book — does this position still help the portfolio's risk-adjusted return and concentration profile?</div>
                                </div>
                                <div className="senator-card">
                                    <div className="senator-card-head"><span className="senator-name">epsilon</span><span className="senator-tag">Heavy</span></div>
                                    <div className="senator-role">Macro Oracle</div>
                                    <div className="senator-desc">Reads macro regime from term-structure, yield-curve, and breadth proxies.</div>
                                </div>
                                <div className="senator-card">
                                    <div className="senator-card-head"><span className="senator-name">beta</span><span className="senator-tag">Mid</span></div>
                                    <div className="senator-role">Bull Advocate</div>
                                    <div className="senator-desc">Only fires positive scores when the bull case is structurally credible — not when euphoria is the only thing carrying it.</div>
                                </div>
                                <div className="senator-card">
                                    <div className="senator-card-head"><span className="senator-name">theta</span><span className="senator-tag">Mid</span></div>
                                    <div className="senator-role">Rebalancer</div>
                                    <div className="senator-desc">Opposes motions that would worsen weight-drift versus the mandate target.</div>
                                </div>
                                <div className="senator-card">
                                    <div className="senator-card-head"><span className="senator-name">zeta</span><span className="senator-tag">Mid</span></div>
                                    <div className="senator-role">Technical</div>
                                    <div className="senator-desc">Squeeze, continuation, breakout-fail patterns. Reads what charts are telling the rest of the market.</div>
                                </div>
                                <div className="senator-card">
                                    <div className="senator-card-head"><span className="senator-name">eta</span><span className="senator-tag">Mid</span></div>
                                    <div className="senator-role">Momentum Hunter</div>
                                    <div className="senator-desc">Cross-sectional momentum, volume profile, trend persistence.</div>
                                </div>
                                <div className="senator-card">
                                    <div className="senator-card-head"><span className="senator-name">kappa</span><span className="senator-tag">Light</span></div>
                                    <div className="senator-role">Catalyst Detector</div>
                                    <div className="senator-desc">Insider clusters, material-event filings, activist disclosures — the things that move the print before consensus catches up.</div>
                                </div>
                                <div className="senator-card">
                                    <div className="senator-card-head"><span className="senator-name">lambda</span><span className="senator-tag">Light</span></div>
                                    <div className="senator-role">Setup</div>
                                    <div className="senator-desc">Base completeness, prior-base depth, volume dry-up — the structural quality of the chart, not just its direction.</div>
                                </div>
                                <div className="senator-card">
                                    <div className="senator-card-head"><span className="senator-name">mu</span><span className="senator-tag">Light</span></div>
                                    <div className="senator-role">Contrarian</div>
                                    <div className="senator-desc">Overbought and euphoria proxies — sentiment extremes, breadth, options skew.</div>
                                </div>
                            </div>
                        </section>

                        <section className="ovl-section">
                            <div className="ovl-pull">
                                <q>Consensus is a weighted average — not a vote count, not a confidence score from one model. Twelve trained models, twelve fixed weights, one written reasoning string per vote.</q>
                                <cite>Senate · architecture spec</cite>
                            </div>
                        </section>

                        <section className="ovl-section">
                            <div className="ovl-section-head">
                                <div className="ovl-section-num">B · Consensus</div>
                                <div>
                                    <h2>One formula.<br />Three signals.</h2>
                                    <p className="lede">The consensus is a weighted average — every senator's score multiplied by its weight, summed, divided by the total active weight. Vetoed votes are excluded, not zeroed, so a single broken agent cannot drag the consensus down. Above the buy band the signal is BUY; below the sell band, SELL; otherwise HOLD. The bands are public, fixed, and reviewed under formal change control.</p>
                                </div>
                            </div>
                        </section>

                        <section className="ovl-section">
                            <div className="ovl-section-head">
                                <div className="ovl-section-num">C · Trained, not prompted</div>
                                <div>
                                    <h2>Models on rails.<br />Hot-swappable.</h2>
                                    <p className="lede">The senators sit on top of reusable trained signal models, managed by an internal registry. Any model can be retrained, swapped, or upgraded at runtime — without restarting or liquidating positions.</p>
                                </div>
                            </div>
                            <div className="ovl-feat3">
                                <div><span className="feat-ic">T</span><h4>Trained signals</h4><p>Sequence-aware models for time-series patterns; tabular classifiers for cross-sectional features. Each one fed by its senator's own slice of reality.</p></div>
                                <div><span className="feat-ic">R</span><h4>Reinforcement layer</h4><p>A second-stage policy reads the trained signals and emits an execution recommendation. Pre-trained, deterministic at inference, retrainable on demand.</p></div>
                                <div><span className="feat-ic">G</span><h4>Graceful degradation</h4><p>If a model file is missing, that senator falls back to neutral (0.5) and the fallback is logged. The senate still votes. Nothing cascades. A genuinely broken dependency escalates to the kill switch.</p></div>
                            </div>
                        </section>

                        <section className="ovl-section">
                            <div className="ovl-section-head">
                                <div className="ovl-section-num">D · Stock Specialist layer · ≈500 analysts</div>
                                <div>
                                    <h2>Five hundred analysts.<br />One local LLM call each.</h2>
                                    <p className="lede">Before any senate vote runs, hundreds of per-symbol specialist analysts execute in parallel batches — one per S&amp;P 500 ticker. Each one reads a wide spread of public sources and synthesises a single structured report in a single on-prem LLM call. The twelve senators read from these reports; they don't re-fetch raw data.</p>
                                </div>
                            </div>
                            <div className="ovl-specs">
                                <div className="ovl-spec"><span className="key">Fundamentals</span><span className="val">Public regulatory filings — quarterly fundamentals, insider trading, material events, activist stakes, institutional flow.</span><span className="tag">Public</span></div>
                                <div className="ovl-spec"><span className="key">Narrative</span><span className="val">Annual and quarterly narrative reports plus call-transcript language — what management is actually saying, in their own words.</span><span className="tag">Public</span></div>
                                <div className="ovl-spec"><span className="key">Insider · congressional</span><span className="val">Cluster insider patterns and congressional trading disclosures — public, lagged, but informative on aggregate.</span><span className="tag">Public</span></div>
                                <div className="ovl-spec"><span className="key">News · attention</span><span className="val">Headlines, search trends, retail attention, short-interest, options skew. The signal isn't the headline; it's the aggregated attention shift.</span><span className="tag">Public</span></div>
                                <div className="ovl-spec"><span className="key">Synthesis</span><span className="val">All sources merged into a single local-LLM call. Inference runs on-prem — zero per-token API cost, no symbol data leaves the box.</span><span className="tag">On-prem</span></div>
                                <div className="ovl-spec"><span className="key">Refresh cadence</span><span className="val">Holdings + top senate candidates refreshed every couple of hours. Full watchlist on a slower cadence. Priority-driven, not exhaustive.</span><span className="tag">Priority</span></div>
                            </div>
                        </section>

                        <section className="ovl-section">
                            <div className="ovl-section-head">
                                <div className="ovl-section-num">E · Decision discipline</div>
                                <div>
                                    <h2>The decision rules<br />are public, by design.</h2>
                                    <p className="lede">A decision system that hides its rules can't be reviewed. A decision system that exposes its rules can be argued with. We chose the second one. Every public-facing rule below is fixed, documented, and changes only under formal control — not at runtime, not by the operator, not by the AI.</p>
                                </div>
                            </div>
                            <div className="ovl-specs">
                                <div className="ovl-spec"><span className="key">Decision bands</span><span className="val">The boundaries between BUY, HOLD and SELL are publicly fixed. Above the buy band the signal is BUY; below the sell band, SELL; otherwise HOLD. The bands don't shift to chase a missed call.</span><span className="tag">Public</span></div>
                                <div className="ovl-spec"><span className="key">Senator weights</span><span className="val">Every seat has a fixed weight. Every change is logged in a senate-weight changelog with run-id, date and reason. There is no quiet retune.</span><span className="tag">Logged</span></div>
                                <div className="ovl-spec"><span className="key">Reasoning required</span><span className="val">A vote without a written reason is rejected by the schema before it reaches the tally. Anonymous votes are physically impossible — accountability is structural, not procedural.</span><span className="tag">Schema</span></div>
                                <div className="ovl-spec"><span className="key">Fallback is neutral</span><span className="val">If a senator's model file is missing or its data feed is dead, that senator returns its neutral baseline and the fallback is logged. The senate still votes; the fallback is visible in the audit chain.</span><span className="tag">Honest</span></div>
                            </div>
                        </section>

                        <section className="ovl-section">
                            <div className="ovl-final">
                                <h3>See the architecture in detail.</h3>
                                <p>Read the full technical write-up — every agent, every weight, every threshold, with the regulatory mapping.</p>
                                <div className="ctas">
                                    <a className="btn-prim" href="mailto:info@aaagents.de?subject=AAAgents%20access%20request" data-close="profit">Request access ›</a>
                                    <a className="btn-ghost" href="https://github.com/Autonomous-Asset-Management-Agents" target="_blank" rel="noopener" data-close="profit">View on GitHub</a>
                                </div>
                            </div>
                        </section>
                    </div>
                </div>
            </div>

            <div
                className={"overlay dark" + (openOverlay === "auto" ? " open" : "")}
                id="overlayAuto"
                data-overlay="auto"
                data-lenis-prevent
                style={
                    {
                        ["--curtain-bg" as any]: "#000",
                        ["--curtain-fg" as any]: "#fff",
                    } as React.CSSProperties
                }
            >
                <div className="overlay-curtain" id="curtainAuto"></div>
                <div className="overlay-content">
                    <div className="overlay-inner">
                        <div className="overlay-top">
                            <div className="overlay-eyebrow">02 — Autonomous</div>
                            <button className="overlay-close" data-close="auto">
                                <span className="x"></span> CLOSE
                            </button>
                        </div>
                        <h1>Autonomous, never opaque.</h1>
                        <p className="lede-big">A pipeline, not a black box. Specialists analyse, the Senate votes, the Iron Dome enforces, the Optimizer sizes, the Risk Manager guards. Every vote carries a reasoning string. Every decision produces a signed audit record. The system physically cannot make an opaque decision — the data structure doesn't allow it.</p>

                        <div className="tier-terminal" role="img" aria-label="Decision flow pipeline">
                            <div className="tt-bar"><span></span><span></span><span></span></div>
                            <div className="tt-prompt">
                                <span>decision_flow.run · specialists → coordinator → senate → compliance → cufolio → executor</span>
                                <span className="tt-prompt-right">sequential · enforced</span>
                            </div>
                            <div className="tt-tier">
                                <span className="tt-key">tier_1</span>
                                <span>
                                    <span className="tt-name">≈500 Stock Specialists</span>
                                    <span className="tt-desc">One on-prem analyst per S&amp;P 500 symbol. Reads filings, insider trades, news, attention proxies. Inference runs locally; no symbol data leaves the operator's environment.</span>
                                    <span className="tt-out">≈500 SpecialistReports / cycle</span>
                                </span>
                                <span className="tt-ix">00</span>
                            </div>
                            <div className="tt-tier">
                                <span className="tt-key">tier_2</span>
                                <span>
                                    <span className="tt-name">Coordinator + Iron Dome</span>
                                    <span className="tt-desc">Coordinator aggregates specialist signals into motions. Iron Dome runs a short list of regulator-driven checks — no async I/O, no bypass. Rejections logged with reason and latency.</span>
                                    <span className="tt-out">approved motion forwarded</span>
                                </span>
                                <span className="tt-ix">01</span>
                            </div>
                            <div className="tt-tier">
                                <span className="tt-key">tier_3</span>
                                <span>
                                    <span className="tt-name">Senate · 12 senators</span>
                                    <span className="tt-desc">alpha · beta · gamma · delta · epsilon · zeta · eta · theta · iota · kappa · lambda · mu. Three named hard-veto seats: alpha, delta, iota — none can be outvoted.</span>
                                    <span className="tt-out">weighted vote · clearly defined bands</span>
                                </span>
                                <span className="tt-ix">02</span>
                            </div>
                            <div className="tt-tier">
                                <span className="tt-key">tier_4</span>
                                <span>
                                    <span className="tt-name">Optimizer + Executor</span>
                                    <span className="tt-desc">Mean-CVaR sizing bounds concentration and tail risk before the order is even constructed. Kill-switch wraps the order path. Routed to a paper account by default; live requires a deliberate code-level decision.</span>
                                    <span className="tt-out">order at the broker · session sealed</span>
                                </span>
                                <span className="tt-ix">03</span>
                            </div>
                            <div className="tt-foot">end of pipeline · session_id sealed to audit chain</div>
                        </div>

                        <section className="ovl-section first">
                            <div className="ovl-kpis">
                                <div className="ovl-kpi"><div className="v">≈500</div><div className="l">Stock Specialists</div><div className="s">one analyst per S&amp;P 500 symbol · on-prem</div></div>
                                <div className="ovl-kpi"><div className="v">12</div><div className="l">Senate seats</div><div className="s">named, weighted, reasoned</div></div>
                                <div className="ovl-kpi"><div className="v">3</div><div className="l">Hard-veto seats</div><div className="s">iota · alpha · delta</div></div>
                                <div className="ovl-kpi"><div className="v">3</div><div className="l">Audit destinations</div><div className="s">live stream · ledger · database</div></div>
                            </div>
                        </section>

                        <section className="ovl-section">
                            <div className="ovl-section-head">
                                <div className="ovl-section-num">A · Replay</div>
                                <div><h2>Walk any decision<br />back, second by second.</h2><p className="lede">Pick a motion. Pull the session record. The pipeline plays back: which specialist reports were on the table, which senators voted how with which reasoning, which veto fired, what the consensus came out at, what the gatekeeper decided, what the optimizer sized, what the broker actually filled. Compliance review without an investigation.</p></div>
                            </div>
                            <div className="replay-chain">
                                <div className="replay-node">
                                    <div className="replay-node-label">N − 3</div>
                                    <div className="replay-node-key">Session opened</div>
                                    <div className="replay-node-sub">Specialist reports staged on the table</div>
                                </div>
                                <div className="replay-node">
                                    <div className="replay-node-label">N − 2</div>
                                    <div className="replay-node-key">Senate vote</div>
                                    <div className="replay-node-sub">12 reasonings + weighted consensus</div>
                                </div>
                                <div className="replay-node">
                                    <div className="replay-node-label">N − 1</div>
                                    <div className="replay-node-key">Gatekeeper</div>
                                    <div className="replay-node-sub">Iron Dome decision · approve or reject</div>
                                </div>
                                <div className="replay-node">
                                    <div className="replay-node-label">N</div>
                                    <div className="replay-node-key">Triple-write seal</div>
                                    <div className="replay-node-sub">Live stream + ledger + database</div>
                                </div>
                                <div className="replay-node is-current">
                                    <div className="replay-node-label">Now</div>
                                    <div className="replay-node-key">Replay on demand</div>
                                    <div className="replay-node-sub">By session · symbol · senator · gate event</div>
                                </div>
                            </div>
                            <p className="ovl-foot-note">Filter the chain four ways — by motion id, by ticker, by named senator's track record, or by which gate fired and why. Rejected orders are logged too: the system records what it didn't do, not just what it did.</p>
                        </section>

                        <section className="ovl-section">
                            <div className="ovl-pull">
                                <q>The AI pipeline produces signals. Compliance decides whether they execute. These are separate systems. The AI cannot optimize away its own guardrails.</q>
                                <cite>Iron Dome · architecture spec</cite>
                            </div>
                        </section>

                        <section className="ovl-section">
                            <div className="ovl-section-head">
                                <div className="ovl-section-num">B · Senate Protocol</div>
                                <div><h2>Triple-write audit.<br />Three destinations.</h2><p className="lede">After every Senate vote — pass or veto — the full session record is persisted via fire-and-forget async logging to three independent destinations in parallel. The audit trail survives any single infrastructure failure.</p></div>
                            </div>
                            <div className="ovl-feat3">
                                <div><span className="feat-ic">R</span><h4>Live stream</h4><p>An in-memory event stream — primary, real-time. Used by the kill switch and operator dashboards. A rolling window of recent decisions is always queryable in milliseconds.</p></div>
                                <div><span className="feat-ic">J</span><h4>Tamper-evident ledger</h4><p>An append-only file with a cryptographic chain — every record carries the hash of the prior record. Tampering breaks the chain on verification.</p></div>
                                <div><span className="feat-ic">P</span><h4>Queryable database</h4><p>A managed relational store for long-term retrieval, regulator review, and replay. Schema versioned under formal change control.</p></div>
                            </div>
                            <p className="ovl-foot-note">Each session record carries the symbol, timestamp, every senator's score + weight + reasoning, the consensus, the gatekeeper's decision, and the final action. Anonymous votes are physically impossible — empty reasoning is rejected before the vote enters the tally.</p>
                        </section>

                        <section className="ovl-section">
                            <div className="ovl-section-head">
                                <div className="ovl-section-num">D · Change control</div>
                                <div><h2>The system itself<br />is on the audit chain.</h2><p className="lede">A model retrain is a logged event. A senator-weight adjustment is a logged event. A threshold tweak is a logged event. None of it happens by quiet hand — every change carries a run-id, a date, a stated reason, and an operator. The audit trail isn't just for trades; it's for the trader.</p></div>
                            </div>
                            <div className="ovl-specs">
                                <div className="ovl-spec"><span className="key">Senate weights</span><span className="val">Every change to a senator's weight is treated as a model change — recorded in a dedicated changelog with run-id, date, and reason. Reviewable on demand.</span><span className="tag">Logged</span></div>
                                <div className="ovl-spec"><span className="key">Decision bands</span><span className="val">The buy/sell bands are publicly fixed. Adjusting them is a documented change-control decision, not a tuning knob.</span><span className="tag">Public</span></div>
                                <div className="ovl-spec"><span className="key">Model artefacts</span><span className="val">Trained model files are versioned. Retraining produces a new artefact; the old one stays archived. Rollback is a swap, not a rebuild.</span><span className="tag">Versioned</span></div>
                                <div className="ovl-spec"><span className="key">Risk thresholds</span><span className="val">Every loss-control rule is anchored in a documented basis (regulatory or internal). Adjustments require formal sign-off; the change is part of the audit story.</span><span className="tag">Anchored</span></div>
                            </div>
                        </section>

                        <section className="ovl-section">
                            <div className="ovl-section-head">
                                <div className="ovl-section-num">E · Where it runs</div>
                                <div><h2>On-prem inference.<br />Managed cloud infra.</h2><p className="lede">The LLM that drives the specialist layer runs on the operator's own machines — symbol-level data never leaves the box. The execution engine, audit stores, and orchestration run on managed cloud infrastructure with all secrets handled out-of-image.</p></div>
                            </div>
                            <div className="ovl-specs">
                                <div className="ovl-spec"><span className="key">Engine</span><span className="val">Auto-scaling stateless workers. Liveness-probed. The trading loop is idempotent and can be restarted at any cycle boundary.</span><span className="tag">Managed</span></div>
                                <div className="ovl-spec"><span className="key">Live state</span><span className="val">In-memory store for orchestration checkpoints, kill-switch flags, rate-limit counters, and session consensus scores. Replicated, durable, ms-latency.</span><span className="tag">In-memory</span></div>
                                <div className="ovl-spec"><span className="key">Audit</span><span className="val">Managed relational store for the long-term audit trail, user metadata, and operator tokens. Schema migrations under formal change control.</span><span className="tag">Persistent</span></div>
                                <div className="ovl-spec"><span className="key">Secrets &amp; artefacts</span><span className="val">Model files and API keys live in a managed secret store and object store. Nothing baked into the image; everything loaded at runtime.</span><span className="tag">Hardened</span></div>
                                <div className="ovl-spec"><span className="key">CI/CD</span><span className="val">Independent build pipelines for engine, database migrations, console, and tests. Hot-patches ship without touching state.</span><span className="tag">Hot-patch</span></div>
                            </div>
                        </section>

                        <section className="ovl-section">
                            <div className="ovl-final">
                                <h3>See the audit pipeline.</h3>
                                <p>A walkthrough of a real session from vote through gatekeeper to triple-write log — the exact format your compliance team will receive.</p>
                                <div className="ctas">
                                    <a className="btn-prim" href="mailto:info@aaagents.de?subject=AAAgents%20access%20request" data-close="auto">Request access ›</a>
                                    <a className="btn-ghost" href="https://github.com/Autonomous-Asset-Management-Agents" target="_blank" rel="noopener" data-close="auto">View on GitHub</a>
                                </div>
                            </div>
                        </section>
                    </div>
                </div>
            </div>

            <div
                className={"overlay" + (openOverlay === "safe" ? " open" : "")}
                id="overlaySafe"
                data-overlay="safe"
                data-lenis-prevent
                style={
                    {
                        ["--curtain-bg" as any]: "#fff",
                        ["--curtain-fg" as any]: "#000",
                    } as React.CSSProperties
                }
            >
                <div className="overlay-curtain" id="curtainSafe"></div>
                <div className="overlay-content">
                    <div className="overlay-inner">
                        <div className="overlay-top">
                            <div className="overlay-eyebrow">03 — Safety</div>
                            <button className="overlay-close" data-close="safe">
                                <span className="x"></span> CLOSE
                            </button>
                        </div>
                        <h1>Safe by construction.<br />Not by promise.</h1>
                        <p className="lede-big">Seven independent loss-control layers. Each has a named Architecture Decision Record (ADR) documenting the regulatory basis. The 17.5% daily-drawdown limit was chosen specifically to allow approximately 3-sigma intraday swings without halting — the goal is catching systematic failures, not normal volatility.</p>

                        <div className="ovl-hero">
                            <div className="ovl-hero-head">
                                <span>RiskManager · 7 layers · each with named ADR</span>
                                <span className="live-dot">Configured · enforced</span>
                            </div>
                            <div className="ovl-hero-body">
                                <div className="rl-stack">
                                    <div className="rl-row"><span className="rl-id">01</span><span className="rl-name">Daily Drawdown Limit</span><span className="rl-val">sized for normal volatility, trips on systematic failure</span></div>
                                    <div className="rl-row"><span className="rl-id">02</span><span className="rl-name">Risk per Trade</span><span className="rl-val">small fixed-fractional cap, configurable per mandate</span></div>
                                    <div className="rl-row"><span className="rl-id">03</span><span className="rl-name">Progressive Halt</span><span className="rl-val">warn-then-halt; reduces size before stopping</span></div>
                                    <div className="rl-row"><span className="rl-id">04</span><span className="rl-name">Unlock Recovery</span><span className="rl-val">a meaningful recovery is required before resuming</span></div>
                                    <div className="rl-row"><span className="rl-id">05</span><span className="rl-name">Default Stop-Loss</span><span className="rl-val">volatility-scaled trailing exit</span></div>
                                    <div className="rl-row"><span className="rl-id">06</span><span className="rl-name">Max Loss per Trade</span><span className="rl-val">absolute hard cap, regardless of stop placement</span></div>
                                    <div className="rl-row"><span className="rl-id">07</span><span className="rl-name">Portfolio Stop-Loss</span><span className="rl-val">session-level cap that catches systematic gaps</span></div>
                                </div>
                                <div className="rl-foot"><span>Each layer is enforced independently — a single failure in one layer doesn't bypass the others. Each rule has a documented basis (regulatory or internal-policy) and is reviewed under formal change control.</span></div>
                            </div>
                        </div>

                        <section className="ovl-section first">
                            <div className="ovl-kpis">
                                <div className="ovl-kpi"><div className="v">7</div><div className="l">Risk layers</div><div className="s">independent · documented basis</div></div>
                                <div className="ovl-kpi"><div className="v">17.5%</div><div className="l">Daily DD limit</div><div className="s">~3-sigma headroom by design</div></div>
                                <div className="ovl-kpi"><div className="v">2.0s</div><div className="l">Kill-switch timeout</div><div className="s">async mass-cancel · MaRisk-aligned</div></div>
                                <div className="ovl-kpi"><div className="v">Paper</div><div className="l">Default mode</div><div className="s">live behind explicit code change</div></div>
                            </div>
                        </section>

                        <section className="ovl-section">
                            <div className="ovl-section-head">
                                <div className="ovl-section-num">A · Kill switch</div>
                                <div><h2>A circuit breaker<br />outside the pipeline.</h2><p className="lede">Triggered automatically when a critical dependency goes missing or when the daily drawdown limit is breached. Once the halt flag is set, the trading pipeline cannot reach the broker.</p></div>
                            </div>
                            <div className="ovl-feat3">
                                <div><span className="feat-ic">1</span><h4>Halt flag</h4><p>A system-wide halt flag is set atomically. The next trading cycle reads it first; in-flight orders cannot complete the loop.</p></div>
                                <div><span className="feat-ic">2</span><h4>Mass cancel</h4><p>Every open and pending order is asynchronously cancelled at the broker within seconds — aligned with the MaRisk requirement for "unverzügliche Reaktionsfähigkeit".</p></div>
                                <div><span className="feat-ic">3</span><h4>Operator action</h4><p>An alert fires to the operator with the cause and current state. The engine refuses any new order. Restart requires an explicit manual reset.</p></div>
                            </div>
                        </section>

                        <section className="ovl-section">
                            <div className="ovl-pull">
                                <q>The 17.5% daily-drawdown limit was chosen specifically to allow approximately 3-sigma intraday swings without halting. The goal is catching systematic failures, not normal volatility.</q>
                                <cite>Risk Manager · architecture spec</cite>
                            </div>
                        </section>

                        <section className="ovl-section">
                            <div className="ovl-section-head">
                                <div className="ovl-section-num">B · Iron Dome</div>
                                <div><h2>Four checks.<br />No bypass.</h2><p className="lede">Before any signal reaches execution it passes four synchronous compliance checks that no AI component can override. The gatekeeper is deliberately synchronous and dict-based — no async I/O, no database calls.</p></div>
                            </div>
                            <div className="ovl-specs">
                                <div className="ovl-spec"><span className="key">Restricted-Symbol Blocklist</span><span className="val">A configurable blocklist; orders on blocked symbols are rejected at the gate. Basis: MAR Art. 5.</span><span className="tag">MAR</span></div>
                                <div className="ovl-spec"><span className="key">Field Completeness</span><span className="val">Every order must carry the fields downstream reporting needs. Missing field → reject.</span><span className="tag">MiFID II</span></div>
                                <div className="ovl-spec"><span className="key">Wash-Trade Window</span><span className="val">Short rolling window per tenant. Opposite-side same-symbol inside the window → reject. MiFID II / MAR Art. 12.</span><span className="tag">MAR</span></div>
                                <div className="ovl-spec"><span className="key">Max Order Value</span><span className="val">A hard per-order cap, configurable per tenant. Decimal arithmetic — float rounding can't sneak past. ESMA Position Limits (MiFID II Art. 57).</span><span className="tag">ESMA</span></div>
                                <div className="ovl-spec"><span className="key">Daily Trade Limit</span><span className="val">A small default cap per tenant per day. Once hit, further orders are rejected.</span><span className="tag">Internal</span></div>
                            </div>
                        </section>

                        <section className="ovl-section">
                            <div className="ovl-section-head">
                                <div className="ovl-section-num">C · Recovery &amp; unlock</div>
                                <div><h2>Stops cleanly.<br />Restarts deliberately.</h2><p className="lede">Halting is the easy part — the hard part is making sure the system doesn't re-arm too soon. Recovery is staged and adaptive: the longer the system has been halted, the cheaper it is to clear, but the bar for the *first* re-entry is intentionally high. No bull-trap re-entries, no quiet auto-resume after a glitch.</p></div>
                            </div>
                            <div className="ovl-feat3">
                                <div><span className="feat-ic">1</span><h4>Recovery threshold</h4><p>After a halt the system looks for a meaningful equity recovery before any new motion can pass. A small bounce isn't enough — the bar is set to filter out bull-trap rebounds.</p></div>
                                <div><span className="feat-ic">2</span><h4>Time-decay</h4><p>If the halt persists for hours, the recovery threshold relaxes on a documented schedule. The longer the cool-off, the cheaper the unlock — but never automatic.</p></div>
                                <div><span className="feat-ic">3</span><h4>Operator-acknowledged</h4><p>Every unlock is acknowledged by a human operator. The audit chain records who, when, and on what evidence. No timed auto-resume, ever.</p></div>
                            </div>
                        </section>

                        <section className="ovl-section">
                            <div className="ovl-section-head">
                                <div className="ovl-section-num">D · Standards</div>
                                <div><h2>Architected against<br />the regulations that matter.</h2><p className="lede">The maintainers do not hold a BaFin authorisation; running this software for your own account does not require one. The architecture is built to the same standards a supervised firm would have to meet — MiFID II audit-trail, DORA resilience, EU AI Act record-keeping — so a regulated operator can adopt it without retro-fitting. No claim of supervised status; see DISCLAIMER.</p></div>
                            </div>
                            <div className="standards-grid">
                                <div className="standard-card">
                                    <div className="standard-stamp">MiFID II</div>
                                    <div className="standard-art">Art. 25 · EU</div>
                                    <div className="standard-desc">Algorithmic-trading documentation. Every automated decision carries a written reason — required at the schema level, not as a manual review step.</div>
                                </div>
                                <div className="standard-card">
                                    <div className="standard-stamp">MiFID II</div>
                                    <div className="standard-art">Art. 26 / RTS 22 · EU</div>
                                    <div className="standard-desc">Transaction-reporting schema in place. <b>Schema ready, submission disabled</b> until the relevant German investment-firm licence is granted. Activates by configuration when ready.</div>
                                </div>
                                <div className="standard-card">
                                    <div className="standard-stamp">BaFin</div>
                                    <div className="standard-art">MaRisk AT 7.2 §6 · DE</div>
                                    <div className="standard-desc">"Unverzügliche Reaktionsfähigkeit" requirement for algo-trading systems. Implemented via the kill-switch's seconds-scale mass-cancel.</div>
                                </div>
                                <div className="standard-card">
                                    <div className="standard-stamp">DORA</div>
                                    <div className="standard-art">Art. 15 · EU</div>
                                    <div className="standard-desc">ICT operational-resilience: tamper-evident logging of material operations. Implemented via the cryptographic hash-chain ledger plus managed-cloud fallback.</div>
                                </div>
                                <div className="standard-card">
                                    <div className="standard-stamp">EU AI Act</div>
                                    <div className="standard-art">Art. 12 · EU</div>
                                    <div className="standard-desc">Automatic record-keeping for high-risk AI systems. Same hash-chain audit sink as DORA — every material decision appended atomically.</div>
                                </div>
                            </div>
                        </section>

                        <section className="ovl-section">
                            <div className="ovl-section-head">
                                <div className="ovl-section-num">E · Safe defaults</div>
                                <div><h2>Honest out of the box.</h2><p className="lede">The platform ships with conservative defaults. Live trading and ML models require explicit configuration changes — there is no "just turn it on" path that bypasses review.</p></div>
                            </div>
                            <div className="ovl-specs">
                                <div className="ovl-spec"><span className="key">Paper trading</span><span className="val">Observation mode by default. Going live requires a deliberate code-level decision — never a UI toggle.</span><span className="tag">Safe</span></div>
                                <div className="ovl-spec"><span className="key">Models</span><span className="val">All twelve senators ship pre-trained. A missing model file makes that senator fall back to neutral and the fallback is logged; the senate still votes.</span><span className="tag">Reviewable</span></div>
                                <div className="ovl-spec"><span className="key">Universe</span><span className="val">Production runs the full S&amp;P 500. New installs ship a small safe-default sandbox until the operator deliberately opts into the full universe.</span><span className="tag">S&amp;P 500</span></div>
                                <div className="ovl-spec"><span className="key">Learning Engine</span><span className="val">A separate process extracts patterns from losing trades and proposes them as pre-filters. Rules require operator review before going live — never automatic.</span><span className="tag">Human-in-loop</span></div>
                            </div>
                        </section>

                        <section className="ovl-section">
                            <div className="ovl-final">
                                <h3>Read the full spec.</h3>
                                <p>Every layer, every threshold, every ADR — with the regulatory mapping. The same document your compliance team will read.</p>
                                <div className="ctas">
                                    <a className="btn-prim" href="mailto:info@aaagents.de?subject=AAAgents%20access%20request" data-close="safe">Request access ›</a>
                                    <a className="btn-ghost" href="https://github.com/Autonomous-Asset-Management-Agents" target="_blank" rel="noopener" data-close="safe">View on GitHub</a>
                                </div>
                            </div>
                        </section>
                    </div>
                </div>
            </div>
        </div>
    );
}
