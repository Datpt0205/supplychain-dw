"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Newspaper, Sparkles } from "lucide-react";
import type { DailyBriefSummary } from "@dw/contracts";
import { Button, Skeleton } from "@dw/ui";
import { PageHeading } from "../../../components/page-heading";
import { BriefSummaryPanel } from "../../../components/supply-chain/brief-summary";
import { DailyBriefView } from "../../../components/supply-chain/daily-brief";
import { useAuth } from "../../../lib/auth/auth-context";
import { errorMessage } from "../../../lib/error-message";
import { apiClient } from "../../../lib/session";
import { useCachedResource } from "../../../lib/use-cached-resource";

/**
 * The daily management brief. `GET /daily-brief` groups every deterministic
 * signal — SLA overruns, silent suppliers, exception states, money waiting
 * on the buyer, undecided approvals, reported delays, the last day's moves —
 * and orders the groups by the tenant's own brief policy.
 *
 * The AI summary is asked for with a button, never on load: every summary is
 * a model call. Its reply carries the brief it was checked against, and the
 * page then shows that brief, so every sentence's cited groups are on screen.
 */
export default function DailyBriefPage() {
  const { active } = useAuth();
  const {
    data: brief,
    loading,
    error,
  } = useCachedResource(
    "supply-chain:daily-brief",
    useCallback(() => apiClient().getDailyBrief(), []),
  );
  const [summarized, setSummarized] = useState<DailyBriefSummary | null>(null);
  const [summaryError, setSummaryError] = useState<unknown>(null);
  const [pending, setPending] = useState(false);
  const inflight = useRef<AbortController | null>(null);

  // A summary belongs to the workspace it was written for.
  useEffect(() => {
    inflight.current?.abort();
    setSummarized(null);
    setSummaryError(null);
  }, [active?.workspaceId]);

  useEffect(() => () => inflight.current?.abort(), []);

  const summarize = async () => {
    inflight.current?.abort();
    const controller = new AbortController();
    inflight.current = controller;
    setPending(true);
    setSummaryError(null);
    try {
      const result = await apiClient().summarizeDailyBrief(controller.signal);
      if (!controller.signal.aborted) setSummarized(result);
    } catch (failure) {
      if (!controller.signal.aborted) setSummaryError(failure);
    } finally {
      if (inflight.current === controller) setPending(false);
    }
  };

  const shown = summarized?.brief ?? brief;

  return (
    <div className="mx-auto max-w-4xl space-y-6">
      <PageHeading
        icon={Newspaper}
        title="Bản tin hôm nay"
        description="Những việc cần xử lý, gom theo tín hiệu xác định và xếp theo thứ tự ưu tiên của công ty — không suy diễn, không chấm điểm."
      />
      {loading && !shown ? (
        <Skeleton className="h-96 w-full" />
      ) : (error != null && !summarized) || !shown ? (
        // A failed load is never an empty brief: "nothing to handle" read
        // off a 403 or an outage is an all-clear nobody gave.
        <p className="text-sm text-destructive">
          Không tải được bản tin:{" "}
          {error instanceof Error ? error.message : "lỗi không xác định"}
        </p>
      ) : (
        <>
          <div className="flex justify-end">
            <Button
              type="button"
              variant="outline"
              onClick={summarize}
              disabled={pending}
            >
              <Sparkles />
              {pending ? "Đang tóm tắt…" : "Tóm tắt bằng AI"}
            </Button>
          </div>
          {/* Announced without moving focus: a summary or a failure arriving
              is a status change a screen reader must hear. */}
          <div role="status" aria-live="polite">
            {summarized && (
              <BriefSummaryPanel
                brief={summarized.brief}
                summary={summarized.summary}
              />
            )}
          </div>
          {summaryError != null && (
            <p role="alert" className="text-sm text-destructive">
              {errorMessage(summaryError)}
            </p>
          )}
          <DailyBriefView brief={shown} />
        </>
      )}
    </div>
  );
}
