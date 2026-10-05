import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { App } from "antd";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { ApiError } from "@dw/api-client";

const getZaloStatus = vi.fn();
const connectZalo = vi.fn();
const disconnectZalo = vi.fn();
vi.mock("../../../lib/session", () => ({
  apiClient: () => ({ getZaloStatus, connectZalo, disconnectZalo }),
}));

import { ZaloConnectCard } from "../zalo-connect-card";

const NOW = new Date("2026-10-05T08:00:00Z");
const OFFER = {
  code: "AbC123xyz",
  deep_link: "https://zalo.me/s/bot-test?start=AbC123xyz",
  expires_at: "2026-10-05T08:15:00Z",
};

beforeAll(() => {
  // antd's responsive helpers ask for media queries jsdom does not implement.
  Object.defineProperty(window, "matchMedia", {
    writable: true,
    value: (query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    }),
  });
});

afterEach(() => {
  cleanup();
  getZaloStatus.mockReset();
  connectZalo.mockReset();
  disconnectZalo.mockReset();
});

function renderCard(now: () => Date = () => NOW) {
  return render(
    <App>
      <ZaloConnectCard now={now} />
    </App>,
  );
}

function apiError(status: number, code: string, message: string) {
  return new ApiError(status, { code, message, details: {} });
}

describe("ZaloConnectCard", () => {
  it("shows a skeleton until the status arrives", async () => {
    let resolve: (v: { linked: boolean }) => void = () => {};
    getZaloStatus.mockReturnValue(
      new Promise((r) => {
        resolve = r;
      }),
    );
    const { container } = renderCard();

    expect(container.querySelector(".ant-skeleton")).not.toBeNull();
    expect(screen.queryByRole("button", { name: "Kết nối Zalo" })).toBeNull();

    await act(async () => resolve({ linked: false }));
    expect(
      await screen.findByRole("button", { name: "Kết nối Zalo" }),
    ).toBeTruthy();
  });

  it("not linked: offers to connect", async () => {
    getZaloStatus.mockResolvedValue({ linked: false });
    renderCard();

    expect(await screen.findByText("Chưa kết nối")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Kết nối Zalo" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Ngắt kết nối" })).toBeNull();
  });

  it("pending: shows the /start line, the expiry, the copy button and the deep link", async () => {
    getZaloStatus.mockResolvedValue({ linked: false });
    connectZalo.mockResolvedValue(OFFER);
    renderCard();

    fireEvent.click(
      await screen.findByRole("button", { name: "Kết nối Zalo" }),
    );

    expect(await screen.findByText("/start AbC123xyz")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Chép lệnh" })).toBeTruthy();
    expect(
      screen.getByRole("link", { name: "Mở Zalo" }).getAttribute("href"),
    ).toBe(OFFER.deep_link);
    expect(
      screen.getByText(/một lần, hết hạn lúc .* \(15 phút\)/),
    ).toBeTruthy();
    expect(
      // jsdom never finishes antd's exit motion, so the spent loading icon
      // stays in the name ("loading Lấy mã mới"); a browser drops it.
      await screen.findByRole("button", { name: /Lấy mã mới$/ }),
    ).toBeTruthy();
  });

  it("pending without a configured deep link shows no Zalo link", async () => {
    getZaloStatus.mockResolvedValue({ linked: false });
    connectZalo.mockResolvedValue({ ...OFFER, deep_link: null });
    renderCard();

    fireEvent.click(
      await screen.findByRole("button", { name: "Kết nối Zalo" }),
    );

    expect(await screen.findByText("/start AbC123xyz")).toBeTruthy();
    expect(screen.queryByRole("link", { name: "Mở Zalo" })).toBeNull();
  });

  it("an expired code says so and offers a new one", async () => {
    getZaloStatus.mockResolvedValue({ linked: false });
    connectZalo.mockResolvedValue(OFFER);
    let now = NOW;
    renderCard(() => now);

    fireEvent.click(
      await screen.findByRole("button", { name: "Kết nối Zalo" }),
    );
    await screen.findByText("/start AbC123xyz");
    now = new Date("2026-10-05T08:15:00Z");
    // Any re-render re-reads the clock; "Kiểm tra lại" reloads the status.
    fireEvent.click(screen.getByRole("button", { name: /Kiểm tra lại/ }));

    expect(await screen.findByText(/Mã đã hết hạn/)).toBeTruthy();
    expect(screen.queryByText("/start AbC123xyz")).toBeNull();
  });

  it("two quick presses on connect send one request", async () => {
    getZaloStatus.mockResolvedValue({ linked: false });
    let resolve: (v: typeof OFFER) => void = () => {};
    connectZalo.mockReturnValue(
      new Promise((r) => {
        resolve = r;
      }),
    );
    renderCard();

    const button = await screen.findByRole("button", { name: "Kết nối Zalo" });
    fireEvent.click(button);
    fireEvent.click(button);
    await act(async () => resolve(OFFER));

    expect(connectZalo).toHaveBeenCalledTimes(1);
  });

  it("linked: offers to switch Zalo and to disconnect", async () => {
    getZaloStatus.mockResolvedValue({ linked: true });
    renderCard();

    expect(await screen.findByText("Đã kết nối")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Đổi Zalo khác" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Ngắt kết nối" })).toBeTruthy();
  });

  it("disconnect asks first, and Cancel keeps the link", async () => {
    getZaloStatus.mockResolvedValue({ linked: true });
    renderCard();

    fireEvent.click(
      await screen.findByRole("button", { name: "Ngắt kết nối" }),
    );
    expect(
      (await screen.findAllByText("Ngắt kết nối Zalo?")).length,
    ).toBeGreaterThan(0);
    fireEvent.click(screen.getByRole("button", { name: "Giữ kết nối" }));

    // antd keeps the closing dialog mounted while its motion runs; what
    // matters is that nothing was sent and the link still reads as linked.
    await act(async () => {
      await new Promise((r) => setTimeout(r, 50));
    });
    expect(disconnectZalo).not.toHaveBeenCalled();
    expect(screen.getByText("Đã kết nối")).toBeTruthy();
  });

  it("confirming disconnect unlinks and shows not linked", async () => {
    getZaloStatus.mockResolvedValue({ linked: true });
    disconnectZalo.mockResolvedValue(undefined);
    renderCard();

    fireEvent.click(
      await screen.findByRole("button", { name: "Ngắt kết nối" }),
    );
    const dialog = await screen.findByRole("dialog");
    const confirm = Array.from(dialog.querySelectorAll("button")).find(
      (b) => b.textContent === "Ngắt kết nối",
    );
    expect(confirm).toBeTruthy();
    fireEvent.click(confirm as HTMLButtonElement);

    await waitFor(() => expect(disconnectZalo).toHaveBeenCalledTimes(1));
    expect(await screen.findByText("Chưa kết nối")).toBeTruthy();
  });

  it("a deployment without a bot shows 'not configured', not an error", async () => {
    getZaloStatus.mockRejectedValue(apiError(404, "not_found", "Not Found"));
    renderCard();

    expect(await screen.findByText("Hệ thống chưa bật Zalo")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Kết nối Zalo" })).toBeNull();
  });

  it("a server error shows the server's sentence and a retry", async () => {
    getZaloStatus
      .mockRejectedValueOnce(
        apiError(
          503,
          "upstream_unavailable",
          "Cơ sở dữ liệu tạm thời không phản hồi.",
        ),
      )
      .mockResolvedValueOnce({ linked: false });
    renderCard();

    expect(
      await screen.findByText("Cơ sở dữ liệu tạm thời không phản hồi."),
    ).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /Thử lại/ }));
    expect(
      await screen.findByRole("button", { name: "Kết nối Zalo" }),
    ).toBeTruthy();
  });

  it("a network failure is the offline state, not an error message", async () => {
    getZaloStatus.mockRejectedValue(new TypeError("Failed to fetch"));
    renderCard();

    expect(await screen.findByText(/mất kết nối mạng/)).toBeTruthy();
    expect(screen.queryByText("Failed to fetch")).toBeNull();
  });

  it("while the browser is offline the mutations are disabled with the reason shown", async () => {
    getZaloStatus.mockResolvedValue({ linked: true });
    const onLine = vi
      .spyOn(window.navigator, "onLine", "get")
      .mockReturnValue(false);
    try {
      renderCard();
      const switchButton = await screen.findByRole("button", {
        name: "Đổi Zalo khác",
      });
      expect((switchButton as HTMLButtonElement).disabled).toBe(true);
      expect(
        (
          screen.getByRole("button", {
            name: "Ngắt kết nối",
          }) as HTMLButtonElement
        ).disabled,
      ).toBe(true);
      expect(screen.getByText(/Không có kết nối mạng/)).toBeTruthy();
    } finally {
      onLine.mockRestore();
    }
  });
});
