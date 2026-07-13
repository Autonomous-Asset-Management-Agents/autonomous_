import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

// --- BackendOffline component tests (desktop-aware) ---

const mockGetEngineStatus = vi.fn().mockResolvedValue("error" as const);
const mockGetEngineLogs = vi.fn().mockResolvedValue(["Shadow Boot FAILED"]);
const mockOnEngineStatus = vi.fn().mockReturnValue(() => {});
const mockStartEngine = vi.fn().mockResolvedValue(undefined);
const mockIsDesktop = vi.fn();

vi.mock("@/lib/desktopBridge", () => ({
    isDesktop: (...args: unknown[]) => mockIsDesktop(...args),
    getEngineStatus: (...args: unknown[]) => mockGetEngineStatus(...args),
    getEngineLogs: (...args: unknown[]) => mockGetEngineLogs(...args),
    onEngineStatus: (...args: unknown[]) => mockOnEngineStatus(...args),
    startEngine: (...args: unknown[]) => mockStartEngine(...args),
}));

// Lazy import after mock is registered
const { default: BackendOffline } = await import("../pages/BackendOffline");

describe("BackendOffline — Desktop vs Cloud", () => {
    beforeEach(() => {
        vi.clearAllMocks();
    });

    it("shows engine status and Restart Engine button on desktop", () => {
        mockIsDesktop.mockReturnValue(true);
        render(<BackendOffline />);
        expect(screen.getByText(/Restart Engine/)).toBeTruthy();
        // Docker instructions must NOT appear for desktop users
        expect(screen.queryByText(/docker compose/)).toBeNull();
    });

    it("shows Docker instructions for non-desktop (cloud) users", () => {
        mockIsDesktop.mockReturnValue(false);
        render(<BackendOffline />);
        expect(screen.getByText(/docker compose/)).toBeTruthy();
        // Desktop-only controls must NOT appear for cloud users
        expect(screen.queryByText(/Restart Engine/)).toBeNull();
    });

    it("always shows the Retry Connection button", () => {
        mockIsDesktop.mockReturnValue(true);
        render(<BackendOffline />);
        expect(screen.getByText(/Retry Connection/)).toBeTruthy();
    });
});

// --- Source-contract: BackendOffline.tsx must import desktopBridge ---

const offline = readFileSync(
    path.join(
        path.dirname(fileURLToPath(import.meta.url)),
        "..",
        "pages",
        "BackendOffline.tsx",
    ),
    "utf8",
);

describe("BackendOffline source contract", () => {
    it("imports isDesktop from the desktop bridge", () => {
        expect(offline).toMatch(/isDesktop/);
        expect(offline).toMatch(/desktopBridge/);
    });

    it("imports engine lifecycle functions", () => {
        expect(offline).toMatch(/getEngineStatus/);
        expect(offline).toMatch(/startEngine/);
        expect(offline).toMatch(/getEngineLogs/);
    });

    it("does not show Docker instructions unconditionally", () => {
        // The Docker block must be behind a conditional (desktop vs cloud)
        expect(offline).toMatch(/desktop\s*\?/);
    });
});
