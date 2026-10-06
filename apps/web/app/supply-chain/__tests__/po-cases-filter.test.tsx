import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { POCase, Page } from "@dw/contracts";

// antd's Table and Select render slowly under jsdom on Windows; the default
// 5 s budget measured too tight for a whole page.
vi.setConfig({ testTimeout: 30_000 });

vi.mock("../../../lib/auth/auth-context", () => ({
  useAuth: () => ({ active: { workspaceId: "ws-A" } }),
}));

let search = "";
vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams(search),
}));

const listPOCases = vi.fn(async (): Promise<Page<POCase>> => ({
  items: [],
  next_cursor: null,
}));
vi.mock("../../../lib/session", () => ({
  apiClient: () => ({ listPOCases }),
}));

import POCasesPage from "../po-cases/page";

afterEach(() => {
  // No vitest globals here, so testing-library cannot clean up on its own.
  cleanup();
  listPOCases.mockClear();
  vi.restoreAllMocks();
});

describe("PO cases list, filtered from the URL", () => {
  it("asks for what the URL says, dropping a state the server would refuse", async () => {
    search = "state=not_a_state&supplier_name=Quiet+%26+Sons&active_only=true";
    render(<POCasesPage />);

    await waitFor(() => expect(listPOCases).toHaveBeenCalled());
    expect(listPOCases).toHaveBeenLastCalledWith({
      cursor: null,
      state: undefined,
      supplierName: "Quiet & Sons",
      activeOnly: true,
    });
  });

  it("keeps keyboard focus on the page when the supplier chip is removed", async () => {
    search = "supplier_name=Quiet+%26+Sons";
    const replace = vi.spyOn(window.history, "replaceState");
    render(<POCasesPage />);

    const remove = await screen.findByRole("button", {
      name: "Bỏ lọc nhà cung cấp Quiet & Sons",
    });
    remove.focus();
    fireEvent.click(remove);

    expect(replace).toHaveBeenLastCalledWith(
      null,
      "",
      "/supply-chain/po-cases",
    );
    expect(document.activeElement).toBe(
      screen.getByRole("combobox", { name: "Lọc theo trạng thái" }),
    );
  });

  it("clears every filter from the empty state and keeps focus", async () => {
    search = "state=blocked&active_only=true";
    const replace = vi.spyOn(window.history, "replaceState");
    render(<POCasesPage />);

    const clear = await screen.findByRole("button", { name: "Xóa bộ lọc" });
    clear.focus();
    fireEvent.click(clear);

    expect(replace).toHaveBeenLastCalledWith(
      null,
      "",
      "/supply-chain/po-cases",
    );
    expect(document.activeElement).toBe(
      screen.getByRole("combobox", { name: "Lọc theo trạng thái" }),
    );
  });
});
