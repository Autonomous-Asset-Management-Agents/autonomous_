import "./SiteMarquee.css";
import { platformDownload } from "@/lib/appVersion";

/**
 * OSS promo ticker — the "autonomous_ desktop trader available - download for <os>" marquee that
 * runs above the nav on the marketing landing (LandingViewE "lb-risk-banner",
 * src/styles/landing-d.css). Extracted so the public LiveDemo shows the SAME strip instead of
 * missing it. Light default (marketing); `dark` matches the demo's black console theme.
 *
 * #2739/S10: the link + copy are platform-aware — a macOS visitor is sent to the Apple-Silicon .dmg
 * (desktop-mac-v* release), everyone else to the Windows installer. Single source of truth in
 * @/lib/appVersion (platformDownload) — no hardcoded release tag / OS here.
 */
export function SiteMarquee({ dark = false }: { dark?: boolean } = {}) {
  const dl = platformDownload();
  const MESSAGE = `autonomous_ desktop trader available - ${dl.label.toLowerCase()}`;
  return (
    <a
      href={dl.url}
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
