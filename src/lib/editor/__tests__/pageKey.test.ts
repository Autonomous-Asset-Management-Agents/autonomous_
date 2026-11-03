import { describe, it, expect } from "vitest";
import { derivePageKey } from "../pageKey";

describe("derivePageKey", () => {
    it("returns 'landing-b' for the landing-b variant", () => {
        expect(derivePageKey("landing-b", "/")).toBe("landing-b");
    });

    it("returns 'v1' for the default variant", () => {
        expect(derivePageKey("v1", "/")).toBe("v1");
    });

    it("returns 'stitch-v1' for the stitch variant", () => {
        expect(derivePageKey("stitch-v1", "/")).toBe("stitch-v1");
    });

    it("includes pathname for non-root routes", () => {
        expect(derivePageKey("v1", "/dashboard")).toBe("v1__dashboard");
    });

    it("collapses leading and trailing slashes", () => {
        expect(derivePageKey("v1", "/foo/bar/")).toBe("v1__foo_bar");
    });
});
