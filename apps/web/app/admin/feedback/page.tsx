"use client";

import { useCallback, useEffect, useState } from "react";
import { Card, Flex, Skeleton, Tag, Typography } from "antd";
import { MessageOutlined } from "@ant-design/icons";
import { PageHeader, RegionState } from "@dw/ui";
import { apiClient } from "../../../lib/session";
import { LoadError } from "../../../components/load-error";
import { LoadMore } from "../../../components/load-more";
import { formatDateTime } from "../../../lib/dates";
import { useCachedPages } from "../../../lib/use-cached-pages";

/**
 * The feedback inbox (spec 003 US5), under Admin: what members sent, newest
 * first, each with its module, page, suggestion and screenshots. The API gates
 * it on the members-read scope; the nav item carries the same scope.
 */
export default function FeedbackInboxPage() {
  const { items, loading, loadingMore, error, hasMore, loadMore, reload } =
    useCachedPages(
      "admin:feedback",
      useCallback(
        (cursor: string | null) => apiClient().listFeedback({ cursor }),
        [],
      ),
    );

  return (
    <div className="mx-auto max-w-3xl">
      <PageHeader
        icon={<MessageOutlined />}
        title="Hộp phản hồi"
        subtitle="Mới nhất trước. Mỗi phản hồi ghi module, trang, mô tả, đề xuất và ảnh màn hình người gửi đính kèm."
      />
      <Flex vertical gap="middle">
        {error != null && <LoadError error={error} onRetry={reload} />}
        {loading && error == null && <RegionState kind="loading" />}
        {!loading && error == null && items.length === 0 && (
          <RegionState
            kind="empty"
            title="Chưa có phản hồi"
            description="Phản hồi thành viên gửi qua nút góc dưới trái sẽ hiện ở đây."
          />
        )}
        {items.length > 0 && (
          <ul className="m-0 flex list-none flex-col gap-3 p-0">
            {items.map((item) => (
              <li key={item.id}>
                <Card size="small">
                  <Flex vertical gap={6}>
                    <Flex justify="space-between" gap="small" wrap>
                      <Typography.Text strong>
                        {item.author_name}
                      </Typography.Text>
                      <Typography.Text type="secondary" className="text-xs">
                        {formatDateTime(item.created_at)}
                      </Typography.Text>
                    </Flex>
                    <Flex wrap gap="small" align="center">
                      <Tag>{item.module ?? item.category}</Tag>
                      {item.page_path && (
                        <Typography.Text
                          type="secondary"
                          code
                          className="text-xs"
                        >
                          {item.page_path}
                        </Typography.Text>
                      )}
                    </Flex>
                    <Typography.Paragraph className="!mb-0 whitespace-pre-wrap">
                      {item.message}
                    </Typography.Paragraph>
                    {item.suggestion && (
                      <Typography.Paragraph
                        type="secondary"
                        className="!mb-0 whitespace-pre-wrap"
                      >
                        <Typography.Text strong>Đề xuất:</Typography.Text>{" "}
                        {item.suggestion}
                      </Typography.Paragraph>
                    )}
                    {item.attachments.length > 0 && (
                      <ul className="m-0 mt-1 flex list-none flex-wrap gap-2 p-0">
                        {item.attachments.map((attachment) => (
                          <li key={attachment.id}>
                            <AttachmentImage
                              feedbackId={item.id}
                              attachmentId={attachment.id}
                            />
                          </li>
                        ))}
                      </ul>
                    )}
                  </Flex>
                </Card>
              </li>
            ))}
          </ul>
        )}
        {!loading && items.length > 0 && (
          <LoadMore
            hasMore={hasMore}
            loading={loadingMore}
            onLoadMore={loadMore}
            shown={items.length}
            noun="phản hồi"
          />
        )}
      </Flex>
    </div>
  );
}

/**
 * A screenshot, fetched with the session's token (the bytes sit behind the
 * inbox scope, so a plain <img src> could not carry the authorization).
 */
function AttachmentImage({
  feedbackId,
  attachmentId,
}: {
  feedbackId: string;
  attachmentId: string;
}) {
  const [url, setUrl] = useState<string | null>(null);
  useEffect(() => {
    let objectUrl: string | null = null;
    apiClient()
      .feedbackAttachment(feedbackId, attachmentId)
      .then((blob) => {
        objectUrl = URL.createObjectURL(blob);
        setUrl(objectUrl);
      })
      .catch(() => setUrl(null));
    return () => {
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [feedbackId, attachmentId]);
  if (!url) {
    return <Skeleton.Image active className="!size-24" />;
  }
  return (
    <a href={url} target="_blank" rel="noreferrer" title="Mở ảnh">
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img
        src={url}
        alt="Ảnh đính kèm phản hồi"
        className="size-24 object-cover"
      />
    </a>
  );
}
