/** #5135 (GTM-1) — the Alpaca authorization prompt: mirrored golden text, key mask, dialog. */
import { createHash } from "node:crypto";
import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";

import {
  AUTHORIZATION_TEXT,
  AUTHORIZATION_VERSION,
  maskKeyId,
} from "@/console/desktop/brokerAuthorization";
import { BrokerAuthorizationDialog } from "@/console/desktop/BrokerAuthorizationDialog";

// The SAME golden is pinned on the engine side (ai_trading_bot/tests/unit/test_broker_authorization_seal.py).
const GOLDEN_TEXT =
  "Authorize autonomous_\n\n" +
  "By allowing autonomous_ to access your Alpaca account, you are granting autonomous_ access " +
  "to your account information and authorization to place transactions at your direction.\n\n" +
  "Alpaca does not warrant or guarantee that autonomous_ will work as advertised or expected. " +
  "Before authorizing, learn more about autonomous_.";
const GOLDEN_SHA256 = "7d047c040ce2604c069f717b7adb058cbe4ca4ffff70165bf4d7a15fabfb219d";

describe("brokerAuthorization mirror (#5135)", () => {
  it("is byte-identical to the engine's golden text, pinned by text and SHA-256", () => {
    expect(AUTHORIZATION_TEXT).toBe(GOLDEN_TEXT);
    expect(createHash("sha256").update(AUTHORIZATION_TEXT, "utf8").digest("hex")).toBe(GOLDEN_SHA256);
    expect(AUTHORIZATION_VERSION).toBe("1.0.0");
  });

  it("maskKeyId keeps only the last four characters", () => {
    expect(maskKeyId("PKABCDEFWXYZ")).toBe("••••WXYZ");
    expect(maskKeyId("  PKABCDEFWXYZ  ")).toBe("••••WXYZ");
    expect(maskKeyId("AB")).toBe("••••AB");
    expect(maskKeyId("")).toBe("••••");
  });
});

describe("BrokerAuthorizationDialog (#5135)", () => {
  it("shows the wording verbatim with DENY and ALLOW and a learn-more link", () => {
    render(<BrokerAuthorizationDialog onAnswer={vi.fn()} />);
    const dialog = screen.getByRole("dialog");
    // whitespace-normalised: paragraphs are rendered as separate blocks
    const shown = (dialog.textContent ?? "").replace(/\s+/g, " ");
    expect(shown).toContain(GOLDEN_TEXT.split("\n\n")[0]);
    expect(shown).toContain(GOLDEN_TEXT.split("\n\n")[1]);
    expect(shown).toContain(GOLDEN_TEXT.split("\n\n")[2]);
    expect(screen.getByRole("button", { name: "DENY" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "ALLOW" })).toBeTruthy();
    expect(screen.queryByRole("checkbox")).toBeNull();
    const link = screen.getByRole("link", { name: /learn more about autonomous_/i });
    expect(link.getAttribute("href")).toBe("https://github.com/Autonomous-Asset-Management-Agents/autonomous_");
    expect(link.getAttribute("target")).toBe("_blank");
    expect(link.getAttribute("rel")).toBe("noreferrer");
  });

  it("reports the answer", () => {
    const onAnswer = vi.fn();
    render(<BrokerAuthorizationDialog onAnswer={onAnswer} />);
    fireEvent.click(screen.getByRole("button", { name: "DENY" }));
    fireEvent.click(screen.getByRole("button", { name: "ALLOW" }));
    expect(onAnswer.mock.calls).toEqual([["deny"], ["allow"]]);
  });

  it("disables both buttons while busy", () => {
    render(<BrokerAuthorizationDialog onAnswer={vi.fn()} busy />);
    expect((screen.getByRole("button", { name: "DENY" }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole("button", { name: "ALLOW" }) as HTMLButtonElement).disabled).toBe(true);
  });
});
