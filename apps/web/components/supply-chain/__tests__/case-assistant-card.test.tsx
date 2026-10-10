import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { App } from "antd";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { ApiError } from "@dw/api-client";

vi.setConfig({ testTimeout: 30_000 });

const askAboutCase = vi.fn();
vi.mock("../../../lib/session", () => ({
  apiClient: () => ({ askAboutCase }),
}));

import { AI_WRITTEN, CaseAssistantCard } from "../case-assistant-card";

const CASE_ID = "11111111-1111-4111-8111-111111111111";

beforeAll(() => {
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
  globalThis.ResizeObserver ??= class {
    observe() {}
    unobserve() {}
    disconnect() {}
  };
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

async function ask(question: string) {
  render(
    <App>
      <CaseAssistantCard caseKind="product" caseId={CASE_ID} />
    </App>,
  );
  fireEvent.change(screen.getByLabelText("Câu hỏi"), {
    target: { value: question },
  });
  fireEvent.click(screen.getByRole("button", { name: "Hỏi" }));
}

describe("CaseAssistantCard", () => {
  it("shows each kept sentence as AI-written with what it cites", async () => {
    askAboutCase.mockResolvedValue({
      answered: true,
      text: "BM04 ghi MOQ là 500 cái.",
      sentences: [
        {
          text: "BM04 ghi MOQ là 500 cái.",
          cites: [{ key: "bm04", label: "BM04 phiên bản 1" }],
        },
      ],
      dropped: 0,
    });
    await ask("BM04 ghi MOQ bao nhiêu?");
    expect(await screen.findByText(AI_WRITTEN)).toBeTruthy();
    expect(screen.getByText("Nguồn: BM04 phiên bản 1")).toBeTruthy();
    expect(askAboutCase).toHaveBeenCalledWith(
      "product",
      CASE_ID,
      "BM04 ghi MOQ bao nhiêu?",
    );
  });

  it("says there is not enough evidence, never an AI tag", async () => {
    askAboutCase.mockResolvedValue({
      answered: false,
      text: "Không đủ bằng chứng trong hồ sơ để trả lời câu này.",
      sentences: [],
      dropped: 1,
    });
    await ask("NCC có FDA không?");
    expect(
      await screen.findByText(
        "Không đủ bằng chứng trong hồ sơ để trả lời câu này.",
      ),
    ).toBeTruthy();
    expect(screen.queryByText(AI_WRITTEN)).toBeNull();
  });

  it("shows a refusal in the server's words", async () => {
    askAboutCase.mockRejectedValue(
      new ApiError(403, {
        code: "permission_denied",
        message: "Bạn không có quyền xem hồ sơ này",
        details: {},
      }),
    );
    await ask("ETD khi nào?");
    await waitFor(() =>
      expect(screen.getByText("Bạn không có quyền xem hồ sơ này")).toBeTruthy(),
    );
  });
});
