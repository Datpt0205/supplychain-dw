"use client";

import { Button, Flex, Typography } from "antd";

interface LoadMoreProps {
  hasMore: boolean;
  loading: boolean;
  onLoadMore: () => void;
  /** How many rows are on screen, so the footer can say where the list stands. */
  shown: number;
  /** The noun for those rows: "sự kiện", "tài liệu", "yêu cầu". */
  noun: string;
  /**
   * Set when the screen filters the loaded rows in the browser. The filter then
   * only sees what has been fetched, and a user who searches a list with more
   * behind it has to be told that — otherwise an empty result reads as "there is
   * none" when it means "there is none so far".
   */
  filtered?: boolean;
}

/**
 * The end of a cursor-paged list: what is shown, and whether there is more.
 *
 * Rendered even when the list is complete. A footer that appears only when
 * there is a next page leaves the user unable to tell a finished list from one
 * whose button they have not scrolled to yet.
 */
export function LoadMore({
  hasMore,
  loading,
  onLoadMore,
  shown,
  noun,
  filtered = false,
}: LoadMoreProps) {
  return (
    <Flex justify="space-between" align="center" gap="middle" wrap>
      <Typography.Text type="secondary">
        {hasMore ? `Đã tải ${shown} ${noun}` : `${shown} ${noun}`}
        {hasMore && filtered && " — ô tìm chỉ tìm trong số đã tải"}
      </Typography.Text>
      {hasMore && (
        <Button onClick={onLoadMore} loading={loading}>
          Tải thêm
        </Button>
      )}
    </Flex>
  );
}
