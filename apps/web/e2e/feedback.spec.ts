import {
  expect,
  test,
  type APIRequestContext,
  type Page,
} from "@playwright/test";

/**
 * Spec 003 US5 — feedback is a round button at the bottom-left of every page,
 * not a nav item; its dialog asks exactly four things and takes screenshots
 * by paste, drop or picker; admins read it (screenshots included) under
 * Admin → Feedback inbox.
 */

const LOGIN_BUTTON = "Đăng nhập";
const API_URL = process.env.E2E_API_URL ?? "http://127.0.0.1:8000";
// The seeded tenant's platform_admin (`seed_env.py`): dieu.hoang, named here before,
// is a member there and the inbox refuses her.
const ADMIN_SUBJECT = "dev|chi.le";

// A 1×1 PNG, so the paste path carries a real image through the whole stack.
const PNG_BASE64 =
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==";

async function loginAsDemoUser(page: Page): Promise<void> {
  await page.goto("/dev-login");
  const firstUser = page.getByRole("button", { name: LOGIN_BUTTON }).first();
  await firstUser.waitFor({ state: "visible" });
  await firstUser.click();
  await page.waitForURL("**/");
}

async function loginAsSubject(
  page: Page,
  request: APIRequestContext,
  subject: string,
): Promise<void> {
  const users = await (
    await request.get(`${API_URL}/api/v1/dev/demo-users`)
  ).json();
  const index = users.findIndex(
    (user: { subject: string }) => user.subject === subject,
  );
  expect(index).toBeGreaterThanOrEqual(0);
  await page.goto("/dev-login");
  const buttons = page.getByRole("button", { name: LOGIN_BUTTON });
  await buttons.first().waitFor({ state: "visible" });
  await buttons.nth(index).click();
  await page.waitForURL("**/");
}

/** Paste an image into the dialog the way Ctrl+V does. */
async function pasteImage(page: Page): Promise<void> {
  await page.getByRole("dialog").evaluate((dialog, base64) => {
    const bytes = Uint8Array.from(atob(base64), (c) => c.charCodeAt(0));
    const file = new File([bytes], "pasted.png", { type: "image/png" });
    const data = new DataTransfer();
    data.items.add(file);
    dialog
      .querySelector("[role=group]")!
      .parentElement!.parentElement!.dispatchEvent(
        new ClipboardEvent("paste", { clipboardData: data, bubbles: true }),
      );
  }, PNG_BASE64);
}

test("US5: the button sits bottom-left on every page and feedback is not in the nav", async ({
  page,
}) => {
  await loginAsDemoUser(page);
  for (const path of ["/audit", "/memory"]) {
    await page.goto(path);
    const launcher = page.getByRole("button", { name: "Gửi phản hồi" });
    await expect(launcher).toBeVisible();
    const box = (await launcher.boundingBox())!;
    const viewport = page.viewportSize()!;
    expect(box.x).toBeLessThan(viewport.width / 4);
    expect(box.y + box.height).toBeGreaterThan(viewport.height * 0.8);
  }
  await expect(
    page.getByRole("link", { name: "Gửi phản hồi", exact: true }),
  ).toHaveCount(0);
});

test("US5: the dialog asks four things, takes a pasted screenshot, and sends", async ({
  page,
}) => {
  await loginAsDemoUser(page);
  await page.goto("/audit");
  await page.getByRole("button", { name: "Gửi phản hồi" }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toBeVisible();

  // Exactly the four parts — and none of the reference form's two follow-ups.
  // The module the page belongs to is preselected (antd Select shows it as
  // the selected item's title).
  await expect(
    dialog.getByRole("combobox", { name: /Module đang gặp lỗi/ }),
  ).toBeVisible();
  await expect(dialog.getByTitle("Nhật ký kiểm toán")).toBeVisible();
  await expect(dialog.getByLabel(/Mô tả lỗi/)).toBeVisible();
  await expect(dialog.getByLabel(/Đề xuất giải pháp/)).toBeVisible();
  await expect(
    dialog.getByRole("group", { name: "Ảnh đính kèm" }),
  ).toBeVisible();
  await expect(dialog.getByText(/share|contact you|liên hệ/i)).toHaveCount(0);

  await dialog.getByLabel(/Mô tả lỗi/).fill(`E2E feedback ${Date.now()}`);
  await dialog.getByLabel(/Đề xuất giải pháp/).fill("Thêm bộ lọc theo ngày.");
  await pasteImage(page);
  await expect(dialog.getByRole("img", { name: "pasted.png" })).toBeVisible();
  // A preview can be removed before sending.
  await dialog.getByRole("button", { name: "Xoá pasted.png" }).click();
  await expect(dialog.getByRole("img", { name: "pasted.png" })).toHaveCount(0);
  await pasteImage(page);

  await dialog.getByRole("button", { name: "Gửi" }).click();
  await expect(page.getByText(/phản hồi đã tới quản trị viên/)).toBeVisible();
  await expect(dialog).toBeHidden();
});

test("US5: the admin inbox lists the feedback with its screenshot", async ({
  page,
  request,
}) => {
  const marker = `Inbox ${Date.now()}`;
  await loginAsDemoUser(page);
  await page.goto("/audit");
  await page.getByRole("button", { name: "Gửi phản hồi" }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel(/Mô tả lỗi/).fill(marker);
  await pasteImage(page);
  await dialog.getByRole("button", { name: "Gửi" }).click();
  await expect(dialog).toBeHidden();

  await loginAsSubject(page, request, ADMIN_SUBJECT);
  await page.goto("/admin/feedback");
  const entry = page.locator("li", { hasText: marker }).first();
  await expect(entry).toBeVisible();
  await expect(entry.getByText("Nhật ký kiểm toán")).toBeVisible();
  await expect(
    entry.getByRole("img", { name: "Ảnh đính kèm phản hồi" }),
  ).toBeVisible({
    timeout: 15_000,
  });
});
