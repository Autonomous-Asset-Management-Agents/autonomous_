import { useEffect, useState } from "react";
import { landingExperiments, type Assignment, type Experiments } from "@/lib/experiments";

/**
 * #4469 (ADR-026 §3): the variation of a landing experiment, fixed on the first render.
 *
 * The assignment is computed synchronously in the state initializer and never changes afterwards,
 * so the first paint already shows the assigned variation — no flicker, no layout shift from a
 * late swap. An exposure is reported once per experiment after mount. Any error renders `control`
 * and is reported with console.warn.
 */
export const useExperiment = (
    key: string,
    control = "control",
    experiments: Experiments = landingExperiments,
): string => {
    const [assignment] = useState<Assignment | null>(() => {
        try {
            return experiments.evaluate(key, control);
        } catch (err) {
            console.warn(`[experiments] "${key}": evaluation failed → control "${control}"`, err);
            return null;
        }
    });

    useEffect(() => {
        if (assignment?.inExperiment) {
            experiments.reportExposure(assignment.experimentKey, assignment.variationId);
        }
    }, [assignment, experiments]);

    return assignment?.value ?? control;
};
