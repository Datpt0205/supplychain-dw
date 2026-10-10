"use client";

import { Button } from "antd";
import { RegionState, stateForError } from "@dw/ui";
import { toRegionError } from "../lib/error-message";

/**
 * A region that failed to load, drawn by the shared `RegionState` from the
 * server's code: a 403 reads as "no access", a 404 as "not found", and only a
 * real failure offers a retry with its request id.
 */
export function LoadError({
  error,
  onRetry,
  compact,
}: {
  error: unknown;
  onRetry?: () => void;
  compact?: boolean;
}) {
  const state = stateForError(toRegionError(error));
  const retry =
    onRetry && (state.kind === "error" || state.kind === "offline") ? (
      <Button onClick={onRetry}>Thử lại</Button>
    ) : undefined;
  return (
    <RegionState
      kind={state.kind}
      description={state.message}
      requestId={state.requestId}
      action={retry}
      compact={compact}
    />
  );
}
