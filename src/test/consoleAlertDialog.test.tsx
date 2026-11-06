/**
 * #5172 — ConsoleAlertDialogContent: a Radix alert dialog portals to document.body, outside the
 * app's `.aaa-console` scope, so `.btn` and the dark base (console.css) would not apply there.
 * The block puts overlay and content back inside an `.aaa-console` element within the portal.
 */
import { render, screen } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogFooter,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { ConsoleAlertDialogContent } from "@/console/shared/ConsoleAlertDialogContent";

function Harness({ className }: { className?: string }) {
  return (
    <div data-testid="app">
      <AlertDialog open>
        <ConsoleAlertDialogContent className={className}>
          <AlertDialogTitle>Title</AlertDialogTitle>
          <AlertDialogFooter>
            <AlertDialogCancel className="btn">Cancel</AlertDialogCancel>
            <AlertDialogAction className="btn">OK</AlertDialogAction>
          </AlertDialogFooter>
        </ConsoleAlertDialogContent>
      </AlertDialog>
    </div>
  );
}

describe("ConsoleAlertDialogContent (#5172)", () => {
  it("renders into the portal, not into the app tree", () => {
    render(<Harness />);
    const dialog = screen.getByRole("alertdialog");
    expect(screen.getByTestId("app").contains(dialog)).toBe(false);
  });

  it("the dialog content and its buttons have an .aaa-console ancestor", () => {
    render(<Harness />);
    const dialog = screen.getByRole("alertdialog");
    expect(dialog.closest(".aaa-console")).not.toBeNull();
    for (const name of ["Cancel", "OK"]) {
      const btn = screen.getByRole("button", { name });
      expect(btn.closest(".aaa-console")).not.toBeNull();
      expect(btn.classList.contains("btn")).toBe(true);
    }
  });

  it("passes className through to the content", () => {
    render(<Harness className="max-w-[520px]" />);
    expect(screen.getByRole("alertdialog").classList.contains("max-w-[520px]")).toBe(true);
  });
});
