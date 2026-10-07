import { Typography } from "antd";

/** What a PO case without its PO number yet reads as: ĐẶT HÀNG opened it
 * awaiting step 10 (ADR 0017), so `po_reference` is null. */
export const NO_PO_REFERENCE = "Chưa có số PO";

/** A PO case's number in words, for a title, a link or a label. */
export function poReferenceLabel(reference: string | null): string {
  return reference ?? NO_PO_REFERENCE;
}

/** A PO case's number as the lists draw it: mono when it exists, the
 * fallback in plain words when it does not. */
export function PoReferenceText({ reference }: { reference: string | null }) {
  return reference === null ? (
    <Typography.Text italic>{NO_PO_REFERENCE}</Typography.Text>
  ) : (
    <Typography.Text code>{reference}</Typography.Text>
  );
}
