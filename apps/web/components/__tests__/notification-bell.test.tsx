import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Inbox } from "@dw/contracts";

const listNotifications = vi.fn();
const markNotificationRead = vi.fn();
const markAllNotificationsRead = vi.fn();
vi.mock("../../lib/session", () => ({
  apiClient: () => ({
    listNotifications,
    markNotificationRead,
    markAllNotificationsRead,
  }),
}));

const push = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));

import { NotificationBell } from "../notification-bell";

function inbox(link: string | null): Inbox {
  return {
    items: [
      {
        id: "n-1",
        title: "Yêu cầu chờ bạn duyệt: hợp đồng HĐ-2026-007",
        body: "Đã chờ 2 ngày.",
        link,
        created_at: "2026-09-28T08:00:00Z",
        read_at: null,
      },
    ],
    unread: 1,
  };
}

afterEach(() => {
  cleanup();
  listNotifications.mockReset();
  markNotificationRead.mockReset();
  markAllNotificationsRead.mockReset();
  push.mockReset();
});

describe("NotificationBell", () => {
  it("shows the unread count, and opening one marks it read and follows its link", async () => {
    listNotifications.mockResolvedValue(inbox("/approvals"));
    markNotificationRead.mockResolvedValue(undefined);
    render(<NotificationBell />);

    const bell = await screen.findByRole("button", {
      name: "Thông báo, 1 chưa đọc",
    });
    fireEvent.click(bell);
    fireEvent.click(
      await screen.findByText("Yêu cầu chờ bạn duyệt: hợp đồng HĐ-2026-007"),
    );

    await waitFor(() => expect(push).toHaveBeenCalledWith("/approvals"));
    expect(markNotificationRead).toHaveBeenCalledWith("n-1");
  });

  it.each(["//evil.example/x", "https://evil.example", "javascript:alert(1)"])(
    "never navigates to %s",
    async (link) => {
      listNotifications.mockResolvedValue(inbox(link));
      markNotificationRead.mockResolvedValue(undefined);
      render(<NotificationBell />);

      fireEvent.click(
        await screen.findByRole("button", { name: "Thông báo, 1 chưa đọc" }),
      );
      fireEvent.click(
        await screen.findByText("Yêu cầu chờ bạn duyệt: hợp đồng HĐ-2026-007"),
      );

      await waitFor(() => expect(markNotificationRead).toHaveBeenCalled());
      expect(push).not.toHaveBeenCalled();
    },
  );

  it("marks all read", async () => {
    listNotifications.mockResolvedValue(inbox(null));
    markAllNotificationsRead.mockResolvedValue(undefined);
    render(<NotificationBell />);

    fireEvent.click(
      await screen.findByRole("button", { name: "Thông báo, 1 chưa đọc" }),
    );
    fireEvent.click(await screen.findByText("Đánh dấu đã đọc hết"));

    await waitFor(() => expect(markAllNotificationsRead).toHaveBeenCalled());
  });
});
