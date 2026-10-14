// "autonomous_" inside a section intro is rendered as the brand mark (owner 12.10.2026):
// the intro text carries the word where the sentence needs it, usually in the last
// sentence, and the component sets it bold with the accent underscore.
import { describe, it, expect } from "vitest";
import { render } from "@testing-library/react";
import { mitMarke } from "../components/views/mitMarke";

describe("mitMarke", () => {
    it("renders autonomous_ at the end of the intro as the bold brand mark", () => {
        const { container } = render(<p>{mitMarke("You design the strategy. autonomous_ executes.")}</p>);
        const b = container.querySelectorAll("b");
        expect(b).toHaveLength(1);
        expect(b[0].textContent).toBe("autonomous_");
        expect(b[0].querySelector("span")?.textContent).toBe("_");
        expect(container.textContent).toBe("You design the strategy. autonomous_ executes.");
    });

    it("leaves a text without the mark unchanged", () => {
        const { container } = render(<p>{mitMarke("Read the code.")}</p>);
        expect(container.querySelectorAll("b")).toHaveLength(0);
        expect(container.textContent).toBe("Read the code.");
    });
});
