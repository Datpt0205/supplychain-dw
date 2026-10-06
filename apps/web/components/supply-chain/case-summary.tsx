"use client";

import type { ReactNode } from "react";
import { Card, theme, Typography } from "antd";

export interface SummaryCell {
  key: string;
  label: string;
  value: ReactNode;
  sub?: ReactNode;
  /** Whether the value blocks the next step (`err`), needs a look, or is clear. */
  tone?: "err" | "warn" | "ok";
}

/**
 * A case's first screenful (the handoff's overview strip): the few facts a
 * person needs before anything else, the one that can block the next step in
 * words and in colour (ui-quality §2, §7).
 */
export function CaseSummary({
  cells,
  label,
}: {
  cells: SummaryCell[];
  /** The strip's accessible name. */
  label: string;
}) {
  const { token } = theme.useToken();
  const colour = {
    err: token.colorErrorText,
    warn: token.colorWarningText,
    ok: token.colorSuccessText,
  };
  return (
    <Card size="small" styles={{ body: { padding: 0 } }}>
      <dl
        aria-label={label}
        className="m-0 grid grid-cols-1 gap-px overflow-hidden rounded-lg bg-border sm:grid-cols-2 xl:grid-cols-4"
      >
        {cells.map((cell) => (
          <div
            key={cell.key}
            className="flex flex-col gap-0.5 bg-card px-4 py-3"
          >
            <dt>
              <Typography.Text type="secondary">{cell.label}</Typography.Text>
            </dt>
            <dd className="m-0 flex flex-col">
              <Typography.Text
                strong
                style={{
                  fontSize: token.fontSizeLG,
                  color: cell.tone ? colour[cell.tone] : undefined,
                }}
              >
                {cell.value}
              </Typography.Text>
              {cell.sub ? (
                <Typography.Text type="secondary">{cell.sub}</Typography.Text>
              ) : null}
            </dd>
          </div>
        ))}
      </dl>
    </Card>
  );
}
