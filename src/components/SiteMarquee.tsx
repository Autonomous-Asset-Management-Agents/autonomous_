import "./SiteMarquee.css";
import { WINDOWS_DOWNLOAD_URL } from "@/lib/appVersion";

/**
 * OSS promo ticker — the "autonomous_ desktop trader available - download for windows" marquee that
 * runs above the nav on the marketing landing (LandingViewE "lb-risk-banner",
 * src/styles/landing-d.css). Extracted so the public LiveDemo shows the SAME strip instead of
 * missing it. Light default (marketing); `dark` matches the demo's black console theme. Links
 * straight to the Windows installer via WINDOWS_DOWNLOAD_URL (single source of truth in
 * @/lib/appVersion), exactly like the landing banner — no hardcoded release tag here.
 */
const MESSAGE = "autonomous_ desktop trader available - download for windows";

export function SiteMarquee({ dark = false }: { dark?: boolean } = {}) {
  return (
    <a
      href={WINDOWS_DOWNLOAD_URL}
      target="_blank"
      rel="noopener"
      className={`site-marquee${dark ? " site-marquee--dark" : ""}`}
    >
      {/* Duplicate the content for a seamless -50% loop */}
      <div className="site-marquee-track">
        {[0, 1].map((i) => (
          <span key={i} className="site-marquee-item" aria-hidden={i === 1 ? "true" : undefined}>
            {MESSAGE}
            &nbsp;&nbsp;&nbsp;·&nbsp;&nbsp;&nbsp;
            {MESSAGE}
            &nbsp;&nbsp;&nbsp;·&nbsp;&nbsp;&nbsp;
          </span>
        ))}
      </div>
    </a>
  );
}
