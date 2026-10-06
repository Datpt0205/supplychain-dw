import Link from "next/link";
import type { BreadcrumbProps } from "antd";

/**
 * A Supply Chain page's breadcrumb: the context (its daily brief is where its
 * day starts), then each level down to the page; every level but the page
 * itself is a link.
 */
export function supplyChainCrumbs(
  ...levels: (string | { title: string; href: string })[]
): NonNullable<BreadcrumbProps["items"]> {
  return [
    { title: <Link href="/supply-chain/daily-brief">Supply Chain</Link> },
    ...levels.map((level) =>
      typeof level === "string"
        ? { title: level }
        : { title: <Link href={level.href}>{level.title}</Link> },
    ),
  ];
}
