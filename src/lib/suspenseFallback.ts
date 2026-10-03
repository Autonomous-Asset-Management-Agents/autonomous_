import type { DesignVariant } from "@/hooks/useDesignVariant";

/**
 * Background class for App's route-level <Suspense> fallback (#2804, second pass).
 *
 * The fallback used to follow the landing A/B design variant alone — and the desktop shell
 * loads from 127.0.0.1, which `hostnameDefault()` maps to "landing-b", whose fallback is
 * `bg-white`. While the lazy /console chunk loads (seconds on a cold machine) that painted a
 * WHITE full-viewport div over the dark app: the real source of the white startup flash. The
 * console is pure-black-themed regardless of any marketing variant, so console routes get a
 * black fallback unconditionally; only the public landing keeps its variant-matched color.
 */
export function suspenseFallbackClass(variant: DesignVariant, pathname: string): string {
  if (pathname.startsWith("/console")) return "bg-black";
  if (variant === "stitch-v1") return "bg-[#F3F4F6]";
  if (variant === "landing-b") return "bg-white";
  return "bg-black";
}
