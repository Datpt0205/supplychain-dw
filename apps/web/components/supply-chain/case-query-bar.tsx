"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";
import { Sparkles } from "lucide-react";
import type { AIWorkResponse } from "@dw/contracts";
import { Button, Card, CardContent, Input } from "@dw/ui";
import { useAuth } from "../../lib/auth/auth-context";
import { errorMessage } from "../../lib/error-message";
import { apiClient } from "../../lib/session";
import { CaseQueryAnswer } from "./case-query-answer";

/**
 * The command bar: ask about PO cases in words, get
 * the answer as structured work rendered by `CaseQueryAnswer`.
 *
 * Asks on submit only, never per keystroke — every question is a model
 * call. A newer question aborts the one still in flight, so a slow answer
 * cannot land on top of a newer one. Hiding this bar would not be
 * authorization; the server refuses a caller who may not read cases.
 */
export function CaseQueryBar() {
  const { active } = useAuth();
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState<AIWorkResponse | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [pending, setPending] = useState(false);
  const inflight = useRef<AbortController | null>(null);

  // An answer belongs to the workspace it was asked in: switching workspace
  // must not leave one workspace's cases on screen under another's name.
  useEffect(() => {
    inflight.current?.abort();
    setAnswer(null);
    setError(null);
  }, [active?.workspaceId]);

  useEffect(() => () => inflight.current?.abort(), []);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const text = question.trim();
    if (!text) return;
    inflight.current?.abort();
    const controller = new AbortController();
    inflight.current = controller;
    setPending(true);
    setError(null);
    try {
      const result = await apiClient().askCaseQuery(text, controller.signal);
      if (!controller.signal.aborted) setAnswer(result);
    } catch (failure) {
      if (!controller.signal.aborted) {
        setAnswer(null);
        setError(failure);
      }
    } finally {
      if (inflight.current === controller) setPending(false);
    }
  };

  return (
    <Card>
      <CardContent className="space-y-4 pt-5">
        <form onSubmit={submit} className="flex gap-2" role="search">
          <Input
            aria-label="Hỏi về PO case"
            placeholder="Ví dụ: PO của NCC Sunhouse đang chờ đặt cọc"
            value={question}
            maxLength={500}
            onChange={(event) => setQuestion(event.target.value)}
          />
          <Button type="submit" disabled={pending || !question.trim()}>
            <Sparkles />
            {pending ? "Đang hỏi…" : "Hỏi"}
          </Button>
        </form>
        {/* Announced without moving focus off the input: an answer or a
            failure arriving is a status change a screen reader must hear. */}
        <div role="status" aria-live="polite">
          {answer && <CaseQueryAnswer answer={answer} />}
        </div>
        {error != null && (
          <p role="alert" className="text-sm text-destructive">
            {errorMessage(error)}
          </p>
        )}
      </CardContent>
    </Card>
  );
}
