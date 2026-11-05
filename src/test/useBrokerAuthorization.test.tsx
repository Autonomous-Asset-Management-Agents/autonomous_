// #5155 (GTM-1): one hook for every place the app takes Alpaca credentials. It shows Alpaca's
// authorization prompt and resolves only AFTER the answer is written (fail-closed: a write error is
// "error", never "allow"). Text, version and key mask come from the hook, never from the caller.
import { render, screen, fireEvent, waitFor, act } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { useState } from "react";

const recordBrokerAuthorization = vi.fn();
vi.mock("@/lib/desktopBridge", () => ({
  recordBrokerAuthorization: (...a: unknown[]) => recordBrokerAuthorization(...a),
}));

import { useBrokerAuthorization, type BrokerAuthorizationSlot } from "../console/desktop/useBrokerAuthorization";
import { AUTHORIZATION_TEXT, AUTHORIZATION_VERSION, maskKeyId } from "../console/desktop/brokerAuthorization";

function Probe({ slot, keyId }: { slot: BrokerAuthorizationSlot; keyId: string }) {
  const { authorize, dialog } = useBrokerAuthorization();
  const [result, setResult] = useState<string>("none");
  return (
    <>
      <button onClick={() => void authorize(slot, keyId).then(setResult)}>ask</button>
      <output aria-label="result">{result}</output>
      {dialog}
    </>
  );
}

const result = () => screen.getByLabelText("result").textContent;

describe("useBrokerAuthorization (#5155)", () => {
  beforeEach(() => {
    recordBrokerAuthorization.mockReset().mockResolvedValue({ ok: true });
  });

  it("no dialog until authorize is called", () => {
    render(<Probe slot="paper1" keyId="PKABCDWXYZ" />);
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("ALLOW: writes the full record with the given slot, the hook's text and the masked key", async () => {
    render(<Probe slot="paper2" keyId="  PKABCDWXYZ " />);
    fireEvent.click(screen.getByRole("button", { name: "ask" }));
    fireEvent.click(await screen.findByRole("button", { name: "ALLOW" }));
    await waitFor(() => expect(result()).toBe("allow"));
    expect(recordBrokerAuthorization).toHaveBeenCalledWith({
      answer: "allow",
      slot: "paper2",
      keyMasked: maskKeyId("PKABCDWXYZ"),
      text: AUTHORIZATION_TEXT,
      version: AUTHORIZATION_VERSION,
    });
    expect(JSON.stringify(recordBrokerAuthorization.mock.calls[0][0])).not.toContain("PKABCDWXYZ");
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("resolves only after the answer is written", async () => {
    let release: (v: { ok: boolean }) => void = () => {};
    recordBrokerAuthorization.mockReturnValue(new Promise((r) => (release = r)));
    render(<Probe slot="live" keyId="AKLIVE1234" />);
    fireEvent.click(screen.getByRole("button", { name: "ask" }));
    fireEvent.click(await screen.findByRole("button", { name: "ALLOW" }));
    await waitFor(() => expect(recordBrokerAuthorization).toHaveBeenCalled());
    expect(result()).toBe("none");
    await act(async () => release({ ok: true }));
    await waitFor(() => expect(result()).toBe("allow"));
  });

  it("DENY: written as deny, resolves deny, dialog closed", async () => {
    render(<Probe slot="paper3" keyId="PK3333" />);
    fireEvent.click(screen.getByRole("button", { name: "ask" }));
    fireEvent.click(await screen.findByRole("button", { name: "DENY" }));
    await waitFor(() => expect(result()).toBe("deny"));
    expect(recordBrokerAuthorization).toHaveBeenCalledWith(expect.objectContaining({ answer: "deny", slot: "paper3" }));
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("bridge answers ok:false → error (fail-closed), even on ALLOW", async () => {
    recordBrokerAuthorization.mockResolvedValue({ ok: false });
    render(<Probe slot="paper1" keyId="PK1111" />);
    fireEvent.click(screen.getByRole("button", { name: "ask" }));
    fireEvent.click(await screen.findByRole("button", { name: "ALLOW" }));
    await waitFor(() => expect(result()).toBe("error"));
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("bridge throws → error (fail-closed)", async () => {
    recordBrokerAuthorization.mockRejectedValue(new Error("ipc down"));
    render(<Probe slot="paper1" keyId="PK1111" />);
    fireEvent.click(screen.getByRole("button", { name: "ask" }));
    fireEvent.click(await screen.findByRole("button", { name: "ALLOW" }));
    await waitFor(() => expect(result()).toBe("error"));
  });
});
