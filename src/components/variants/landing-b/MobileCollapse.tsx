import { useState, ReactNode } from "react";
import { useIsMobile } from "@/hooks/use-mobile";

/**
 * MobileCollapse — wraps children in a collapsible block on mobile
 * (≤768px, as defined by `useIsMobile`) and renders them inline on
 * desktop. Used on landing-b to hide bullet-heavy section bodies
 * behind a tap-to-expand toggle so each section can breathe at mobile
 * width without removing content.
 */
export function MobileCollapse({
    children,
    summary = "Read more",
    summaryOpen = "Show less",
}: {
    children: ReactNode;
    summary?: string;
    summaryOpen?: string;
}) {
    const isMobile = useIsMobile();
    const [open, setOpen] = useState(false);

    if (!isMobile) return <>{children}</>;

    return (
        <div className="lb-mobile-collapse">
            {open && <div className="lb-mobile-collapse-body">{children}</div>}
            <button
                type="button"
                className="lb-mobile-collapse-toggle"
                onClick={() => setOpen((v) => !v)}
                aria-expanded={open}
            >
                {open ? summaryOpen : summary}
            </button>
        </div>
    );
}
