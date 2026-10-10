import type { Approval } from "@dw/contracts";
import { Descriptions } from "antd";
import { PRODUCT_APPROVAL_TITLE } from "./supply-chain/product-case-labels";

/**
 * How a tool approval is shown to the person deciding it.
 *
 * Shared by the approvals inbox and a context's own agent panel: both ask a
 * human to authorize the same interrupt, so both must show the same thing —
 * what is about to be written.
 */

/**
 * The interrupt raised by the tool bridge nests the tool's arguments one level
 * down, next to the tool name and its versions:
 *
 *     {approval_type, reason, tool, tool_version, payload: {...arguments}}
 *
 * Rendering the outer dict put four internal identifiers on the card and turned
 * the one field that matters into the string "[object Object]" — so a person
 * approved a write without ever seeing the value being written.
 */
const ARGUMENTS_KEY = "payload";

/**
 * What the call will do, for a tool whose arguments only point at it.
 *
 * A tool that takes only an artifact id - on purpose, so the body cannot drift
 * between this card and the stored record - leaves a person authorizing a write
 * with a UUID as the only thing on screen. Such a tool fills this in from the
 * same stored version the write reads.
 */
const PREVIEW_KEY = "preview";

/**
 * PLUG-IN POINT: a bounded context maps its own `approval_type` values to the
 * sentence a person reads on the card. An unmapped type falls back to the
 * generic title below — the card still shows every argument, so nothing about
 * the decision is hidden, only its headline is less specific.
 */
const APPROVAL_TITLE: Record<string, string> = { ...PRODUCT_APPROVAL_TITLE };

/**
 * PLUG-IN POINT: field names as the person deciding knows them. An unmapped
 * field still shows, under its raw name: on this card, an unlabelled value
 * beats a hidden one.
 */
const FIELD_LABEL: Record<string, string> = {};

export function approvalTitle(approvalType: string): string {
  return APPROVAL_TITLE[approvalType] ?? "Yêu cầu phê duyệt";
}

type Row = { field: string; label: string; value: string };

function rowsUnder(payload: Approval["payload"], key: string): Row[] {
  const section = payload[key];
  if (
    typeof section !== "object" ||
    section === null ||
    Array.isArray(section)
  ) {
    return [];
  }
  return (
    Object.entries(section)
      .filter(
        ([, value]) => value !== null && value !== undefined && value !== "",
      )
      // Structure does not survive String(): an argument holding a list or an
      // object renders as "[object Object]" and tells the approver nothing. A
      // tool whose arguments have shape supplies a preview instead - a list of
      // changes, say, already rendered as text.
      .filter(([, value]) => typeof value !== "object")
      .map(([field, value]) => ({
        field,
        label: FIELD_LABEL[field] ?? field,
        value: String(value),
      }))
  );
}

/**
 * What the tool will do, then what it will run with.
 *
 * The effect comes first because it is what the decision is about; the
 * arguments stay because for most tools they are the effect, and for the rest
 * they are still the record of what was asked for.
 */
export function toolArgumentRows(payload: Approval["payload"]): Row[] {
  const preview = rowsUnder(payload, PREVIEW_KEY);
  // A preview naming the same key is showing a strictly better version of it -
  // "old → new" where the argument holds only "new". Dropping the duplicate
  // also keeps `field` unique, which the render below uses as its React key:
  // two rows with one key is a warning whenever a tool previews a field it
  // also passes as an argument, and would be every row of an update.
  const shown = new Set(preview.map((row) => row.field));
  return [
    ...preview,
    ...rowsUnder(payload, ARGUMENTS_KEY).filter((row) => !shown.has(row.field)),
  ];
}

/**
 * Uncapped and wrapped, both deliberately. A truncated rationale and a dropped
 * ninth field are the two ways this card can lie about what it is authorizing.
 */
export function ToolApprovalPayload({
  payload,
}: {
  payload: Approval["payload"];
}) {
  const rows = toolArgumentRows(payload);
  if (rows.length === 0) return null;
  return (
    <Descriptions
      size="small"
      bordered
      column={1}
      items={rows.map((row) => ({
        key: row.field,
        label: row.label,
        children: (
          <span className="whitespace-pre-wrap break-words">{row.value}</span>
        ),
      }))}
    />
  );
}
