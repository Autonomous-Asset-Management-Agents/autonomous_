/**
 * Waitlist submission — writes the visitor's email to Firestore collection
 * `waitlist/{autoId}` with a server timestamp. No read is ever performed from
 * the client; the collection is write-only per the rules snippet in
 * INTEGRATION_NOTES.md.
 *
 * App Check is intentionally DISABLED for local development. Enable via
 * reCAPTCHA v3 before going live to prevent bot spam.
 */
import { addDoc, collection, serverTimestamp } from "firebase/firestore";
import { db } from "@/lib/firebase";

const EMAIL_REGEX = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

export function isValidEmail(email: string): boolean {
    return EMAIL_REGEX.test(email.trim());
}

export async function submitWaitlist(emailRaw: string, source: string = "landing-b"): Promise<void> {
    const email = emailRaw.trim().toLowerCase();
    if (!isValidEmail(email)) {
        throw new Error("invalid_email");
    }
    await addDoc(collection(db, "waitlist"), {
        email,
        source,
        createdAt: serverTimestamp(),
        userAgent: typeof navigator !== "undefined" ? navigator.userAgent : null,
    });
}
