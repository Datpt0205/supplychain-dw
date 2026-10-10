import { expect, test, type Page } from "@playwright/test";

/**
 * Every platform page, rebuilt on antd and in Vietnamese, at a 320px phone and
 * on both sides of antd's lg (991/992px): it renders its one h1, the page does
 * not scroll sideways, the Content-Security-Policy holds (no violation, no
 * page error), and the shell is reached through Vietnamese names.
 *
 * Needs the stack: the API on 8000 with the seed (dev|chi.le is the tenant's
 * platform_admin and a platform operator).
 */

const API_URL = process.env.E2E_API_URL ?? "http://127.0.0.1:8000";
const SUBJECT = "dev|chi.le";

const PAGES: [string, string][] = [
  ["/", "Xin chào"],
  ["/approvals", "Duyệt"],
  ["/knowledge", "Tri thức"],
  ["/memory", "Bộ nhớ dài hạn"],
  ["/integrations", "Tích hợp và kết nối"],
  ["/audit", "Nhật ký kiểm toán"],
  ["/admin", "Vai trò và quyền"],
  ["/admin/workspaces", "Workspace"],
  ["/admin/hierarchy", "Tuyến báo cáo"],
  ["/admin/separation-of-duties", "Tách nhiệm"],
  ["/admin/settings", "Thiết lập công ty"],
  ["/admin/feedback", "Hộp phản hồi"],
  ["/platform", "Nền tảng"],
];

async function login(page: Page): Promise<void> {
  const users = await (
    await page.request.get(`${API_URL}/api/v1/dev/demo-users`)
  ).json();
  const index = users.findIndex(
    (user: { subject: string }) => user.subject === SUBJECT,
  );
  expect(index).toBeGreaterThanOrEqual(0);
  await page.goto("/dev-login");
  const buttons = page.getByRole("button", { name: "Đăng nhập" });
  await buttons.first().waitFor({ state: "visible" });
  await buttons.nth(index).click();
  await page.waitForURL("**/");
}

/** CSP violations and uncaught errors, collected for the whole test. */
function watch(page: Page): { violations: string[]; errors: string[] } {
  const seen = { violations: [] as string[], errors: [] as string[] };
  page.on("console", (message) => {
    if (/Content Security Policy|Refused to/i.test(message.text())) {
      seen.violations.push(message.text());
    }
  });
  page.on("pageerror", (error) => seen.errors.push(error.message));
  return seen;
}

test("every platform page renders, fits, and keeps its CSP", async ({
  page,
}) => {
  const seen = watch(page);
  await login(page);
  expect(await page.evaluate(() => document.documentElement.lang)).toBe("vi");

  for (const [path, title] of PAGES) {
    const response = await page.goto(path);
    const policy = response?.headers()["content-security-policy"] ?? "";
    expect(policy, path).toMatch(/script-src [^;]*'nonce-[^']+'/);
    expect(policy, path).toContain("'strict-dynamic'");
    await expect(
      page.getByRole("heading", { level: 1, name: title }),
      path,
    ).toBeVisible();
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - window.innerWidth,
    );
    expect(
      overflow,
      `${path} scrolls sideways by ${overflow}px`,
    ).toBeLessThanOrEqual(0);
  }
  expect(seen.violations).toEqual([]);
  expect(seen.errors).toEqual([]);
});

test("the shell is reached by its Vietnamese names", async ({ page }) => {
  await login(page);
  const width = page.viewportSize()!.width;

  if (width < 992) {
    await expect(
      page.getByRole("navigation", { name: "Điều hướng chính" }),
    ).toHaveCount(0);
    await page.getByRole("button", { name: "Mở menu" }).click();
    const drawer = page.getByRole("dialog");
    await drawer
      .getByRole("navigation", { name: "Điều hướng chính" })
      .getByRole("link", { name: "Nhật ký kiểm toán" })
      .click();
  } else {
    await expect(page.getByRole("button", { name: "Mở menu" })).toHaveCount(0);
    const nav = page.getByRole("navigation", { name: "Điều hướng chính" });
    await expect(nav).toBeVisible();
    // An item is read by its label alone, not "check-square Duyệt". The
    // first one, whichever it is: a context's pages come before the
    // platform's (lib/nav/registry.ts), and an admin's long bar sends the
    // later ones into antd's overflow menu.
    const first = nav.getByRole("menuitem").first();
    await expect(first).toBeVisible();
    await expect(first).toHaveAccessibleName((await first.innerText()).trim());
    await page.goto("/audit");
  }
  await expect(
    page.getByRole("heading", { level: 1, name: "Nhật ký kiểm toán" }),
  ).toBeVisible();

  // Bell, account menu and the feedback button, by name.
  await expect(page.getByRole("button", { name: /^Thông báo/ })).toBeVisible();
  await page.getByRole("button", { name: /^Tài khoản/ }).click();
  await expect(page.getByRole("menuitem", { name: "Đăng xuất" })).toBeVisible();
  // The account menu names the operator, never a role code.
  await expect(
    page.getByRole("menu").getByText("Quản trị nền tảng"),
  ).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(
    page.getByRole("button", { name: "Gửi phản hồi" }),
  ).toBeVisible();
});
