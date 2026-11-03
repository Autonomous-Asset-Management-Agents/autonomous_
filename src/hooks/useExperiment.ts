import { useState } from "react";
import { landingExperiments, type Experiments } from "@/lib/experiments";

/**
 * #4469 / #4869 (ADR-026 Weg D): the variation of a landing experiment, fixed on the first render.
 *
 * The server writes the variant into <meta name="aaa-experiments">; it is read synchronously in the
 * state initializer and never changes afterwards, so the first paint already shows it — no
 * flicker, no layout shift from a late swap. The exposure is counted by the server, not here.
 * Any error renders `control` and is reported with console.warn.
 */
export const useExperiment = (
    key: string,
    control = "control",
    experiments: Experiments = landingExperiments,
): string => {
    const [value] = useState<string>(() => {
        try {
            return experiments.evaluate(key, control).value;
        } catch (err) {
            console.warn(`[experiments] "${key}": evaluation failed → control "${control}"`, err);
            return control;
        }
    });
    return value;
};
