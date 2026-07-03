import "./SiteMarquee.css";

/**
 * OSS promo ticker — the "Open Source Version launched - download from github" marquee that
 * runs above the nav on the marketing landing (LandingViewE "lb-risk-banner",
 * src/styles/landing-d.css). Extracted so the public LiveDemo shows the SAME strip instead of
 * missing it. Light default (marketing); `dark` matches the demo's black console theme. Links
 * to the GitHub releases, exactly like the landing banner.
 */
const MESSAGE = "Open Source Version launched - download from github";
const RELEASES_URL =
  "https://github.com/Autonomous-Asset-Management-Agents/autonomous_/releases";

export function SiteMarquee({ dark = false }: { dark?: boolean } = {}) {
  return (
    <a
      href={RELEASES_URL}
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
