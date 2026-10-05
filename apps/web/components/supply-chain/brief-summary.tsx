import type { BriefSummary, DailyBrief } from "@dw/contracts";
import { groupHeadline } from "./daily-brief";

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
 * label is composed here from the brief, never taken from the model.
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
    <section
      aria-label="Tóm tắt do AI viết"
      className="space-y-2 rounded-md border border-dashed p-4"
    >
      <p className="text-xs font-medium text-muted-foreground">
        Tóm tắt do AI viết — mỗi câu dẫn về nhóm tín hiệu của bản tin bên dưới.
      </p>
      {note && <p className="text-sm">{note}</p>}
      {summary.sentences.length > 0 && (
        <ol className="list-decimal space-y-2 pl-5 text-sm">
          {summary.sentences.map((sentence, index) => (
            <li key={index}>
              <span>{sentence.text}</span>{" "}
              <span className="inline-flex flex-wrap gap-1 align-middle">
                {sentence.group_keys.map((key) => {
                  const group = brief.groups.find((g) => g.key === key);
                  // The server only keeps a sentence whose every key is a
                  // group of this brief; a missing one renders nothing.
                  return group ? (
                    <a
                      key={key}
                      href={`#brief-${key}`}
                      className="rounded bg-muted px-1.5 py-0.5 text-xs text-muted-foreground hover:underline"
                    >
                      {groupHeadline(group)}
                    </a>
                  ) : null;
                })}
              </span>
            </li>
          ))}
        </ol>
      )}
      {summary.status === "written" && summary.dropped > 0 && (
        <p className="text-xs text-muted-foreground">
          {summary.dropped} câu khác bị loại vì không khớp với dữ liệu bản tin.
        </p>
      )}
    </section>
  );
}
