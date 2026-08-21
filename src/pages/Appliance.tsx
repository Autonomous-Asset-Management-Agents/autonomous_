import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { joinWaitlist, fetchPosition, type PositionResult } from "@/lib/applianceApi";
import { positionCopy, buildInviteLink } from "@/lib/applianceWaitlist";
import { GERMAN_ENABLED } from "@/lib/languageGate";

/**
 * /appliance — "Your broker on the shelf." (spec docs/superpowers/specs/
 * 2026-08-14-appliance-preview-page-design.md, structure & copy operator-approved §7a).
 *
 * DEV-GATED: the route is registered only under import.meta.env.DEV (operator 14.08.:
 * not linked, not reachable in production until go-live). Visual slots render neutral
 * placeholders until the imagery round; the waitlist talks to the STUB api seam until
 * the Functions plan lands. Design per SYSTEM_DESIGN_GUIDE §1–17.
 *
 * i18n (#2869): all copy lives under the `appliance.*` keys in src/locales/{en,de} —
 * EXCEPT the terminal queue block, which stays English by design (terminal output is a
 * technical surface, like logs and timestamps — guide §11).
 */

type FormState = "idle" | "submitting" | "pending" | "error";

const FOCUS =
  "focus-visible:outline focus-visible:outline-2 focus-visible:outline-white/35 focus-visible:outline-offset-2";

export default function Appliance({ initialCode }: { initialCode?: string }) {
  const { t, i18n } = useTranslation();
  const [email, setEmail] = useState("");
  const [form, setForm] = useState<FormState>("idle");
  const [pos, setPos] = useState<PositionResult | null>(null);
  // Arriving via the double-opt-in confirm redirect (/appliance?code=...) shows the
  // live queue position; the prop keeps tests deterministic.
  const [code] = useState<string | null>(
    () => initialCode ?? new URLSearchParams(window.location.search).get("code"),
  );

  // Language gate: until the full main-page translation ships, everything runs
  // English-only — flip localStorage'd 'de' back and render no toggle.
  useEffect(() => {
    if (!GERMAN_ENABLED && i18n.resolvedLanguage !== "en") {
      i18n.changeLanguage("en");
    }
  }, [i18n]);

  // SPA navigations keep the previous scroll offset — arriving from the landing
  // (ticker/editions card) must land at the TOP. Hardened: immediate + next frame
  // + late tick, because the landing's Lenis instance and the browser's scroll
  // restoration both write scroll positions after mount.
  useEffect(() => {
    if ("scrollRestoration" in window.history) {
      window.history.scrollRestoration = "manual";
    }
    const toTop = () => window.scrollTo(0, 0);
    toTop();
    const raf = requestAnimationFrame(toTop);
    const late = window.setTimeout(toTop, 120);
    return () => {
      cancelAnimationFrame(raf);
      window.clearTimeout(late);
    };
  }, []);

  // Confirmed visitors (arriving via the opt-in confirm link) see their live position.
  useEffect(() => {
    if (!code) return;
    let alive = true;
    fetchPosition(code)
      .then((p) => {
        if (alive) setPos(p);
      })
      .catch(() => {
        /* position stays hidden — the queue block simply doesn't render (§15: no fake data) */
      });
    return () => {
      alive = false;
    };
  }, [code]);

  async function join() {
    if (!email.trim() || form === "submitting") return;
    setForm("submitting");
    try {
      const ref = new URLSearchParams(window.location.search).get("ref") ?? undefined;
      await joinWaitlist(email.trim().toLowerCase(), ref);
      setForm("pending");
    } catch {
      setForm("error");
    }
  }

  const scrollToWaitlist = () =>
    document.getElementById("waitlist")?.scrollIntoView({ behavior: "smooth", block: "start" });

  // Scroll-scrub on the hero visual (main-page pattern): scrolling gently rotates the stele
  // in perspective so it reads as an object, not a photo. Replaced by the real orbit frames
  // once the angle-sequence assets exist. Disabled under prefers-reduced-motion (guide §12).
  const heroImgRef = useRef<HTMLImageElement | null>(null);
  useEffect(() => {
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    let raf = 0;
    const onScroll = () => {
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(() => {
        const el = heroImgRef.current;
        if (!el) return;
        // No zoom on mobile: below md the enlarged tower overflows the viewport
        // and gets hard-cut at the screen edge.
        if (window.innerWidth < 768) {
          el.style.transform = "";
          return;
        }
        // Short scrub distance: the full sweep completes while the hero is still on
        // screen (~40% viewport of scroll), otherwise the rotation happens unseen.
        const progress = Math.min(1, Math.max(0, window.scrollY / (window.innerHeight * 0.4)));
        // Pure zoom (operator 14.08.): rotateY on a flat photo foreshortens the plane —
        // real rotation waits for the Gemini orbit frames. The zoom targets the
        // autonomous_ wordmark + LED (lower right of the stele) via transform-origin,
        // so scrolling closes in on the brand mark.
        el.style.transformOrigin = "12% 79%"; // wordmark + LED in the tight-cropped v3 render
        const scale = 1 + progress * 0.45;
        el.style.transform = `scale(${scale.toFixed(3)})`;
      });
    };
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => {
      window.removeEventListener("scroll", onScroll);
      cancelAnimationFrame(raf);
    };
  }, []);

  return (
    <div className="min-h-screen bg-black text-[#f4f4f5]" style={{ fontFamily: "Inter, system-ui, sans-serif" }}>
      {/* ── Hero ── EXACT main-page hero geometry (LandingViewE, operator 14.08.):
          section padding clamp(40px,6vw,80px) / 40px gutter, inner max 1280, the H1
          full-width ABOVE the grid, then lb-hero-grid `0.9fr 1.05fr` gap clamp(64px,
          8vw,140px) items-start — text column max 540 left, visual right pulled up
          -60px with align-self start (the landing terminal's slot). ─────────── */}
      {/* ── Ticker ── the landing's sticky top marquee, carrying the edition line
          (moved out of the hero, operator 14.08.). At go-live the MAIN page ticker
          gets this text too — that change belongs to the go-live PR. */}
      <style>{`
        @keyframes applianceTicker { 0% { transform: translateX(0); } 100% { transform: translateX(-50%); } }
        .appliance-ticker-track { display: inline-flex; animation: applianceTicker 30s linear infinite; will-change: transform; }
        .appliance-ticker-track:hover { animation-play-state: paused; }
        @media (prefers-reduced-motion: reduce) { .appliance-ticker-track { animation: none; } }
      `}</style>
      <div
        className="sticky top-0 z-[1000] overflow-hidden bg-white text-[#111]"
        style={{ borderBottom: "1px solid #ececec", fontSize: 13 }}
      >
        <div className="appliance-ticker-track">
          {[0, 1].map((i) => (
            <span
              key={i}
              aria-hidden={i === 1 ? "true" : undefined}
              // identical to the landing lb-ticker-item: plain 13px sans, no mono/uppercase
              className="inline-block whitespace-nowrap py-[10px] pr-12"
            >
              {t("appliance.eyebrow")}
              {"   ·   "}
              {t("appliance.eyebrow")}
              {"   ·   "}
            </span>
          ))}
        </div>
      </div>

      {/* ── Nav ── replica of the landing's lb-nav (its CSS is scoped to the landing
          root): logo tile links home, LinkedIn + GitHub, green LIVE DEMO pill. */}
      <nav className="bg-white text-[#111] flex items-center justify-between" style={{ padding: "24px 40px" }}>
        <a
          href="/"
          aria-label="AAAgents home"
          className="bg-white w-8 h-8 flex items-center justify-center rounded-[4px] border-b-[3px] border-[#00c27a]"
        >
          <img src="/aaagents_logo_linkedin.png" alt="AAAgents Logo" style={{ height: 32, width: "auto" }} />
        </a>
        <div className="flex items-center gap-1">
          <a
            href="/"
            aria-label="Home"
            title="Home"
            className="inline-flex items-center justify-center w-9 h-9 rounded-lg text-[#555] hover:text-[#0a0a0a] hover:bg-black/5 transition-colors"
          >
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <path d="M3 10.5 12 3l9 7.5" />
              <path d="M5 9.5V21h5v-6h4v6h5V9.5" />
            </svg>
          </a>
          <a
            href="https://www.linkedin.com/company/aaa-autonomous-asset-management-agents/posts/?feedView=all"
            target="_blank"
            rel="noopener"
            aria-label="Follow us on LinkedIn"
            title="Follow us on LinkedIn"
            className="inline-flex items-center justify-center w-9 h-9 rounded-lg text-[#555] hover:text-[#0a0a0a] hover:bg-black/5 transition-colors"
          >
            <svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
              <path d="M20.447 20.452h-3.554v-5.569c0-1.328-.027-3.037-1.852-3.037-1.853 0-2.136 1.445-2.136 2.939v5.667H9.351V9h3.414v1.561h.046c.477-.9 1.637-1.85 3.37-1.85 3.601 0 4.267 2.37 4.267 5.455v6.286zM5.337 7.433a2.062 2.062 0 01-2.063-2.065 2.064 2.064 0 112.063 2.065zm1.782 13.019H3.555V9h3.564v11.452zM22.225 0H1.771C.792 0 0 .774 0 1.729v20.542C0 23.227.792 24 1.771 24h20.451C23.2 24 24 23.227 24 22.271V1.729C24 .774 23.2 0 22.222 0h.003z" />
            </svg>
          </a>
          <a
            href="https://github.com/Autonomous-Asset-Management-Agents/autonomous_"
            target="_blank"
            rel="noopener"
            aria-label="View on GitHub"
            title="View on GitHub"
            className="inline-flex items-center justify-center w-9 h-9 rounded-lg text-[#555] hover:text-[#0a0a0a] hover:bg-black/5 transition-colors"
          >
            <svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
              <path d="M12 .5C5.65.5.5 5.65.5 12c0 5.08 3.29 9.39 7.86 10.91.58.1.79-.25.79-.56 0-.28-.01-1.02-.02-2-3.2.7-3.87-1.54-3.87-1.54-.52-1.33-1.28-1.69-1.28-1.69-1.05-.72.08-.7.08-.7 1.16.08 1.77 1.19 1.77 1.19 1.03 1.77 2.7 1.26 3.36.96.1-.75.4-1.26.73-1.55-2.55-.29-5.24-1.28-5.24-5.69 0-1.26.45-2.28 1.19-3.09-.12-.29-.52-1.46.11-3.05 0 0 .97-.31 3.18 1.18.92-.26 1.91-.39 2.9-.39.99 0 1.98.13 2.9.39 2.21-1.49 3.18-1.18 3.18-1.18.63 1.59.23 2.76.11 3.05.74.81 1.19 1.83 1.19 3.09 0 4.42-2.69 5.39-5.25 5.68.41.36.78 1.06.78 2.13 0 1.54-.01 2.78-.01 3.16 0 .31.21.67.8.56C20.22 21.39 23.5 17.08 23.5 12 23.5 5.65 18.35.5 12 .5z" />
            </svg>
          </a>
          {/* Language toggle — gated OFF until the full main-page translation ships
              (operator 14.08.: no mixed-language experience). */}
          {GERMAN_ENABLED && (
          <button
            type="button"
            onClick={() => i18n.changeLanguage(i18n.resolvedLanguage === "de" ? "en" : "de")}
            aria-label={i18n.resolvedLanguage === "de" ? "Switch to English" : "Zu Deutsch wechseln"}
            title={i18n.resolvedLanguage === "de" ? "Switch to English" : "Zu Deutsch wechseln"}
            className="inline-flex items-center justify-center w-9 h-9 rounded-lg hover:bg-black/5 transition-colors ml-2"
          >
            {/* The flag shows the TARGET language (operator): on German pages the
                Union Jack, on English pages the German flag. */}
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
          <a
            href="/live-demo"
            aria-label="View live demo"
            title="View live demo"
            className="inline-flex items-center justify-center whitespace-nowrap bg-[#3eb57a] hover:bg-[#349a67] text-white font-bold text-[14px] px-4 sm:px-6 py-2 rounded-full transition-all hover:-translate-y-px ml-3"
          >
            LIVE DEMO
          </a>
        </div>
      </nav>

      <section className="bg-white text-[#111] -mt-px" style={{ padding: "clamp(16px,3vw,40px) 40px clamp(40px,6vw,80px)" }}>
        <div className="max-w-[1280px] mx-auto">
          <h1
            className="font-extrabold text-left relative z-10"
            style={{
              fontSize: "clamp(56px,7vw,108px)",
              lineHeight: 1.0,
              letterSpacing: "-0.025em",
              margin: "0 0 clamp(16px,2vw,28px)",
              whiteSpace: "pre-line", // two lines like the landing's "Autonomous<br/>Trading."
            }}
          >
            {t("appliance.hero.h1")}
          </h1>
          <div
            className="grid grid-cols-1 md:grid-cols-[0.9fr_1.05fr] items-start"
            style={{ gap: "clamp(64px,8vw,140px)" }}
          >
            <div className="flex flex-col gap-[18px] max-w-[540px] text-left">
              <div
                className="font-bold whitespace-nowrap"
                style={{ fontSize: "clamp(26px,3vw,38px)", letterSpacing: "-0.02em", lineHeight: 1.1 }}
              >
                {t("appliance.hero.subline")}
              </div>
              {/* Hardware-accessory positioning (operator 14.08.): the appliance is the
                  hardware FOR the software — autonomous_ acts, the device hosts. */}
              <p className="text-[16px] leading-[1.55] text-[#666] max-w-[46ch] mt-[18px] mb-0">
                <span className="font-mono font-bold text-[#111]">
                  autonomous<span className="text-[#00a56b]">_</span> tower
                </span>{" "}
                {t("appliance.hero.bodyLead")}{" "}
                <span className="font-mono font-bold text-[#111]">
                  autonomous<span className="text-[#00a56b]">_</span>
                </span>{" "}
                {t("appliance.hero.bodyMid")}{" "}
                <span className="font-mono font-bold text-[#111]">
                  autonomous<span className="text-[#00a56b]">_</span>
                </span>{" "}
                {t("appliance.hero.bodyTail")}
              </p>
              <div className="font-mono text-base">
                {t("appliance.hero.price")}{" "}
                <span className="text-black/45">{t("appliance.hero.priceSuffix")}</span>
              </div>
              {/* Desktop CTAs stay in the text column; on mobile they move BELOW the
                  image (main-page hero order: header, sub, text, image, buttons). */}
              <div className="hidden md:flex gap-3.5 flex-wrap items-center mt-[10px]">
                <button
                  type="button"
                  className={`rounded-full px-7 py-3 text-[13px] font-bold tracking-wide uppercase text-white bg-[#111] hover:bg-black transition-all transform active:scale-[0.97] ${FOCUS}`}
                  onClick={scrollToWaitlist}
                >
                  {t("appliance.hero.ctaJoin")}
                </button>
                <button
                  type="button"
                  className={`rounded-full px-7 py-3 text-[13px] font-bold tracking-wide uppercase text-white bg-[#111] hover:bg-black transition-all transform active:scale-[0.97] ${FOCUS}`}
                  onClick={() =>
                    document.getElementById("how")?.scrollIntoView({ behavior: "smooth", block: "start" })
                  }
                >
                  {t("appliance.hero.ctaHow")}
                </button>
              </div>
              <div className="hidden md:block text-[12px] text-black/40">{t("appliance.hero.micro")}</div>
            </div>
            {/* The stele hero shot (operator-approved 14.08.): honed black porcelain monolith
                on its walnut base, photoreal interior washed to white. Sits in the landing
                terminal's exact slot: align-self start, pulled up -60px into the H1 zone. */}
            {/* overflow-hidden: the wordmark-zoom scales the image well past its box —
                clipping at the column edge keeps it off the text (the image ground is
                page-white, so the clip line is invisible). */}
            {/* No clipping, no mask (operator 14.08.): the zoomed tower must stay FULLY
                visible — nothing may fade or vanish. The v3 studio asset carries its
                soft white edges in the file, so free overflow blends into the white
                page at any scale. */}
            <div className="relative self-start md:mt-[-140px] w-fit max-w-full mx-auto">
              <img
                ref={heroImgRef}
                src="/appliance-tower.webp"
                alt="autonomous appliance — a honed black porcelain stele on a walnut base"
                className="w-auto max-w-full object-contain select-none pointer-events-none will-change-transform"
                // Larger cap is safe with the v3 studio asset: its borders are blended
                // to white in the file, so a width-clamp letterbox can never show a
                // seam. Device bottom stays above the fold down to ~800px viewports.
                // Fit above the fold (operator 15.08.): the image top sits ~255px
                // below the viewport top on desktop (ticker + nav + H1 - pull-up),
                // so 100vh-170 overflowed and cut the walnut base. 100vh-290 keeps
                // the full stele visible on first paint.
                style={{ height: "min(860px, calc(100vh - 290px))" }}
              />
              {/* Legal note pinned bottom-right INSIDE the image box so it stays with
                  the visual at every viewport; z-raised above the scaled photo. */}
              <div className="absolute bottom-2 right-4 z-10 text-[11px] text-black/35">
                {t("appliance.hero.imgNote")}
              </div>
            </div>
            {/* Mobile CTAs (main-page hero pattern): two equal pills side by side
                below the image, micro line underneath. Hidden on md+ where the
                CTAs live in the text column above. */}
            <div className="md:hidden">
              <div className="grid grid-cols-2 gap-3">
                <button
                  type="button"
                  className={`rounded-full px-2 py-3 text-[12px] font-bold tracking-wide uppercase whitespace-nowrap text-white bg-[#111] hover:bg-black transition-all transform active:scale-[0.97] ${FOCUS}`}
                  onClick={scrollToWaitlist}
                >
                  {t("appliance.hero.ctaJoin")}
                </button>
                <button
                  type="button"
                  className={`rounded-full px-2 py-3 text-[12px] font-bold tracking-wide uppercase whitespace-nowrap text-white bg-[#111] hover:bg-black transition-all transform active:scale-[0.97] ${FOCUS}`}
                  onClick={() =>
                    document.getElementById("how")?.scrollIntoView({ behavior: "smooth", block: "start" })
                  }
                >
                  {t("appliance.hero.ctaHow")}
                </button>
              </div>
              <div className="text-[12px] text-black/40 mt-3">{t("appliance.hero.micro")}</div>
            </div>
          </div>
        </div>
      </section>

      {/* ── Story (light — the landing flip) ─────────────────────────────────── */}
      {/* -mt-px: the page root is black; a subpixel rounding gap between the two white
          sections reads as a full-width grey hairline on some resolutions. */}
      <section className="bg-white text-[#111] px-6 py-24 -mt-px">
        <div className="max-w-[1040px] mx-auto">
          <h2 className="text-3xl md:text-[34px] font-bold tracking-[-0.02em] mb-11">
            {t("appliance.story.h2")}
          </h2>
          <div className="grid md:grid-cols-3 gap-10">
            <div>
              <h3 className="text-lg font-semibold mb-2">{t("appliance.story.silentTitle")}</h3>
              <p className="text-black/65 text-[15px] leading-relaxed">{t("appliance.story.silentBody")}</p>
            </div>
            <div>
              <h3 className="text-lg font-semibold mb-2">{t("appliance.story.materialsTitle")}</h3>
              <p className="text-black/65 text-[15px] leading-relaxed">{t("appliance.story.materialsBody")}</p>
            </div>
            <div>
              <h3 className="text-lg font-semibold mb-2">{t("appliance.story.tradesTitle")}</h3>
              <p className="text-black/65 text-[15px] leading-relaxed">{t("appliance.story.tradesBody")}</p>
            </div>
          </div>
        </div>
      </section>

      {/* ── How it works ─────────────────────────────────────────────────────── */}
      <section id="how" className="px-6 py-24">
        <div className="max-w-[1040px] mx-auto">
          <h2 className="text-3xl md:text-[34px] font-bold tracking-[-0.02em] mb-11">
            {t("appliance.how.h2")}
          </h2>
          <div className="grid md:grid-cols-3 gap-10">
            <div>
              <div className="font-mono text-[13px] text-[#00c27a] mb-3">01</div>
              <h3 className="text-lg font-semibold mb-2">{t("appliance.how.step1Title")}</h3>
              <p className="text-white/60 text-[15px] leading-relaxed">{t("appliance.how.step1Body")}</p>
            </div>
            <div>
              <div className="font-mono text-[13px] text-[#00c27a] mb-3">02</div>
              <h3 className="text-lg font-semibold mb-2">{t("appliance.how.step2Title")}</h3>
              <p className="text-white/60 text-[15px] leading-relaxed">{t("appliance.how.step2Body")}</p>
            </div>
            <div>
              <div className="font-mono text-[13px] text-[#00c27a] mb-3">03</div>
              <h3 className="text-lg font-semibold mb-2">{t("appliance.how.step3Title")}</h3>
              <p className="text-white/60 text-[15px] leading-relaxed">
                {t("appliance.how.step3BodyBefore")}{" "}
                <span className="font-mono">name.autonomous.de</span>{" "}
                {t("appliance.how.step3BodyAfter")}
              </p>
            </div>
          </div>
        </div>
      </section>

      {/* ── Specs ────────────────────────────────────────────────────────────── */}
      <section className="px-6 py-24">
        <div className="max-w-[1040px] mx-auto">
          <h2 className="text-3xl md:text-[34px] font-bold tracking-[-0.02em] mb-9">
            {t("appliance.specs.h2")}
          </h2>
          <table className="w-full font-mono text-[13px] border-collapse">
            <tbody>
              {(
                [
                  ["finish", "finishV"],
                  ["acoustics", "acousticsV"],
                  ["memory", "memoryV"],
                  ["connectivity", "connectivityV"],
                  ["software", "softwareV"],
                  ["inTheBox", "inTheBoxV"],
                  ["price", "priceV"],
                ] as const
              ).map(([k, v]) => (
                <tr key={k}>
                  {/* Green labels (operator 15.08.): white/35 on black was barely legible. */}
                  <td className="py-3.5 pr-4 w-[34%] text-[11px] uppercase tracking-[0.06em] text-[#00c27a] border-b border-white/[0.06]">
                    {t(`appliance.specs.${k}`)}
                  </td>
                  <td className="py-3.5 border-b border-white/[0.06]">{t(`appliance.specs.${v}`)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="mt-4 text-[11px] text-white/35">{t("appliance.specs.note")}</div>
        </div>
      </section>

      {/* ── Waitlist (signature) ─────────────────────────────────────────────── */}
      <section id="waitlist" className="px-6 py-24 text-center">
        {/* Glow card (operator 15.08.): the waitlist sits in a tile styled like
            the Institutional editions card — green top-glow bar, soft green
            shadow, same gradient ground. */}
        <div
          className="max-w-[1040px] mx-auto relative overflow-hidden"
          style={{
            background:
              "linear-gradient(180deg, rgba(0, 194, 122, 0.05) 0%, rgba(0, 194, 122, 0) 50%), rgba(255, 255, 255, 0.015)",
            boxShadow: "0 0 40px rgba(0, 194, 122, 0.12)",
            borderRadius: "18px",
            padding: "48px 40px",
          }}
        >
          <div
            className="absolute inset-x-0 top-0 h-[2px] opacity-70"
            style={{ background: "linear-gradient(90deg, transparent, #00c27a 50%, transparent)" }}
          />
          <h2 className="text-4xl font-bold tracking-[-0.02em] mb-4">{t("appliance.wait.h2")}</h2>
          <p className="text-white/60 leading-relaxed max-w-[520px] mx-auto mb-8">
            {t("appliance.wait.lead")}
          </p>

          {form === "pending" ? (
            <div className="font-mono text-[14px] text-white/80 mb-8">
              {t("appliance.wait.pendingHint")}
            </div>
          ) : (
            /* Editions-CTA format (operator 15.08.): field + full-width button in the
               exact Contact-Us size (py-8, text-base, green glow) instead of the
               small inline pill. */
            <div className="flex flex-col gap-3.5 max-w-[420px] mx-auto mb-8">
              <input
                aria-label="waitlist-email"
                type="email"
                placeholder={t("appliance.wait.emailPlaceholder")}
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                className={`w-full border border-white/[0.14] bg-white/[0.03] rounded-full px-5 py-3 text-[13px] text-white placeholder:text-white/40 ${FOCUS}`}
              />
              <button
                type="button"
                className={`w-full h-[51px] rounded-full font-bold text-sm transition-all bg-[#00c27a] text-black hover:bg-[#00e08d] shadow-[0_0_20px_rgba(0,194,122,0.3)] flex items-center justify-center disabled:opacity-60 ${FOCUS}`}
                onClick={() => void join()}
                disabled={form === "submitting"}
              >
                {form === "submitting" ? t("appliance.wait.joining") : t("appliance.wait.join")}
              </button>
            </div>
          )}
          {form === "error" && (
            <div className="text-[13px] text-[#FF453A] mb-8">{t("appliance.wait.error")}</div>
          )}

          {pos && code && (
            /* Terminal output stays ENGLISH by design — a technical surface (guide §11). */
            <div className="bg-[#050505] border border-white/10 rounded-xl max-w-[640px] mx-auto text-left px-7 py-5 font-mono text-[14px] leading-[2.0]">
              <div>
                <span className="text-[#00c27a]">queue &gt;</span> {positionCopy(pos.position, pos.editionSize)}
              </div>
              <div>
                <span className="text-[#00c27a]">invite &gt;</span>{" "}
                {buildInviteLink(window.location.origin, code)}{" "}
                <span className="text-white/40">{t("appliance.wait.inviteSuffix")}</span>
              </div>
            </div>
          )}

          <div className="text-[12px] text-white/40 mt-6">{t("appliance.wait.micro")}</div>
        </div>
      </section>

      {/* ── FAQ ──────────────────────────────────────────────────────────────── */}
      <section className="px-6 py-24">
        <div className="max-w-[1040px] mx-auto">
          <h2 className="text-3xl md:text-[34px] font-bold tracking-[-0.02em] mb-9">
            {t("appliance.faq.h2")}
          </h2>
          <dl>
            {(["1", "2", "3", "4"] as const).map((n) => (
              <div key={n}>
                <dt className="text-base font-semibold pt-5">{t(`appliance.faq.q${n}`)}</dt>
                <dd className="ml-0 mt-2 mb-5 pb-5 text-[15px] leading-relaxed text-white/60 border-b border-white/[0.06]">
                  {t(`appliance.faq.a${n}`)}
                </dd>
              </div>
            ))}
          </dl>
        </div>
      </section>

      {/* ── Footer ───────────────────────────────────────────────────────────── */}
      <footer className="px-6 py-12 text-[12px] text-white/40 leading-relaxed">
        <div className="max-w-[1040px] mx-auto">
          <div>{t("appliance.footer.risk")}</div>
          <div className="font-mono mt-3.5 flex gap-4 justify-start flex-wrap">
            <a className={`hover:text-white/70 ${FOCUS}`} href="/legal/imprint">{t("appliance.footer.imprint")}</a>
            <a className={`hover:text-white/70 ${FOCUS}`} href="/legal/privacy">{t("appliance.footer.privacy")}</a>
            <a className={`hover:text-white/70 ${FOCUS}`} href="/legal/risk-disclosure">{t("appliance.footer.riskDisclosure")}</a>
            <a className={`hover:text-white/70 ${FOCUS}`} href="/legal/terms">{t("appliance.footer.terms")}</a>
          </div>
        </div>
      </footer>
    </div>
  );
}
