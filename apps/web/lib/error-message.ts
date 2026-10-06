import { ApiError } from "@dw/api-client";
import type { RegionFailure } from "@dw/ui";

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
 * A failed load as the shared region state reads it (`RegionState` in
 * `@dw/ui` owns which code draws which state). A request that never reached
 * the server is the offline state, not an error message (ui-quality §4):
 * `fetch` rejects with a TypeError for that ("Failed to fetch", Firefox's
 * "NetworkError…", Safari's "Load failed").
 */
export function regionFailure(error: unknown): RegionFailure {
  if (error instanceof ApiError) {
    return {
      code: error.body.code,
      message: error.body.message,
      requestId: error.body.request_id ?? null,
    };
  }
  const offline =
    (typeof navigator !== "undefined" && navigator.onLine === false) ||
    (error instanceof TypeError &&
      /fetch|network|load failed/i.test(error.message));
  return { code: null, message: errorMessage(error), offline };
}
