// Single source of truth for the desktop app version in the web/console build.
// The value is injected at build time by Vite's `define` from desktop/package.json
// (see vite.config.ts and vitest.config.ts). Keeping the Settings "Version" row,
// the "Download for Windows" link and (later) the legal notice on ONE number means
// a single desktop/package.json bump (once per release) updates them all — no
// hand-edited version literals to drift, which is what forced the #1899 href bump.
declare const __APP_VERSION__: string;

/** The desktop app version, e.g. "0.1.11". Falls back to "0.0.0" only if the
 *  build-time define is absent (defensive — it is set in vite + vitest configs). */
export const APP_VERSION: string =
  typeof __APP_VERSION__ !== "undefined" ? __APP_VERSION__ : "0.0.0";

/** GitHub release tag the desktop installer is published under. The `-beta` suffix
 *  is fixed until GA; the version tracks APP_VERSION. */
export const DESKTOP_RELEASE_TAG = `desktop-v${APP_VERSION}-beta`;

/** Direct download of the signed Windows Setup.exe from the public release.
 *  NOTE: cannot use GitHub's `/releases/latest/…` — desktop builds are marked
 *  pre-release, so `latest` resolves to the (non-prerelease) models release. */
export const WINDOWS_DOWNLOAD_URL =
  "https://github.com/Autonomous-Asset-Management-Agents/autonomous_/releases/download/" +
  `${DESKTOP_RELEASE_TAG}/autonomous_setup.exe`;
