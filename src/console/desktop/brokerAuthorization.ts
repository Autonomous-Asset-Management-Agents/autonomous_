/**
 * Alpaca authorization prompt (#5135, GTM-1) — Alpaca's template, `[Name]` → `autonomous_`.
 *
 * Sealed on the WORM chain via its SHA-256 — English, byte-identical, never translated. Mirrors
 * `ai_trading_bot/core/broker_authorization.py` (AUTHORIZATION_TEXT / AUTHORIZATION_VERSION), so
 * it is part of the record, not UI copy: it stays out of the i18n catalogue and this file imports
 * nothing. Pinned by text and SHA-256 in `src/test/brokerAuthorization.test.tsx`; the engine pins
 * the same hash. Any change to the wording bumps AUTHORIZATION_VERSION on both sides.
 */

export const AUTHORIZATION_VERSION = "1.0.0" as const;

export const AUTHORIZATION_TEXT =
  "Authorize autonomous_\n\n" +
  "By allowing autonomous_ to access your Alpaca account, you are granting autonomous_ access " +
  "to your account information and authorization to place transactions at your direction.\n\n" +
  "Alpaca does not warrant or guarantee that autonomous_ will work as advertised or expected. " +
  "Before authorizing, learn more about autonomous_.";

/** Target of "learn more about autonomous_" — not part of the hashed text. */
export const LEARN_MORE_URL = "https://github.com/Autonomous-Asset-Management-Agents/autonomous_";

/** The only key identifier that may leave the renderer: "••••" + the last four characters
 *  (same mask as desktop/electron/paper-slots.cjs::maskAccount). */
export const maskKeyId = (keyId: string): string => `••••${keyId.trim().slice(-4)}`;
