import * as React from "react";
import * as AlertDialogPrimitive from "@radix-ui/react-alert-dialog";

import { cn } from "@/lib/utils";
import { AlertDialogOverlay, AlertDialogPortal, alertDialogContentClass } from "@/components/ui/alert-dialog";

/**
 * #5172 — `AlertDialogContent` for the console. The Radix portal lands on `document.body`,
 * OUTSIDE the app's `.aaa-console` scope, so `.btn` and the dark base of `console.css` would not
 * apply inside the dialog (Guide §7: light buttons with dark text). This block renders overlay
 * and content within the portal inside an `.aaa-console` element — the scope `LiveConsentModal`
 * and `BrokerAuthorizationDialog` set by hand. `contents` keeps the wrapper out of the layout;
 * overlay and content stay `fixed` as in shadcn.
 *
 * It cannot wrap the shadcn `AlertDialogContent`: that one opens its own portal, which would
 * leave the wrapper again. So it composes overlay and content itself, with the same classes.
 *
 * Same props as `AlertDialogContent`. Use this, not the shadcn one, for every console dialog.
 */
const ConsoleAlertDialogContent = React.forwardRef<
  React.ElementRef<typeof AlertDialogPrimitive.Content>,
  React.ComponentPropsWithoutRef<typeof AlertDialogPrimitive.Content>
>(({ className, ...props }, ref) => (
  <AlertDialogPortal>
    <div className="aaa-console contents">
      <AlertDialogOverlay />
      <AlertDialogPrimitive.Content ref={ref} className={cn(alertDialogContentClass, className)} {...props} />
    </div>
  </AlertDialogPortal>
));
ConsoleAlertDialogContent.displayName = "ConsoleAlertDialogContent";

export { ConsoleAlertDialogContent };
