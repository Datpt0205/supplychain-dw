import Link from "next/link";
import type {
  AIWorkResponse,
  DataView,
  GroundedField,
  POCase,
  ProductCaseRef,
} from "@dw/contracts";
import { Flex, Table, Typography, type TableColumnsType } from "antd";
import { StatusTag } from "@dw/ui";
import { poCasesHref } from "../../lib/supply-chain/po-case-filter";
import { productCasesHref } from "../../lib/supply-chain/product-case-filter";
import { CASE_STATE_LABEL, CaseStateTag } from "./case-state-badge";
import { poReferenceLabel } from "./po-reference";
import {
  PRODUCT_DEV_STATE_LABEL,
  ProductDevStateTag,
} from "./product-case-labels";

/**
 * Renders one command-bar answer — structured data, never model markup.
 *
 * Everything on screen is composed HERE from the answer's structured fields:
 * the sentence, the "understood as" line, and every link — built from
 * resolved values through `poCasesHref` / the case id, never taken from text
 * a model wrote. `data_view` is a closed union rendered by an exhaustive
 * switch; a type this file does not know cannot reach it (the response
 * schema rejects it first).
 */
export function CaseQueryAnswer({ answer }: { answer: AIWorkResponse }) {
  return (
    <Flex vertical gap="small">
      <Typography.Text strong>{summary(answer)}</Typography.Text>
      <Understood answer={answer} />
      {answer.outcome === "supplier_ambiguous" && (
        <SupplierCandidates answer={answer} />
      )}
      {(answer.outcome === "category_ambiguous" ||
        answer.outcome === "pic_ambiguous") && (
        <NamedCandidates answer={answer} />
      )}
      {answer.data_view && (
        <DataViewBlock view={answer.data_view} answer={answer} />
      )}
    </Flex>
  );
}

const FIELD_LABEL: Record<GroundedField, string> = {
  supplier: "nhà cung cấp",
  po_reference: "mã PO",
  state: "trạng thái",
  active_only: "chỉ case đang chạy",
  proposal_code: "mã đề xuất",
  product_state: "trạng thái hồ sơ phát triển",
  category: "Category",
  pic: "người phụ trách",
  mine: "hồ sơ của bạn",
};

function fieldList(fields: GroundedField[]): string {
  return fields.map((field) => FIELD_LABEL[field]).join(", ");
}

function quoteFor(answer: AIWorkResponse, field: GroundedField): string | null {
  return answer.citations.find((c) => c.field === field)?.quote ?? null;
}

/** Whether the answer applied any filter at all — unfiltered rows are the
 * newest cases, never "a match" for the question. */
function isFiltered(answer: AIWorkResponse): boolean {
  const { understood } = answer;
  return (
    understood.state !== null ||
    understood.supplier_name !== null ||
    understood.active_only ||
    understood.product_state !== null ||
    understood.category !== null ||
    understood.pic_user_id !== null
  );
}

/** The product case list narrowed exactly as the answer was. */
function productListHref(answer: AIWorkResponse): string {
  const { understood } = answer;
  return productCasesHref({
    state: understood.product_state ?? undefined,
    pic: understood.pic_user_id ?? undefined,
    category: understood.category ?? undefined,
  });
}

/** One sentence per outcome — composed from the answer, not written by a
 * model. */
function summary(answer: AIWorkResponse): string {
  const rows =
    answer.data_view?.type === "case_table" ||
    answer.data_view?.type === "product_case_table"
      ? answer.data_view.rows
      : [];
  const hasMore =
    (answer.data_view?.type === "case_table" ||
      answer.data_view?.type === "product_case_table") &&
    answer.data_view.has_more;
  const supplier = quoteFor(answer, "supplier");
  const po = answer.understood.po_reference ?? quoteFor(answer, "po_reference");
  const code =
    answer.understood.proposal_code ?? quoteFor(answer, "proposal_code");
  switch (answer.outcome) {
    case "list": {
      const filtered = isFiltered(answer);
      // No filter applied is not "these match your question" — it is the
      // newest cases, and the sentence has to say so.
      if (!filtered)
        return rows.length === 0
          ? "Chưa có PO case nào."
          : `Chưa áp dụng bộ lọc nào — đây là ${rows.length} case mới nhất.`;
      if (rows.length === 0) return "Không có case nào khớp.";
      return hasMore
        ? `Hiển thị ${rows.length} case đầu tiên khớp câu hỏi.`
        : `${rows.length} case khớp câu hỏi.`;
    }
    case "product_list": {
      if (!isFiltered(answer))
        return rows.length === 0
          ? "Chưa có hồ sơ phát triển nào."
          : `Chưa áp dụng bộ lọc nào — đây là ${rows.length} hồ sơ mới nhất.`;
      if (rows.length === 0) return "Không có hồ sơ phát triển nào khớp.";
      return hasMore
        ? `Hiển thị ${rows.length} hồ sơ đầu tiên khớp câu hỏi.`
        : `${rows.length} hồ sơ khớp câu hỏi.`;
    }
    case "product_open":
      return `Hồ sơ ${code ?? ""}`.trim() + ":";
    case "proposal_code_missing":
      return "Câu hỏi chưa nêu mã đề xuất nào.";
    case "category_not_found":
      return `Không có Category “${quoteFor(answer, "category") ?? ""}” trong danh sách.`;
    case "category_ambiguous":
      return `“${quoteFor(answer, "category") ?? ""}” khớp nhiều Category — hỏi rõ hơn:`;
    case "pic_not_found":
      return `Không tìm thấy người “${quoteFor(answer, "pic") ?? ""}” trong workspace.`;
    case "pic_ambiguous":
      return `“${quoteFor(answer, "pic") ?? ""}” khớp nhiều người — hỏi rõ hơn:`;
    case "product_not_found":
      return `Không tìm thấy hồ sơ phát triển “${code ?? ""}”.`;
    case "product_ambiguous":
      return `Có nhiều hồ sơ khớp “${code ?? ""}” — chọn một:`;
    case "open":
      return `Case ${po ?? ""}`.trim() + ":";
    case "not_understood": {
      // Two different reasons, never merged: saying "no grounds for the PO
      // code" about a question that states the PO code would be false.
      const reasons: string[] = [];
      if (answer.ignored_fields.length > 0)
        reasons.push(
          `không tìm thấy căn cứ trong câu hỏi cho ${fieldList(answer.ignored_fields)}`,
        );
      if (answer.unusable_fields.length > 0)
        reasons.push(
          `câu hỏi có nêu ${fieldList(answer.unusable_fields)} nhưng loại câu trả lời này không áp dụng được — hãy hỏi riêng`,
        );
      return reasons.length > 0
        ? `Chưa hiểu câu hỏi: ${reasons.join("; ")}.`
        : "Chưa hiểu câu hỏi này. Thử hỏi về PO theo nhà cung cấp, trạng thái hoặc mã PO, hoặc về hồ sơ phát triển theo trạng thái, Category, PIC hoặc mã đề xuất.";
    }
    case "supplier_not_found":
      return `Không tìm thấy nhà cung cấp “${supplier ?? ""}”.`;
    case "supplier_ambiguous":
      return `“${supplier ?? ""}” khớp nhiều nhà cung cấp — chọn một:`;
    case "po_reference_missing":
      return "Câu hỏi chưa nêu mã PO nào.";
    case "po_not_found":
      return `Không tìm thấy PO “${po ?? ""}”.`;
    case "po_ambiguous":
      return `Có nhiều PO khớp “${po ?? ""}” — chọn một:`;
    default: {
      const unreachable: never = answer.outcome;
      return unreachable;
    }
  }
}

/** "Hiểu là: …" — each applied value next to the question's own words it
 * came from, so a person can see why a filter applied. */
function Understood({ answer }: { answer: AIWorkResponse }) {
  if (
    answer.outcome !== "list" &&
    answer.outcome !== "open" &&
    answer.outcome !== "product_list" &&
    answer.outcome !== "product_open"
  )
    return null;
  const { understood } = answer;
  const parts: { label: string; quote: string | null }[] = [];
  if (understood.supplier_name)
    parts.push({
      label: `Nhà cung cấp: ${understood.supplier_name}`,
      quote: quoteFor(answer, "supplier"),
    });
  if (understood.state)
    parts.push({
      label: `Trạng thái: ${CASE_STATE_LABEL[understood.state]}`,
      quote: quoteFor(answer, "state"),
    });
  if (understood.active_only)
    parts.push({
      label: "Chỉ case đang chạy",
      quote: quoteFor(answer, "active_only"),
    });
  // A Category and a PIC are shown by the question's own words: what code
  // resolved them to is the filter the list link carries.
  if (understood.category)
    parts.push({ label: "Category", quote: quoteFor(answer, "category") });
  if (understood.pic_user_id) {
    const mine = quoteFor(answer, "mine");
    parts.push(
      mine
        ? { label: "Hồ sơ của bạn", quote: mine }
        : { label: "Người phụ trách", quote: quoteFor(answer, "pic") },
    );
  }
  if (understood.product_state)
    parts.push({
      label: `Trạng thái: ${PRODUCT_DEV_STATE_LABEL[understood.product_state]}`,
      quote: quoteFor(answer, "product_state"),
    });
  if (parts.length === 0) return null;
  return (
    <Flex wrap gap="small" align="center">
      <Typography.Text type="secondary">Hiểu là:</Typography.Text>
      {parts.map((part) => (
        <StatusTag key={part.label} tone="gray">
          {part.label}
          {part.quote && ` (từ “${part.quote}”)`}
        </StatusTag>
      ))}
    </Flex>
  );
}

/** How many candidates to list before summarizing the rest — a mention
 * like "co" can match most of a tenant's suppliers. */
const MAX_CANDIDATES_SHOWN = 10;

/** Each candidate is a stored supplier name; picking one opens the list the
 * question would have given — its state and active-only filter included —
 * built by code. */
function SupplierCandidates({ answer }: { answer: AIWorkResponse }) {
  const { understood } = answer;
  const shown = answer.candidates.slice(0, MAX_CANDIDATES_SHOWN);
  const hidden = answer.candidates.length - shown.length;
  return (
    <ul className="m-0 flex list-none flex-wrap items-center gap-3 p-0">
      {shown.map((name) => (
        <li key={name}>
          <Link
            href={poCasesHref({
              supplierName: name,
              state: understood.state ?? undefined,
              activeOnly: understood.active_only,
            })}
          >
            {name}
          </Link>
        </li>
      ))}
      {hidden > 0 && (
        <li>
          <Typography.Text type="secondary">
            và {hidden} nhà cung cấp khác — hỏi cụ thể hơn để thu hẹp.
          </Typography.Text>
        </li>
      )}
    </ul>
  );
}

/** Category labels or people's names the question matched more than one of:
 * shown, never picked from. */
function NamedCandidates({ answer }: { answer: AIWorkResponse }) {
  const shown = answer.candidates.slice(0, MAX_CANDIDATES_SHOWN);
  return (
    <Flex wrap gap="small">
      {shown.map((name, index) => (
        <StatusTag key={`${name}-${index}`} tone="gray">
          {name}
        </StatusTag>
      ))}
    </Flex>
  );
}

function DataViewBlock({
  view,
  answer,
}: {
  view: DataView;
  answer: AIWorkResponse;
}) {
  switch (view.type) {
    case "case_table":
      return (
        <CaseRows
          rows={view.rows}
          fullList={
            answer.outcome === "list"
              ? poCasesHref({
                  state: answer.understood.state ?? undefined,
                  supplierName: answer.understood.supplier_name ?? undefined,
                  activeOnly: answer.understood.active_only,
                })
              : null
          }
          hasMore={view.has_more}
          filtered={isFiltered(answer)}
        />
      );
    case "case_link":
      return (
        <Flex gap="small" align="center">
          <Link href={`/supply-chain/po-cases/${view.case.id}`}>
            Mở case {poReferenceLabel(view.case.po_reference)}
          </Link>
          <CaseStateTag state={view.case.state} />
        </Flex>
      );
    case "product_case_table":
      return (
        <ProductRows
          rows={view.rows}
          fullList={
            answer.outcome === "product_list" ? productListHref(answer) : null
          }
          hasMore={view.has_more}
          filtered={isFiltered(answer)}
        />
      );
    case "product_case_link":
      return (
        <Flex gap="small" align="center" wrap>
          <Link href={`/supply-chain/product-cases/${view.case.id}`}>
            Mở hồ sơ {view.case.proposal_code}
          </Link>
          <Typography.Text>{view.case.product_name}</Typography.Text>
          <ProductDevStateTag state={view.case.state} />
        </Flex>
      );
    default: {
      const unreachable: never = view;
      return unreachable;
    }
  }
}

function CaseRows({
  rows,
  fullList,
  hasMore,
  filtered,
}: {
  rows: POCase[];
  fullList: string | null;
  hasMore: boolean;
  filtered: boolean;
}) {
  const columns: TableColumnsType<POCase> = [
    {
      title: "Hồ sơ PO",
      key: "case",
      render: (_: unknown, row) => (
        <Link href={`/supply-chain/po-cases/${row.id}`}>
          {poReferenceLabel(row.po_reference)}
        </Link>
      ),
    },
    { title: "NCC", dataIndex: "supplier_name" },
    {
      title: "Trạng thái",
      key: "state",
      render: (_: unknown, row) => <CaseStateTag state={row.state} />,
    },
  ];
  return (
    <Flex vertical gap="small" align="start">
      {rows.length > 0 && (
        <Table<POCase>
          rowKey="id"
          size="small"
          pagination={false}
          scroll={{ x: "max-content" }}
          columns={columns}
          dataSource={rows}
          className="w-full"
        />
      )}
      {fullList && (
        <Link href={fullList}>
          {hasMore && filtered
            ? "Xem tất cả case khớp"
            : hasMore
              ? "Xem tất cả PO cases"
              : "Mở trong danh sách PO cases"}
        </Link>
      )}
    </Flex>
  );
}

function ProductRows({
  rows,
  fullList,
  hasMore,
  filtered,
}: {
  rows: ProductCaseRef[];
  fullList: string | null;
  hasMore: boolean;
  filtered: boolean;
}) {
  const columns: TableColumnsType<ProductCaseRef> = [
    {
      title: "Hồ sơ",
      key: "case",
      render: (_: unknown, row) => (
        <Link href={`/supply-chain/product-cases/${row.id}`}>
          {row.proposal_code}
        </Link>
      ),
    },
    {
      title: "Sản phẩm",
      dataIndex: "product_name",
      ellipsis: { showTitle: true },
    },
    {
      title: "Trạng thái",
      key: "state",
      render: (_: unknown, row) => <ProductDevStateTag state={row.state} />,
    },
  ];
  return (
    <Flex vertical gap="small" align="start">
      {rows.length > 0 && (
        <Table<ProductCaseRef>
          rowKey="id"
          size="small"
          pagination={false}
          scroll={{ x: "max-content" }}
          columns={columns}
          dataSource={rows}
          className="w-full"
        />
      )}
      {fullList && (
        <Link href={fullList}>
          {hasMore && filtered
            ? "Xem tất cả hồ sơ khớp"
            : hasMore
              ? "Xem tất cả hồ sơ phát triển"
              : "Mở trong danh sách hồ sơ phát triển"}
        </Link>
      )}
    </Flex>
  );
}
