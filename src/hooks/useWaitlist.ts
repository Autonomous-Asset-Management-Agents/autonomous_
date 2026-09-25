import { useState } from "react";
import { submitWaitlist, isValidEmail } from "@/lib/waitlist";

export type WaitlistState = "idle" | "submitting" | "thanks" | "error";

export function useWaitlist(source: string = "landing-b/hero-cta") {
    const [email, setEmail] = useState("");
    const [wlState, setWlState] = useState<WaitlistState>("idle");
    const [wlError, setWlError] = useState<string | null>(null);

    const submit = async (e?: React.FormEvent) => {
        if (e) {
            e.preventDefault();
        }
        if (wlState === "submitting") return;
        if (!isValidEmail(email)) {
            setWlState("error");
            setWlError("Please enter a valid email address.");
            return;
        }
        
        setWlState("submitting");
        setWlError(null);
        
        try {
            await submitWaitlist(email, source);
            setWlState("thanks");
            setEmail("");
        } catch (err) {
            console.warn("waitlist submit failed", err);
            setWlState("error");
            setWlError("Something went wrong. Please try again in a moment.");
        }
    };

    return {
        email,
        setEmail,
        wlState,
        wlError,
        submit
    };
}
