"use client";

import { Fragment, useMemo, type ReactNode } from "react";

/**
 * An answer, rendered as the document it already is.
 *
 * The model writes Markdown - it is asked to, because a ranking of five vendors
 * is a table and a set of findings is a list, and most assistants do it
 * unprompted anyway. Until this file existed the transcript printed that
 * Markdown as preformatted text, so a reader got pipes and asterisks and had to
 * assemble the table in their head. That is a real cost, not a cosmetic one: the
 * whole feature exists so somebody can check a number against the query beside
 * it, and a table nobody can read is a number nobody checks.
 *
 * Shared by every surface that shows a model's answer. Written once because a
 * second subset would be a second set of rules about what the model may emit,
 * and nobody would know which surface followed which.
 *
 * Hand-written rather than `react-markdown` + `remark-gfm`, for two reasons that
 * both matter here. It is a subset - headings, emphasis, code, lists, tables,
 * quotes, rules, links - which is the whole of what the prompt asks for, so a
 * parser covering all of CommonMark would be carrying HTML passthrough,
 * autolinks and reference definitions on behalf of syntax nothing emits. And
 * every branch below produces a React element from a string, so there is no
 * `dangerouslySetInnerHTML` anywhere in the path from model output to the DOM -
 * the same rule the figure renderer follows, for the same reason.
 *
 * What is deliberately NOT supported: raw HTML (rendered as the characters it
 * is), images, footnotes, nested lists past one level. If the model starts
 * needing one, the prompt and this file change together.
 */

/** A `[text](href)` whose href is not one of these is drawn as plain text. */
/** Joins the class names that are set. */
function cn(...names: (string | false | null | undefined)[]): string {
  return names.filter(Boolean).join(" ");
}

const SAFE_LINK = /^(https?:\/\/|\/)/i;

type Block =
  | { kind: "heading"; level: 3 | 4; text: string }
  | { kind: "paragraph"; text: string }
  | { kind: "list"; ordered: boolean; items: string[] }
  | { kind: "quote"; text: string }
  | { kind: "rule" }
  | { kind: "code"; text: string }
  | { kind: "table"; head: string[]; align: Align[]; rows: string[][] };

type Align = "left" | "right" | "center";

/**
 * Inline syntax, in the order it is peeled off.
 *
 * Code first, and that ordering is the only subtle thing here: a backticked
 * `**not bold**` has to survive as literal asterisks, which it only does if the
 * code span is taken before emphasis is looked for.
 */
const INLINE =
  /(`[^`]+`)|(\*\*[^*]+\*\*)|(\*[^*\n]+\*)|(_[^_\n]+_)|(\[[^\]]+\]\([^)\s]+\))/;

function splitCells(line: string): string[] {
  // A table row's outer pipes are decoration; the cells are what is between
  // them. `split` on a trimmed row would otherwise produce a leading and a
  // trailing empty cell and shift every column by one.
  return line
    .replace(/^\s*\|/, "")
    .replace(/\|\s*$/, "")
    .split("|")
    .map((cell) => cell.trim());
}

function alignmentOf(cell: string): Align {
  const right = cell.endsWith(":");
  const left = cell.startsWith(":");
  if (right && left) return "center";
  return right ? "right" : "left";
}

/** Whether this line is a table's `| --- | ---: |` separator. */
function isDelimiter(line: string): boolean {
  const cells = splitCells(line);
  return (
    cells.length > 0 && cells.every((cell) => /^:?-{1,}:?$/.test(cell.trim()))
  );
}

/**
 * Group the text into blocks. One pass, no lookbehind.
 *
 * Blank-line separation is what ends a paragraph or a list, which is how
 * Markdown already behaves and what the model already writes.
 */
function parseBlocks(source: string): Block[] {
  const lines = source.replace(/\r\n?/g, "\n").split("\n");
  const blocks: Block[] = [];
  let index = 0;

  while (index < lines.length) {
    const line = lines[index] ?? "";
    const trimmed = line.trim();

    if (!trimmed) {
      index += 1;
      continue;
    }

    // Fenced code. Kept verbatim, including the SQL a model sometimes shows in
    // prose - the one place where the characters are the content.
    if (trimmed.startsWith("```")) {
      const body: string[] = [];
      index += 1;
      while (
        index < lines.length &&
        !(lines[index] ?? "").trim().startsWith("```")
      ) {
        body.push(lines[index] ?? "");
        index += 1;
      }
      index += 1; // the closing fence, or the end of the text
      blocks.push({ kind: "code", text: body.join("\n") });
      continue;
    }

    if (/^-{3,}$/.test(trimmed) || /^\*{3,}$/.test(trimmed)) {
      blocks.push({ kind: "rule" });
      index += 1;
      continue;
    }

    // Headings. `#` and `##` are folded to level 3: a chat answer sits inside a
    // page that already has an h1 and an h2, and a model writing `#` means "this
    // is my top heading", not "this is the page's title".
    const heading = /^(#{1,4})\s+(.*)$/.exec(trimmed);
    if (heading) {
      blocks.push({
        kind: "heading",
        level: (heading[1] ?? "").length >= 4 ? 4 : 3,
        text: heading[2] ?? "",
      });
      index += 1;
      continue;
    }

    // A table needs its delimiter row to be a table at all; a lone piped line is
    // a sentence with pipes in it.
    if (trimmed.startsWith("|") && isDelimiter(lines[index + 1] ?? "")) {
      const head = splitCells(trimmed);
      const align = splitCells(lines[index + 1] ?? "").map(alignmentOf);
      index += 2;
      const rows: string[][] = [];
      while (
        index < lines.length &&
        (lines[index] ?? "").trim().startsWith("|")
      ) {
        rows.push(splitCells((lines[index] ?? "").trim()));
        index += 1;
      }
      blocks.push({ kind: "table", head, align, rows });
      continue;
    }

    const bullet = /^[-*+]\s+(.*)$/.exec(trimmed);
    const numbered = /^\d+[.)]\s+(.*)$/.exec(trimmed);
    if (bullet || numbered) {
      const ordered = numbered !== null;
      const items: string[] = [];
      while (index < lines.length) {
        const candidate = (lines[index] ?? "").trim();
        const next = ordered
          ? /^\d+[.)]\s+(.*)$/.exec(candidate)
          : /^[-*+]\s+(.*)$/.exec(candidate);
        if (!next) break;
        items.push(next[1] ?? "");
        index += 1;
      }
      blocks.push({ kind: "list", ordered, items });
      continue;
    }

    if (trimmed.startsWith(">")) {
      const body: string[] = [];
      while (
        index < lines.length &&
        (lines[index] ?? "").trim().startsWith(">")
      ) {
        body.push((lines[index] ?? "").trim().replace(/^>\s?/, ""));
        index += 1;
      }
      blocks.push({ kind: "quote", text: body.join(" ") });
      continue;
    }

    // A paragraph runs until a blank line or the start of another block, so a
    // model that wraps its prose does not get one paragraph per line.
    //
    // The first line is consumed unconditionally, and that is what guarantees
    // the outer loop makes progress. Testing the stop conditions before taking
    // anything is how this spun forever: a line starting with `|` whose next
    // line is not a `| --- |` row falls past the table branch, stops here
    // immediately, leaves `index` where it was, and the outer loop reads the
    // same line again.
    //
    // It is not a hypothetical shape - it is every table mid-stream. The header
    // row arrives a chunk before its delimiter, so for one render the text holds
    // exactly that line. Measured on 2026-08-19: a frozen tab and Chrome's
    // out-of-memory page, on the second question of a demo.
    const body: string[] = [trimmed];
    index += 1;
    while (index < lines.length) {
      const candidate = lines[index] ?? "";
      const stop =
        !candidate.trim() ||
        candidate.trim().startsWith("|") ||
        candidate.trim().startsWith(">") ||
        candidate.trim().startsWith("```") ||
        /^(#{1,4})\s+/.test(candidate.trim()) ||
        /^[-*+]\s+/.test(candidate.trim()) ||
        /^\d+[.)]\s+/.test(candidate.trim());
      if (stop) break;
      body.push(candidate.trim());
      index += 1;
    }
    blocks.push({ kind: "paragraph", text: body.join(" ") });
  }

  return blocks;
}

/** Emphasis, code and links, as React nodes. Never as HTML. */
function renderInline(text: string, keyPrefix = ""): ReactNode[] {
  const out: ReactNode[] = [];
  let rest = text;
  let key = 0;

  while (rest) {
    const found = INLINE.exec(rest);
    if (!found || found.index === undefined) {
      out.push(rest);
      break;
    }
    if (found.index > 0) out.push(rest.slice(0, found.index));
    const token = found[0];
    const id = `${keyPrefix}i${key++}`;

    if (token.startsWith("`")) {
      out.push(
        <code
          key={id}
          className="rounded bg-muted px-1 py-0.5 font-mono text-[0.92em] text-foreground"
        >
          {token.slice(1, -1)}
        </code>,
      );
    } else if (token.startsWith("**")) {
      out.push(
        <strong key={id} className="font-semibold text-foreground">
          {token.slice(2, -2)}
        </strong>,
      );
    } else if (token.startsWith("*") || token.startsWith("_")) {
      out.push(<em key={id}>{token.slice(1, -1)}</em>);
    } else {
      const link = /^\[([^\]]+)\]\(([^)\s]+)\)$/.exec(token);
      const label = link?.[1] ?? token;
      const href = link?.[2] ?? "";
      // An unsafe scheme is drawn as its own text rather than dropped: a reader
      // seeing the raw link can judge it, and a silently removed one leaves an
      // answer referring to something that is not there.
      out.push(
        SAFE_LINK.test(href) ? (
          <a
            key={id}
            href={href}
            target="_blank"
            rel="noopener noreferrer"
            className="text-link underline decoration-link/40 underline-offset-2 hover:decoration-link"
          >
            {label}
          </a>
        ) : (
          <Fragment key={id}>{token}</Fragment>
        ),
      );
    }
    rest = rest.slice(found.index + token.length);
  }

  return out;
}

const ALIGN_CLASS: Record<Align, string> = {
  left: "text-left",
  right: "text-right tabular-nums",
  center: "text-center",
};

function renderBlock(block: Block, key: number): ReactNode {
  switch (block.kind) {
    case "heading":
      return block.level === 3 ? (
        <h3
          key={key}
          className="mt-3 text-[13px] font-semibold text-foreground first:mt-0"
        >
          {renderInline(block.text, `h${key}`)}
        </h3>
      ) : (
        <h4
          key={key}
          className="mt-2.5 text-[12.5px] font-semibold text-foreground first:mt-0"
        >
          {renderInline(block.text, `h${key}`)}
        </h4>
      );

    case "paragraph":
      return (
        <p key={key} className="text-[13px] leading-relaxed">
          {renderInline(block.text, `p${key}`)}
        </p>
      );

    case "list": {
      const Tag = block.ordered ? "ol" : "ul";
      return (
        <Tag
          key={key}
          className={cn(
            "space-y-1 pl-5 text-[13px] leading-relaxed",
            block.ordered ? "list-decimal" : "list-disc",
          )}
        >
          {block.items.map((item, position) => (
            <li key={position} className="marker:text-muted-foreground">
              {renderInline(item, `l${key}-${position}`)}
            </li>
          ))}
        </Tag>
      );
    }

    case "quote":
      return (
        <blockquote
          key={key}
          className="border-l-2 border-border pl-3 text-[12.5px] italic leading-relaxed text-muted-foreground"
        >
          {renderInline(block.text, `q${key}`)}
        </blockquote>
      );

    case "rule":
      return <hr key={key} className="border-border" />;

    case "code":
      return (
        <pre
          key={key}
          className="overflow-x-auto rounded-md bg-muted px-2.5 py-2 font-mono text-[11.5px] leading-relaxed text-foreground"
        >
          {block.text}
        </pre>
      );

    case "table":
      return (
        // Its own scroller: a five-column ranking of Vietnamese company names is
        // wider than the bubble, and a table that widens the page pushes the
        // whole transcript sideways.
        <div
          key={key}
          className="overflow-x-auto rounded-md border border-border"
        >
          <table className="w-full border-collapse text-[12px]">
            <thead className="bg-muted/60">
              <tr>
                {block.head.map((cell, column) => (
                  <th
                    key={column}
                    scope="col"
                    className={cn(
                      "whitespace-nowrap border-b border-border px-2.5 py-1.5 font-medium text-muted-foreground",
                      ALIGN_CLASS[block.align[column] ?? "left"],
                    )}
                  >
                    {renderInline(cell, `th${key}-${column}`)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {block.rows.map((row, position) => (
                <tr
                  key={position}
                  className="border-b border-border/60 last:border-0"
                >
                  {row.map((cell, column) => (
                    <td
                      key={column}
                      className={cn(
                        "px-2.5 py-1.5 align-top text-foreground",
                        ALIGN_CLASS[block.align[column] ?? "left"],
                      )}
                    >
                      {renderInline(cell, `td${key}-${position}-${column}`)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      );
  }
}

export function MarkdownAnswer({
  text,
  className,
}: {
  text: string;
  className?: string;
}) {
  // Memoised on the text, which matters only while a turn streams: every token
  // is a new `text`, and without this the whole block list is re-parsed and
  // re-created dozens of times a second. Same output either way - the difference
  // is whether the tab has CPU left to scroll smoothly while it happens.
  const blocks = useMemo(() => parseBlocks(text), [text]);
  return (
    <div className={cn("space-y-2", className)}>
      {blocks.map((block, index) => renderBlock(block, index))}
    </div>
  );
}
