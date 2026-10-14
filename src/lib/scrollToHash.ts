/**
 * Resolve a deep-link hash (e.g. `#block-editions`, targeted by the Live Demo's
 * "Download" CTA at `/preview#block-editions`) to an actual scroll on the landing.
 *
 * Why this exists: the landing renders async (the target section isn't in the DOM
 * when the browser performs its native hash jump on load) AND it runs a Lenis
 * smooth-scroll that owns the scroll position — so the native `#anchor` jump is a
 * no-op and the visitor lands at the top instead of on the Editions section.
 *
 * Uses `Lenis.scrollTo` when a live smooth-scroll instance is passed; otherwise
 * falls back to native `window.scrollTo` (also the `prefers-reduced-motion` path,
 * where Lenis is intentionally not initialised).
 *
 * @returns `true` once the target was found and a scroll was issued, `false` if
 *          the element is not (yet) in the DOM — callers can retry while sections
 *          finish mounting.
 */
export interface LenisLike {
  scrollTo?: (target: unknown, opts?: unknown) => void;
}

export function scrollToHash(hash: string, lenis?: LenisLike | null): boolean {
  const id = hash.replace(/^#/, "");
  if (!id) return false;

  const el = document.getElementById(id);
  if (!el) return false;

  // The risk banner is pinned on the landing (sections scroll *under* it), so
  // offset by its height — otherwise the section heading hides behind the bar.
  // Only offset when the banner is actually fixed/sticky; if it scrolls away
  // with the page, offset 0 keeps the section flush to the top.
  let offset = 0;
  const banner = document.querySelector<HTMLElement>(".lb-risk-banner");
  if (banner) {
    const pos = window.getComputedStyle(banner).position;
    if (pos === "fixed" || pos === "sticky") offset = banner.offsetHeight + 12;
  }

  if (typeof lenis?.scrollTo === "function") {
    lenis.scrollTo(el, { offset: -offset, duration: 1.0 });
  } else {
    const y = el.getBoundingClientRect().top + window.scrollY - offset;
    window.scrollTo({ top: y, behavior: "smooth" });
  }
  return true;
}
