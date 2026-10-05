import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { App } from "antd";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { ApiError, type CaseDocument } from "@dw/api-client";

const listCaseDocuments = vi.fn();
const uploadCaseDocument = vi.fn();
const downloadCaseDocument = vi.fn();
vi.mock("../../../lib/session", () => ({
  apiClient: () => ({
    listCaseDocuments,
    uploadCaseDocument,
    downloadCaseDocument,
  }),
}));

import { CaseDocumentsCard, DOC_TYPE_LABEL } from "../case-documents-card";

const CASE_ID = "11111111-1111-4111-8111-111111111111";

function doc(overrides: Partial<CaseDocument> = {}): CaseDocument {
  return {
    id: "22222222-2222-4222-8222-222222222222",
    po_case_id: CASE_ID,
    doc_type: "purchase_order",
    filename: "PO-0042.pdf",
    content_type: "application/pdf",
    size_bytes: 2048,
    sha256: "a".repeat(64),
    version: 2,
    uploaded_by: "33333333-3333-4333-8333-333333333333",
    uploaded_at: "2026-10-05T08:00:00Z",
    ...overrides,
  };
}

/** The idempotency key the nth upload call carried. */
function keyOfCall(n: number): string {
  const call = uploadCaseDocument.mock.calls[n];
  if (!call) throw new Error(`no upload call ${n}`);
  return (call[1] as { idempotencyKey: string }).idempotencyKey;
}

function apiError(status: number, code: string, message: string) {
  return new ApiError(status, { code, message, details: {} });
}

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
  // antd's Table and Tooltip measure themselves; jsdom has no ResizeObserver.
  globalThis.ResizeObserver ??= class {
    observe() {}
    unobserve() {}
    disconnect() {}
  };
  // jsdom has neither; the download path asks for both.
  URL.createObjectURL = vi.fn(() => "blob:test");
  URL.revokeObjectURL = vi.fn();
});

afterEach(() => {
  cleanup();
  listCaseDocuments.mockReset();
  uploadCaseDocument.mockReset();
  downloadCaseDocument.mockReset();
});

function renderCard(canUpload = true) {
  return render(
    <App>
      <CaseDocumentsCard caseKind="po" caseId={CASE_ID} canUpload={canUpload} />
    </App>,
  );
}

async function chooseType(label: string) {
  fireEvent.mouseDown(screen.getByRole("combobox", { name: "Loại chứng từ" }));
  fireEvent.click(await screen.findByTitle(label));
}

async function chooseFile(name = "PO-0043.pdf"): Promise<File> {
  const file = new File(["%PDF-1.7"], name, { type: "application/pdf" });
  const input = document.querySelector<HTMLInputElement>('input[type="file"]');
  if (!input) throw new Error("no file input");
  fireEvent.change(input, { target: { files: [file] } });
  // antd hands the file over asynchronously; wait until it is shown.
  await screen.findByText(name);
  return file;
}

describe("CaseDocumentsCard", () => {
  it("lists documents with their Vietnamese type label and version", async () => {
    listCaseDocuments.mockResolvedValue([doc()]);
    renderCard();

    expect(await screen.findByText("Đơn đặt hàng (PO)")).toBeTruthy();
    expect(screen.getByText("v2")).toBeTruthy();
    expect(screen.getByText("PO-0042.pdf")).toBeTruthy();
    expect(listCaseDocuments).toHaveBeenCalledWith(CASE_ID);
  });

  it("labels every document type the API can return, from one table", () => {
    expect(Object.keys(DOC_TYPE_LABEL)).toHaveLength(14);
    expect(
      Object.values(DOC_TYPE_LABEL).every((label) => label.length > 0),
    ).toBe(true);
  });

  it("says when the case has no documents yet", async () => {
    listCaseDocuments.mockResolvedValue([]);
    renderCard();

    expect(await screen.findByText("Hồ sơ chưa có chứng từ nào.")).toBeTruthy();
  });

  it("shows the server's sentence and retries when the list fails", async () => {
    listCaseDocuments.mockRejectedValueOnce(
      apiError(
        503,
        "upstream_unavailable",
        "Kho tài liệu tạm thời không phản hồi",
      ),
    );
    listCaseDocuments.mockResolvedValueOnce([doc()]);
    renderCard();

    expect(
      await screen.findByText("Kho tài liệu tạm thời không phản hồi"),
    ).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /Thử lại/ }));
    expect(await screen.findByText("PO-0042.pdf")).toBeTruthy();
  });

  it("without the write scope, upload is disabled and says why in words", async () => {
    listCaseDocuments.mockResolvedValue([]);
    renderCard(false);

    await screen.findByText("Hồ sơ chưa có chứng từ nào.");
    const button = screen.getByRole("button", { name: /Tải lên/ });
    expect(button.hasAttribute("disabled")).toBe(true);
    // Nothing to fill in either: the picker and the file button are locked too.
    expect(
      screen
        .getByRole("combobox", { name: "Loại chứng từ" })
        .hasAttribute("disabled"),
    ).toBe(true);
    expect(
      screen
        .getByRole("button", { name: /Chọn file/ })
        .hasAttribute("disabled"),
    ).toBe(true);
    expect(
      screen.getByText(/Chỉ người có quyền tải chứng từ lên/),
    ).toBeTruthy();
    expect(uploadCaseDocument).not.toHaveBeenCalled();
  });

  it("uploads the chosen file as the chosen type, then shows the new list", async () => {
    listCaseDocuments.mockResolvedValueOnce([]);
    uploadCaseDocument.mockResolvedValue(doc({ version: 1 }));
    listCaseDocuments.mockResolvedValueOnce([doc({ version: 1 })]);
    renderCard();
    await screen.findByText("Hồ sơ chưa có chứng từ nào.");

    await chooseType("Đơn đặt hàng (PO)");
    const file = await chooseFile();
    fireEvent.click(screen.getByRole("button", { name: /Tải lên/ }));

    await waitFor(() => expect(uploadCaseDocument).toHaveBeenCalledTimes(1));
    const [caseId, input] = uploadCaseDocument.mock.calls[0] ?? [];
    expect(caseId).toBe(CASE_ID);
    expect(input.docType).toBe("purchase_order");
    expect(input.file).toBe(file);
    expect(typeof input.idempotencyKey).toBe("string");
    expect(await screen.findByText("v1")).toBeTruthy();
  });

  it("a retry of the same file reuses its key; another file gets a new one", async () => {
    listCaseDocuments.mockResolvedValue([]);
    uploadCaseDocument
      .mockRejectedValueOnce(new TypeError("Failed to fetch"))
      .mockResolvedValue(doc({ version: 1 }));
    renderCard();
    await screen.findByText("Hồ sơ chưa có chứng từ nào.");

    await chooseType("Hồ sơ đặt cọc");
    await chooseFile("coc.pdf");
    fireEvent.click(screen.getByRole("button", { name: /Tải lên/ }));
    await waitFor(() => expect(uploadCaseDocument).toHaveBeenCalledTimes(1));
    await screen.findByText(/Failed to fetch/);
    fireEvent.click(screen.getByRole("button", { name: /Tải lên/ }));
    await waitFor(() => expect(uploadCaseDocument).toHaveBeenCalledTimes(2));

    expect(keyOfCall(1)).toBe(keyOfCall(0));

    await chooseFile("coc-2.pdf");
    fireEvent.click(screen.getByRole("button", { name: /Tải lên/ }));
    await waitFor(() => expect(uploadCaseDocument).toHaveBeenCalledTimes(3));
    expect(keyOfCall(2)).not.toBe(keyOfCall(0));
  });

  it("shows the server's refusal of a file and keeps the form", async () => {
    listCaseDocuments.mockResolvedValue([]);
    uploadCaseDocument.mockRejectedValue(
      apiError(
        415,
        "unsupported_media_type",
        "chỉ nhận PDF, JPEG, PNG, XLSX, DOCX, EML hoặc MSG",
      ),
    );
    renderCard();
    await screen.findByText("Hồ sơ chưa có chứng từ nào.");

    await chooseType("Đơn đặt hàng (PO)");
    await chooseFile("virus.exe");
    fireEvent.click(screen.getByRole("button", { name: /Tải lên/ }));

    expect(
      await screen.findByText(
        "chỉ nhận PDF, JPEG, PNG, XLSX, DOCX, EML hoặc MSG",
      ),
    ).toBeTruthy();
    expect(screen.getByText("virus.exe")).toBeTruthy();
  });

  it("downloads a document through the API, saved under its own name", async () => {
    listCaseDocuments.mockResolvedValue([doc()]);
    downloadCaseDocument.mockResolvedValue(new Blob(["%PDF"]));
    const click = vi
      .spyOn(HTMLAnchorElement.prototype, "click")
      .mockImplementation(() => {});
    renderCard();

    const row = (await screen.findByText("PO-0042.pdf")).closest("tr");
    if (!row) throw new Error("no row");
    await act(async () => {
      fireEvent.click(within(row).getByRole("button", { name: /Tải xuống/ }));
    });

    await waitFor(() =>
      expect(downloadCaseDocument).toHaveBeenCalledWith(doc().id),
    );
    await waitFor(() => expect(click).toHaveBeenCalled());
    click.mockRestore();
  });
});
