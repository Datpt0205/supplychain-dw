import { useState } from "react";

/**
 * A fresh `Idempotency-Key` for one press of a mutating button.
 *
 * Minted once per press and reused when the same payload is retried, replaced
 * after an edit (ui-quality §3): the API refuses a reused key with another
 * payload. `crypto.randomUUID` needs a secure context; the fallback covers a
 * plain-HTTP dev host.
 */
export function newIdempotencyKey(): string {
  if (typeof crypto.randomUUID === "function") return crypto.randomUUID();
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  return Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
}

/** The key one attempt is sent under: the same values retried keep it, so a
 * retry after a lost answer is not a second step; other values get a new one. */
export function useAttemptKey() {
  const [attempt, setAttempt] = useState<{ key: string; values: string }>();
  return (values: unknown): string => {
    const fingerprint = JSON.stringify(values);
    const key =
      attempt && attempt.values === fingerprint
        ? attempt.key
        : newIdempotencyKey();
    setAttempt({ key, values: fingerprint });
    return key;
  };
}
