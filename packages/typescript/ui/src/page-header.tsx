"use client";

import type { ReactNode } from "react";
import { Breadcrumb, Flex, Typography, theme } from "antd";

export interface PageHeaderCrumb {
  /** The app passes its router's link here; the shell imports no framework. */
  title: ReactNode;
}

export interface PageHeaderProps {
  /** The page's one `<h1>`. */
  title: ReactNode;
  subtitle?: ReactNode;
  /** Shown before the title, e.g. the page's icon. */
  icon?: ReactNode;
  /** Right of the title from `md`, below it when narrow. */
  actions?: ReactNode;
  /** Where the page sits, in a `nav` named "Vị trí". */
  breadcrumb?: PageHeaderCrumb[];
  /** Status tags above the title. */
  tags?: ReactNode;
}

/**
 * The head of every page: breadcrumb, tags, one `<h1>`, a subtitle and the
 * page's actions. Sizes and colours come from the theme's tokens, so a
 * product's theme restyles every page at once.
 */
export function PageHeader({
  title,
  subtitle,
  icon,
  actions,
  breadcrumb,
  tags,
}: PageHeaderProps) {
  const { token } = theme.useToken();
  return (
    <div className="mb-5 flex flex-col gap-3 md:flex-row md:items-end md:justify-between">
      <Flex vertical gap={6} className="min-w-0">
        {breadcrumb && breadcrumb.length > 0 && (
          // antd's Breadcrumb is itself the <nav>; it gets the name.
          <Breadcrumb
            aria-label="Vị trí"
            items={breadcrumb.map((crumb) => ({ title: crumb.title }))}
          />
        )}
        {tags && (
          <Flex wrap gap={6}>
            {tags}
          </Flex>
        )}
        <h1
          className="m-0 flex min-w-0 items-center gap-2 break-words"
          style={{
            fontSize: token.fontSizeHeading3,
            lineHeight: token.lineHeightHeading3,
            fontWeight: token.fontWeightStrong,
            color: token.colorText,
          }}
        >
          {icon && (
            <span aria-hidden className="inline-flex shrink-0">
              {icon}
            </span>
          )}
          <span className="min-w-0">{title}</span>
        </h1>
        {subtitle && (
          <Typography.Paragraph type="secondary" className="!mb-0 max-w-3xl">
            {subtitle}
          </Typography.Paragraph>
        )}
      </Flex>
      {actions && (
        <Flex wrap gap="small" align="center" className="shrink-0">
          {actions}
        </Flex>
      )}
    </div>
  );
}
