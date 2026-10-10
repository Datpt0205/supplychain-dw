"use client";

import { useState } from "react";
import { Alert, Button, Card, Flex, Form, Input, Tag, Typography } from "antd";
import type { CaseAnswer } from "@dw/api-client";
import { errorMessage } from "../../lib/error-message";
import { useOnline } from "../../lib/hooks/use-online";
import { apiClient } from "../../lib/session";

const OFFLINE = "Không có kết nối mạng. Kết nối lại rồi thử lại.";
export const ASSISTANT_NOTE =
  "AI trả lời chỉ từ hồ sơ này (lịch sử, BM04, số đo, chứng từ đã đọc mà bạn được xem); mỗi câu kèm nguồn, câu không kiểm được bị bỏ. Trợ lý chỉ đọc, không làm bước nào.";
export const AI_WRITTEN = "AI viết, đã kiểm dẫn chứng";
const MAX_QUESTION = 500;

interface AskValues {
  question: string;
}

/**
 * Trợ lý hồ sơ chỉ đọc (ticket ai-automation/19): một câu hỏi về hồ sơ này,
 * trả lời từ chính hồ sơ, mỗi câu kèm nguồn. Không đủ bằng chứng thì nói vậy.
 * Câu trả lời là của AI, đã được hệ thống kiểm, không phải giá trị người duyệt.
 */
export function CaseAssistantCard({
  caseKind,
  caseId,
}: {
  caseKind: "po" | "product";
  caseId: string;
}) {
  const [form] = Form.useForm<AskValues>();
  const online = useOnline();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [answer, setAnswer] = useState<CaseAnswer | null>(null);

  const ask = async ({ question }: AskValues) => {
    setBusy(true);
    setError(null);
    try {
      setAnswer(await apiClient().askAboutCase(caseKind, caseId, question));
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card title="Hỏi về hồ sơ">
      <Flex vertical gap="small">
        <Typography.Text>{ASSISTANT_NOTE}</Typography.Text>
        <Form<AskValues>
          form={form}
          layout="vertical"
          validateTrigger="onBlur"
          onFinish={(values) => void ask(values)}
        >
          <Form.Item
            name="question"
            label="Câu hỏi"
            rules={[
              { required: true, whitespace: true, message: "Nhập câu hỏi" },
              {
                max: MAX_QUESTION,
                message: `Câu hỏi tối đa ${MAX_QUESTION} ký tự`,
              },
            ]}
          >
            <Input.TextArea
              rows={2}
              placeholder="Ví dụ: BM04 ghi MOQ bao nhiêu?"
            />
          </Form.Item>
          <Button
            type="primary"
            htmlType="submit"
            loading={busy}
            disabled={!online}
          >
            Hỏi
          </Button>
          {!online && <Typography.Text> {OFFLINE}</Typography.Text>}
        </Form>
        {error && <Alert type="error" showIcon title={error} />}
        {answer && (
          <Flex vertical gap="small" role="status">
            {answer.answered ? (
              <>
                <Tag>{AI_WRITTEN}</Tag>
                {answer.sentences.map((sentence, index) => (
                  <Flex key={index} vertical gap={2}>
                    <Typography.Text>{sentence.text}</Typography.Text>
                    <Typography.Text type="secondary">
                      Nguồn: {sentence.cites.map((c) => c.label).join(", ")}
                    </Typography.Text>
                  </Flex>
                ))}
              </>
            ) : (
              <Typography.Text>{answer.text}</Typography.Text>
            )}
          </Flex>
        )}
      </Flex>
    </Card>
  );
}
