import { ApiError } from "@dw/api-client";

/**
 * The sentence to show a person when a call fails.
 *
 * `ApiError.message` is `"<code>: <sentence>"` because the code is what a
 * developer reading a stack trace needs. A seller reading a toast does not:
 * "validation_failed: hai đầu quan hệ chưa có trên bản đồ" leaks a machine
 * word into the product. The server already writes the sentence in Vietnamese
 * — take that, and keep the code for the console.
 */
export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) return error.body.message;
  if (error instanceof Error) return error.message;
  return String(error);
}

/** The server's machine code, when there is one — for branching, not display. */
export function errorCode(error: unknown): string | null {
  return error instanceof ApiError ? error.body.code : null;
}

/**
 * A failure, as the shared `RegionState` reads it (`stateForError` in
 * `@dw/ui` owns which code draws which state): the server's code and sentence
 * (never `"<code>: …"`), or `"offline"` when the request never reached the
 * server (ui-quality §4) — the browser says it is offline, or `fetch`
 * rejected with its network TypeError ("Failed to fetch", Firefox's
 * "NetworkError…", Safari's "Load failed").
 */
export function toRegionError(
  error: unknown,
): { code: string; message: string; requestId?: string | null } | "offline" {
  if (error instanceof ApiError) {
    return {
      code: error.body.code,
      message: error.body.message,
      requestId: error.body.request_id ?? null,
    };
  }
  if (
    (typeof navigator !== "undefined" && navigator.onLine === false) ||
    (error instanceof TypeError &&
      /fetch|network|load failed/i.test(error.message))
  ) {
    return "offline";
  }
  return { code: "internal", message: errorMessage(error) };
}
