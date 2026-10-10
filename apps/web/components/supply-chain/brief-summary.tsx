import { Card, Flex, Typography } from "antd";
import type { BriefSummary, DailyBrief } from "@dw/contracts";
import { StatusTag } from "@dw/ui";
import { groupHeadline } from "./daily-brief";
import { OriginTag } from "./origin-tag";

/** What the page says for every outcome but a written summary. */
function outcomeNote(summary: BriefSummary): string | null {
  switch (summary.status) {
    case "written":
      return null;
    case "nothing_kept":
      return `AI đã viết ${summary.dropped} câu nhưng không câu nào khớp với dữ liệu bản tin, nên không hiển thị câu nào.`;
    case "nothing_written":
      return "AI không viết câu tóm tắt nào.";
    case "nothing_to_summarize":
      return "Bản tin không có nhóm nào để tóm tắt.";
    case "unavailable":
      return "Chưa tóm tắt được: câu trả lời của AI không đúng định dạng. Thử lại sau.";
    default: {
      const unreachable: never = summary.status;
      return unreachable;
    }
  }
}

/**
 * A model's summary of the brief — only the sentences the server checked
 * against the groups they cite. Each sentence is shown as AI-written, next
 * to links to those groups, so a reader can see what it rests on; a group's
 * label is composed here from the brief, never taken from the model
 * (ui-quality §7).
 */
export function BriefSummaryPanel({
  brief,
  summary,
}: {
  brief: DailyBrief;
  summary: BriefSummary;
}) {
  const note = outcomeNote(summary);
  return (
    <section aria-label="Tóm tắt do AI viết">
      <Card size="small">
        <Flex vertical gap="small">
          <Flex wrap gap="small" align="center">
            <OriginTag origin="model_written" />
            <Typography.Text type="secondary">
              Mỗi câu dẫn về nhóm tín hiệu của bản tin bên dưới.
            </Typography.Text>
          </Flex>
          {note && <Typography.Text>{note}</Typography.Text>}
          {summary.sentences.length > 0 && (
            <ol className="m-0 flex list-decimal flex-col gap-2 ps-5">
              {summary.sentences.map((sentence, index) => (
                // eslint-disable-next-line react/no-array-index-key
                <li key={index}>
                  <Typography.Text>{sentence.text}</Typography.Text>{" "}
                  <span className="inline-flex flex-wrap gap-1 align-middle">
                    {sentence.group_keys.map((key) => {
                      const group = brief.groups.find((g) => g.key === key);
                      // The server only keeps a sentence whose every key is
                      // a group of this brief; a missing one renders nothing.
                      return group ? (
                        <a key={key} href={`#brief-${key}`}>
                          <StatusTag tone="gray">
                            {groupHeadline(group)}
                          </StatusTag>
                        </a>
                      ) : null;
                    })}
                  </span>
                </li>
              ))}
            </ol>
          )}
          {summary.status === "written" && summary.dropped > 0 && (
            <Typography.Text type="secondary">
              {summary.dropped} câu khác bị loại vì không khớp với dữ liệu bản
              tin.
            </Typography.Text>
          )}
        </Flex>
      </Card>
    </section>
  );
}
