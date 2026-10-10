"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Badge, Button, Flex, Popover, Typography, theme } from "antd";
import { BellOutlined, CheckOutlined } from "@ant-design/icons";
import type { AppNotification, Inbox } from "@dw/contracts";
import { formatDateTime } from "../lib/dates";
import { apiClient } from "../lib/session";

// How often the badge checks for something new while the app is open.
const POLL_MS = 60_000;

/** An app-relative path only; the database already refuses anything else,
 * and this refuses it again before navigating. */
function internalPath(link: string | null): string | null {
  return link && link.startsWith("/") && !link.startsWith("//") ? link : null;
}

export function NotificationBell() {
  const router = useRouter();
  const { token } = theme.useToken();
  const [inbox, setInbox] = useState<Inbox | null>(null);
  const [open, setOpen] = useState(false);

  const load = useCallback((signal?: AbortSignal) => {
    apiClient()
      .listNotifications(signal)
      .then(setInbox)
      .catch(() => {
        // A failed poll leaves the last inbox shown; the next one retries.
      });
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    load(controller.signal);
    const timer = window.setInterval(() => load(), POLL_MS);
    return () => {
      controller.abort();
      window.clearInterval(timer);
    };
  }, [load]);

  const follow = async (item: AppNotification) => {
    setOpen(false);
    if (item.read_at === null) {
      await apiClient()
        .markNotificationRead(item.id)
        .catch(() => undefined);
      load();
    }
    const path = internalPath(item.link);
    if (path) router.push(path);
  };

  const readAll = async () => {
    await apiClient()
      .markAllNotificationsRead()
      .catch(() => undefined);
    load();
  };

  const unread = inbox?.unread ?? 0;
  const items = inbox?.items ?? [];

  const panel = (
    <div className="w-80 max-w-[85vw]">
      <Flex justify="space-between" align="center" className="mb-2">
        <Typography.Text strong>Thông báo</Typography.Text>
        {unread > 0 && (
          <Button
            type="link"
            size="small"
            icon={<CheckOutlined aria-hidden />}
            onClick={() => void readAll()}
          >
            Đánh dấu đã đọc hết
          </Button>
        )}
      </Flex>
      {items.length === 0 ? (
        <Typography.Paragraph type="secondary" className="!my-6 text-center">
          Chưa có thông báo nào.
        </Typography.Paragraph>
      ) : (
        <ul className="m-0 max-h-96 list-none overflow-y-auto p-0">
          {items.map((item) => (
            <li key={item.id}>
              <button
                type="button"
                onClick={() => void follow(item)}
                className="block w-full border-0 px-2 py-2 text-left"
                style={{
                  borderRadius: token.borderRadius,
                  background:
                    item.read_at === null
                      ? token.colorPrimaryBg
                      : "transparent",
                  color: token.colorText,
                }}
              >
                <Typography.Text strong className="block">
                  {item.title}
                </Typography.Text>
                {item.body && (
                  <Typography.Text type="secondary" className="block text-xs">
                    {item.body}
                  </Typography.Text>
                )}
                <Typography.Text type="secondary" className="block text-xs">
                  {formatDateTime(item.created_at)}
                </Typography.Text>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );

  return (
    <Popover
      content={panel}
      trigger="click"
      placement="bottomRight"
      open={open}
      onOpenChange={setOpen}
    >
      <Badge count={unread} overflowCount={99} size="small">
        <Button
          shape="circle"
          icon={<BellOutlined aria-hidden />}
          aria-label={unread ? `Thông báo, ${unread} chưa đọc` : "Thông báo"}
        />
      </Badge>
    </Popover>
  );
}
