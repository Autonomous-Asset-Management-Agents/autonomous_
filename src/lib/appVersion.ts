// Single source of truth for the desktop app version in the web/console build.
// The values are injected at build time by Vite's `define` (see vite.config.ts and
// vitest.config.ts). Two distinct numbers are tracked (#2270, "prod-only gate"):
//
//   • __APP_VERSION__            ← desktop/package.json   — the INSTALLED/running build.
//   • __STABLE_DESKTOP_VERSION__ ← desktop/stable-release.json — the last PROD release.
//
// Keeping them separate means a test/rc build (which bumps desktop/package.json) does NOT move
// the public "Download for Windows" link — that link follows STABLE_DESKTOP_VERSION and only
// advances when a human bumps desktop/stable-release.json on a clean PROD release. The Settings
// "Version" row keeps showing APP_VERSION (the installed build). A single per-release file bump
// updates the derived surfaces — no hand-edited version literals to drift (#1899).
declare const __APP_VERSION__: string;
declare const __STABLE_DESKTOP_VERSION__: string;
declare const __STABLE_DESKTOP_VERSION_MAC__: string;

/** The desktop app version, e.g. "0.1.11". Falls back to "0.0.0" only if the
 *  build-time define is absent (defensive — it is set in vite + vitest configs). */
export const APP_VERSION: string =
  typeof __APP_VERSION__ !== "undefined" ? __APP_VERSION__ : "0.0.0";

/** Resolve the stable (last PROD) desktop version. Pure so the fallback is unit-testable:
 *  when the build-time stable define is absent, fall back to the installed app version
 *  (pre-#2270 behaviour — website link tracked the app version). */
export const resolveStableVersion = (
  stable: string | undefined,
  app: string,
): string => (typeof stable !== "undefined" ? stable : app);

/** The last PROD-released desktop version (#2270), from desktop/stable-release.json.
 *  Decoupled from APP_VERSION so a test/rc build does not move the public download link. */
export const STABLE_DESKTOP_VERSION: string = resolveStableVersion(
  typeof __STABLE_DESKTOP_VERSION__ !== "undefined" ? __STABLE_DESKTOP_VERSION__ : undefined,
  APP_VERSION,
);

/** Build the GitHub release tag for a given desktop version. The `-beta` suffix is fixed
 *  until GA. Pure so callers/tests can derive a tag for any version. */
export const desktopReleaseTag = (version: string): string => `desktop-v${version}-beta`;

/** Build the direct download URL of the signed Windows Setup.exe for a given desktop version.
 *  NOTE: cannot use GitHub's `/releases/latest/…` — desktop builds are marked pre-release, so
 *  `latest` resolves to the (non-prerelease) models release. Pure so it is testable per version. */
export const windowsDownloadUrl = (version: string): string =>
  "https://github.com/Autonomous-Asset-Management-Agents/autonomous_/releases/download/" +
  `${desktopReleaseTag(version)}/autonomous_setup.exe`;

/** GitHub release tag the PUBLIC download link points at — the last PROD release (#2270),
 *  NOT the installed build. */
export const DESKTOP_RELEASE_TAG = desktopReleaseTag(STABLE_DESKTOP_VERSION);

/** Direct download of the signed Windows Setup.exe from the last PROD release (#2270). */
export const WINDOWS_DOWNLOAD_URL = windowsDownloadUrl(STABLE_DESKTOP_VERSION);

// ── macOS (native .dmg, #2739 / S2) ─────────────────────────────────────────────────────────────
// The mac release line is SEPARATE from Windows: its own tag namespace `desktop-mac-v*-beta` and its
// own version key (`mac_version` in desktop/stable-release.json). Falls back to "0.0.0" (no mac
// release yet) so a repo without a mac build never mints a broken link.
export const STABLE_DESKTOP_VERSION_MAC: string =
  typeof __STABLE_DESKTOP_VERSION_MAC__ !== "undefined"
    ? __STABLE_DESKTOP_VERSION_MAC__
    : "0.0.0";

/** GitHub release tag for a mac desktop version (separate namespace from Windows `desktop-v*`). */
export const macDesktopReleaseTag = (version: string): string =>
  `desktop-mac-v${version}-beta`;

/** Direct download URL of the Apple-Silicon .dmg for a given mac desktop version. Fixed asset name
 *  (like `autonomous_setup.exe`), so the URL is derivable from the tag alone. */
export const macDownloadUrl = (version: string): string =>
  "https://github.com/Autonomous-Asset-Management-Agents/autonomous_/releases/download/" +
  `${macDesktopReleaseTag(version)}/AAAgents-arm64.dmg`;

/** GitHub release tag the PUBLIC mac download points at — the last mac PROD release. */
export const MAC_DESKTOP_RELEASE_TAG = macDesktopReleaseTag(STABLE_DESKTOP_VERSION_MAC);

/** Direct download of the Apple-Silicon .dmg from the last mac PROD release. */
export const MAC_DOWNLOAD_URL = macDownloadUrl(STABLE_DESKTOP_VERSION_MAC);
export interface PlatformDownload {
  os: "mac" | "windows";
  url: string;
  label: string;
}

/** Pick the installer for the visitor's OS (#2739/S10). The marketing site is client-rendered so
 *  `navigator` is available; pass `ua` in tests. Apple Silicon is the only mac target — Intel Macs
 *  get the same .dmg link (the page notes "Apple Silicon"); iOS is treated as non-mac. Defaults to
 *  Windows so a bot/SSR/unknown UA keeps today's behaviour. */
export const platformDownload = (ua?: string): PlatformDownload => {
  const s =
    ua ?? (typeof navigator !== "undefined" ? navigator.userAgent : "") ?? "";
  const isMac = /Mac(intosh| OS X)?/i.test(s) && !/(iPhone|iPad|iPod)/i.test(s);
  return isMac
    ? { os: "mac", url: MAC_DOWNLOAD_URL, label: "Download for Mac" }
    : { os: "windows", url: WINDOWS_DOWNLOAD_URL, label: "Download for Windows" };
};
