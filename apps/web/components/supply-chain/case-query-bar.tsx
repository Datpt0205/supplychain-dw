"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";
import { Alert, Button, Card, Flex, Input } from "antd";
import { RobotOutlined } from "@ant-design/icons";
import type { AIWorkResponse } from "@dw/contracts";
import { useAuth } from "../../lib/auth/auth-context";
import { errorMessage } from "../../lib/error-message";
import { apiClient } from "../../lib/session";
import { CaseQueryAnswer } from "./case-query-answer";

/**
 * The command bar: ask about PO cases and product-development cases in words, get
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
    <Card size="small">
      <Flex vertical gap="middle">
        <form onSubmit={submit} className="flex gap-2" role="search">
          <Input
            aria-label="Hỏi về PO case hoặc hồ sơ phát triển"
            placeholder="Ví dụ: PO của NCC Sunhouse đang chờ đặt cọc; hồ sơ SP-028 tới đâu rồi"
            value={question}
            maxLength={500}
            onChange={(event) => setQuestion(event.target.value)}
          />
          <Button
            type="primary"
            htmlType="submit"
            icon={<RobotOutlined aria-hidden />}
            loading={pending}
            disabled={!question.trim()}
          >
            {pending ? "Đang hỏi…" : "Hỏi"}
          </Button>
        </form>
        {/* Announced without moving focus off the input: an answer or a
            failure arriving is a status change a screen reader must hear. */}
        <div role="status" aria-live="polite">
          {answer && <CaseQueryAnswer answer={answer} />}
        </div>
        {error != null && (
          <Alert type="error" showIcon title={errorMessage(error)} />
        )}
      </Flex>
    </Card>
  );
}
