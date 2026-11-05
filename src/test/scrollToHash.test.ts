import { describe, it, expect, vi, afterEach, beforeEach } from "vitest";
import { scrollToHash } from "@/lib/scrollToHash";

// The landing's Download CTA deep-links to /preview#block-editions. Because the
// landing renders async and Lenis owns the scroll position, the browser's native
// hash jump is a no-op — scrollToHash resolves the anchor to a real scroll.
describe("scrollToHash", () => {
  beforeEach(() => {
    document.body.innerHTML = "";
    vi.stubGlobal("scrollTo", vi.fn());
  });
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    document.body.innerHTML = "";
  });

  it("returns false and does nothing for an empty hash", () => {
    const lenis = { scrollTo: vi.fn() };
    expect(scrollToHash("", lenis)).toBe(false);
    expect(scrollToHash("#", lenis)).toBe(false);
    expect(lenis.scrollTo).not.toHaveBeenCalled();
  });

  it("returns false when the target element is not (yet) in the DOM", () => {
    const lenis = { scrollTo: vi.fn() };
    expect(scrollToHash("#block-editions", lenis)).toBe(false);
    expect(lenis.scrollTo).not.toHaveBeenCalled();
  });

  it("uses Lenis.scrollTo on the target element when a live Lenis instance is given", () => {
    const el = document.createElement("section");
    el.id = "block-editions";
    document.body.appendChild(el);
    const lenis = { scrollTo: vi.fn() };

    expect(scrollToHash("#block-editions", lenis)).toBe(true);
    expect(lenis.scrollTo).toHaveBeenCalledTimes(1);
    // first arg is the element itself; second carries an offset option
    const [target, opts] = lenis.scrollTo.mock.calls[0];
    expect(target).toBe(el);
    expect(opts).toHaveProperty("offset");
  });

  it("falls back to native window.scrollTo when no Lenis instance is present (reduced-motion path)", () => {
    const el = document.createElement("section");
    el.id = "block-editions";
    document.body.appendChild(el);

    expect(scrollToHash("#block-editions", null)).toBe(true);
    expect(window.scrollTo).toHaveBeenCalledTimes(1);
  });

  it("tolerates a bare id without the leading '#'", () => {
    const el = document.createElement("section");
    el.id = "block-editions";
    document.body.appendChild(el);
    const lenis = { scrollTo: vi.fn() };

    expect(scrollToHash("block-editions", lenis)).toBe(true);
    expect(lenis.scrollTo).toHaveBeenCalledTimes(1);
  });
});
