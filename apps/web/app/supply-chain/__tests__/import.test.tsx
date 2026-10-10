import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { App } from "antd";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ImportReport } from "@dw/api-client";

vi.setConfig({ testTimeout: 30_000 });

let scopes = new Set(["supply_chain.import"]);
vi.mock("../../../lib/auth/auth-context", () => ({
  useAuth: () => ({
    active: { workspaceId: "w" },
    hasScope: (s: string) => scopes.has(s),
  }),
}));
let online = true;
vi.mock("../../../lib/hooks/use-online", () => ({
  useOnline: () => online,
}));

const runImport = vi.fn();
const downloadImportTemplate = vi.fn();
vi.mock("../../../lib/session", () => ({
  apiClient: () => ({ runImport, downloadImportTemplate }),
}));

import ImportPage from "../import/page";
import {
  DRY_RUN_FIRST,
  OFFLINE_LOCK,
} from "../../../components/supply-chain/import-labels";

function report(dryRun: boolean): ImportReport {
  return {
    dry_run: dryRun,
    sheets: [
      {
        sheet: "suppliers",
        title: "NCC",
        created: 1,
        exists: 0,
        partial: 0,
        rejected: 1,
      },
      {
        sheet: "catalogue",
        title: "Danh mục",
        created: 0,
        exists: 0,
        partial: 0,
        rejected: 0,
      },
      {
        sheet: "users",
        title: "Người dùng",
        created: 0,
        exists: 0,
        partial: 0,
        rejected: 0,
      },
    ],
    problems: [],
    rows: [
      {
        sheet: "suppliers",
        row: 2,
        key: "MP01",
        status: "created",
        messages: ["NCC: tạo mới"],
      },
      {
        sheet: "suppliers",
        row: 3,
        key: "MP02",
        status: "rejected",
        messages: ["thiếu Tên NCC"],
      },
    ],
  };
}

function renderPage() {
  return render(
    <App>
      <ImportPage />
    </App>,
  );
}

function chooseFile(): File {
  const file = new File(["x"], "nap.xlsx", {
    type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  });
  const input = document.querySelector(
    'input[type="file"]',
  ) as HTMLInputElement;
  fireEvent.change(input, { target: { files: [file] } });
  return file;
}

afterEach(() => {
  cleanup();
  runImport.mockReset();
  scopes = new Set(["supply_chain.import"]);
  online = true;
});

describe("import page", () => {
  it("without the import scope says why and offers nothing", () => {
    scopes = new Set();
    renderPage();
    expect(screen.getByText(/supply_chain.import/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: /Chạy thử/ })).toBeNull();
  });

  it("opens the real import only after a dry run of the same file", async () => {
    runImport.mockResolvedValueOnce(report(true));
    renderPage();
    const apply = () =>
      screen.getByRole("button", { name: /Nạp thật/ }) as HTMLButtonElement;
    expect(apply().disabled).toBe(true);
    const file = chooseFile();
    await waitFor(() => expect(screen.getByText(DRY_RUN_FIRST)).toBeTruthy());
    expect(apply().disabled).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: /Chạy thử/ }));
    await waitFor(() =>
      expect(
        screen.getByText("Chạy thử: chưa ghi gì vào hệ thống."),
      ).toBeTruthy(),
    );
    expect(runImport).toHaveBeenCalledWith(file, "dry-run", expect.any(String));
    expect(screen.getByText("Sẽ thêm", { selector: "span" })).toBeTruthy();
    expect(screen.getByText("thiếu Tên NCC")).toBeTruthy();
    expect(apply().disabled).toBe(false);
    // A new file needs its own dry run.
    chooseFile();
    await waitFor(() => expect(apply().disabled).toBe(true));
  });

  it("offline, nothing runs and the reason is in words", async () => {
    online = false;
    renderPage();
    chooseFile();
    await waitFor(() => expect(screen.getByText(OFFLINE_LOCK)).toBeTruthy());
    expect(
      (screen.getByRole("button", { name: /Chạy thử/ }) as HTMLButtonElement)
        .disabled,
    ).toBe(true);
  });

  it("shows the server's refusal", async () => {
    runImport.mockRejectedValueOnce(new Error("file không phải Excel"));
    renderPage();
    chooseFile();
    fireEvent.click(screen.getByRole("button", { name: /Chạy thử/ }));
    await waitFor(() =>
      expect(screen.getByText(/file không phải Excel/)).toBeTruthy(),
    );
  });
});
