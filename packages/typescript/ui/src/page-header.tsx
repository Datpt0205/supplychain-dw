"use client";

import type { ReactNode } from "react";
import { Breadcrumb, theme, Typography, type BreadcrumbProps } from "antd";

export interface PageHeaderProps {
  title: ReactNode;
  /** One line: what the page holds now, or the record's identity. */
  description?: ReactNode;
  /** Where the page sits: the context, the list, the record. */
  breadcrumb?: BreadcrumbProps["items"];
  /** A record's kind, code and step, on the line above its title. */
  meta?: ReactNode;
  /** Status tags beside the title. */
  tags?: ReactNode;
  /** The page's actions, at the end of the row (wraps below on a phone). */
  extra?: ReactNode;
}

/**
 * The header every page in the shell starts with (the handoff's list and
 * record headers): the breadcrumb, the record's kind above the title, the
 * title (the page's one `h1`), its status, one line of summary, and the
 * actions at the end of the row. The same place for each on every screen
 * (ui-quality "put the screen beside its sibling"). Domain-neutral; a context
 * passes its own words and tags.
 */
export function PageHeader({
  title,
  description,
  breadcrumb,
  meta,
  tags,
  extra,
}: PageHeaderProps) {
  const { token } = theme.useToken();
  return (
    <header className="mb-4 flex flex-col gap-1">
      {breadcrumb?.length ? <Breadcrumb items={breadcrumb} /> : null}
      {meta ? (
        <div className="flex flex-wrap items-center gap-2">{meta}</div>
      ) : null}
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            {/* The page's h1, at the size of antd's third heading step. */}
            <Typography.Title
              level={1}
              style={{ margin: 0, fontSize: token.fontSizeHeading3 }}
            >
              {title}
            </Typography.Title>
            {tags}
          </div>
          {description ? (
            <Typography.Paragraph type="secondary" style={{ margin: 0 }}>
              {description}
            </Typography.Paragraph>
          ) : null}
        </div>
        {extra ? (
          <div className="flex flex-wrap items-center gap-2">{extra}</div>
        ) : null}
      </div>
    </header>
  );
}
