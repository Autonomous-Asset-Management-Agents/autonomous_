import { Fragment, type ReactNode } from "react";

const MARKE = "autonomous_";

/** Sets every "autonomous_" in a copy text as the bold brand mark with the accent underscore. */
export function mitMarke(text: string): ReactNode {
    const teile = text.split(MARKE);
    return teile.map((teil, i) => (
        <Fragment key={i}>
            {i > 0 && <b>autonomous<span style={{ color: "var(--lb-accent, #00c27a)" }}>_</span></b>}
            {teil}
        </Fragment>
    ));
}
