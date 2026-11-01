// #4869 (#4581 S1): no workflow runs functions/test/, so the node:test suites of the `edge`
// function run here, inside the regular vitest gate. They import no Firebase package, so the
// functions/ dependencies need not be installed.
import { execFileSync } from "node:child_process";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const SUITES = ["functions/test/edge.test.mjs", "functions/test/package-generated.test.mjs"];

describe("edge function (#4869)", () => {
  it("node:test suites pass", () => {
    let out: string;
    let code = 0;
    try {
      out = execFileSync(process.execPath, ["--test", ...SUITES], {
        cwd: resolve(process.cwd()),
        encoding: "utf8",
        stdio: "pipe",
      });
    } catch (err) {
      const e = err as { status?: number; stdout?: string; stderr?: string };
      code = e.status ?? 1;
      out = `${e.stdout ?? ""}\n${e.stderr ?? ""}`;
    }
    expect(code, out).toBe(0);
    expect(out).toMatch(/fail 0/);
  }, 60_000);
});
