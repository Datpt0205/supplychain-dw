"use client";

import { useSyncExternalStore } from "react";

/**
 * Whether the browser believes it is online, kept current by its own events.
 *
 * `ui-quality.md` §4 puts offline detection in the `@dw/ui` shell, which does
 * not draw an offline banner yet; until it does, a screen that must disable
 * its mutations reads this. When the shell gains the banner, this hook moves
 * there and screens keep calling it.
 */
function subscribe(onChange: () => void): () => void {
  window.addEventListener("online", onChange);
  window.addEventListener("offline", onChange);
  return () => {
    window.removeEventListener("online", onChange);
    window.removeEventListener("offline", onChange);
  };
}

export function useOnline(): boolean {
  return useSyncExternalStore(
    subscribe,
    () => navigator.onLine,
    // The server cannot know; render online and let the client correct it.
    () => true,
  );
}
