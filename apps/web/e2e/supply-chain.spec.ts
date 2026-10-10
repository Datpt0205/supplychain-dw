import { execFileSync } from "node:child_process";
import path from "node:path";
import { expect, test, type Page } from "@playwright/test";

/**
 * Supply Chain's rebuilt pages in a browser (ticket antd-pages 01, slice W):
 * each one stands at 320 px and on both sides of antd's `lg` (991 / 992 px),
 * and every control the walk uses is found by its role and Vietnamese name.
 * The widths are the `sc-*` projects of `playwright.config.ts`, which owns
 * the viewport list (ui-quality.md 12); the 992 one runs in Tokyo's zone, so
 * "giờ Việt Nam" is checked against a browser that is not in Vietnam.
 *
 * Unlike the other specs this one signs in through Keycloak: three of the
 * personas it needs (Linh, R&D; Khánh, BGĐ) are not on the dev-login roster.
 * So it runs against a web and API started in `oidc` mode, as `make dev`
 * does from `.env`, and `E2E_WEB_URL` names that web. Before it:
 *
 *   uv run python scripts/seed_supply_chain_demo.py seed
 *   uv run python scripts/seed_supply_chain_demo.py e2e-fixtures
 *   uv run python scripts/keycloak_dev_users.py
 *
 * with the API holding `DW_APPROVAL_CODE_SECRET`, so a code can be issued.
 * After the walk, each project removes the case it proposed (`e2e-cleanup
 * <code>`), so the dev database does not collect one `E2E-…` case per run;
 * `e2e-cleanup` with no code removes any a crashed run left.
 * The walk is one product case from proposal to BGĐ's review: An proposes it
 * and asks for a sample, Linh receives and passes it with its evaluation
 * uploaded inside the step's form, Khánh asks for a Zalo code on the review.
 */

const PASSWORD = process.env.DW_DEV_USER_PASSWORD ?? "";
const AN = "an.nguyen@alpha.local";
const LINH = "linh.phan@alpha.local";
const KHANH = "khanh.ngo@alpha.local";
const PROPOSAL = `E2E-${Date.now().toString(36).toUpperCase()}`;
const PRODUCT = `Nồi thử E2E ${PROPOSAL}`;

test.describe.configure({ mode: "serial" });
test.skip(!PASSWORD, "DW_DEV_USER_PASSWORD is not set (source .env)");

// The repository root, where `uv` finds the workspace and the seed script.
const REPO = path.resolve(__dirname, "../../..");

test.afterAll(() => {
  // The case this project's walk proposed, whatever step it reached; its
  // approvals and notices go with it. Through the seed script, which owns
  // what the suite writes beyond what a person could undo in the browser.
  execFileSync(
    "uv",
    [
      "run",
      "python",
      "scripts/seed_supply_chain_demo.py",
      "e2e-cleanup",
      PROPOSAL,
    ],
    { cwd: REPO, stdio: "inherit" },
  );
});

async function signIn(page: Page, email: string): Promise<void> {
  await page.goto("/");
  const signInButton = page.getByRole("button", { name: "Sign in" });
  await page.waitForURL(/realms\/dw/, { timeout: 30_000 }).catch(async () => {
    await signInButton.click();
    await page.waitForURL(/realms\/dw/);
  });
  await page.locator("#username").fill(email);
  await page.locator("#password").fill(PASSWORD);
  await page.locator("#kc-login").click();
  await page.waitForURL((url) => !url.href.includes("realms/dw"));
  await expect(page.getByRole("banner")).toBeVisible();
}

/** Nothing scrolls the page sideways, and no text is cut off: neither by a
 * box that hides its overflow, nor by an ellipsis with no `title` to show
 * the rest (ui-quality.md 8: truncate only with the full text on hover). */
async function fits(page: Page, where: string): Promise<void> {
  const report = await page.evaluate(() => {
    const root = document.documentElement;
    const clipped: string[] = [];
    for (const element of Array.from(document.querySelectorAll("main *"))) {
      const el = element as HTMLElement;
      const style = getComputedStyle(el);
      if (style.overflowX !== "hidden" && style.overflowX !== "clip") continue;
      if (el.clientWidth === 0) continue;
      // A wide table scrolls inside its container (ui-quality.md 11); its
      // sticky header hides what the body has scrolled past.
      if (el.classList.contains("ant-table-header")) continue;
      // An ellipsis is fine where the whole text is a hover away.
      if (style.textOverflow === "ellipsis" && el.closest("[title]")) continue;
      if (el.scrollWidth <= el.clientWidth + 1) continue;
      if (!el.innerText?.trim()) continue;
      clipped.push(
        `${el.tagName}.${el.className}: ${el.innerText.slice(0, 60)}`,
      );
    }
    return { scroll: root.scrollWidth, client: root.clientWidth, clipped };
  });
  expect(
    report.scroll,
    `${where}: the page scrolls sideways`,
  ).toBeLessThanOrEqual(report.client);
  expect(report.clipped, `${where}: text cut off`).toEqual([]);
}

/** The navbar on its side of `lg`: the menu under 992 px, a drawer below. */
async function navbar(page: Page): Promise<void> {
  const width = page.viewportSize()!.width;
  const menu = page.getByRole("button", { name: "Mở menu" });
  await expect(
    page.getByRole("button", { name: /^Tài khoản: / }),
  ).toBeVisible();
  if (width >= 992) {
    await expect(menu).toHaveCount(0);
    const nav = page.getByRole("navigation", { name: "Điều hướng chính" });
    await expect(nav).toBeVisible();
    // Named by its label alone, not by its icon's glyph as well.
    await expect(
      nav.getByRole("menuitem", { name: "Bản tin hôm nay", exact: true }),
    ).toBeVisible();
  } else {
    await expect(menu).toBeVisible();
  }
}

/** One page at this project's width: its h1, the navbar, its controls by
 * name, the fit, and a screenshot kept with the report. */
async function stands(
  page: Page,
  path: string,
  heading: string | RegExp,
  controls: (page: Page) => Promise<void>,
): Promise<void> {
  if (new URL(page.url()).pathname !== path) await page.goto(path);
  await expect(
    page.getByRole("heading", { level: 1, name: heading }),
  ).toBeVisible();
  await navbar(page);
  await controls(page);
  await fits(page, path);
  await test.info().attach(path, {
    body: await page.screenshot({ fullPage: true }),
    contentType: "image/png",
  });
}

/** "HH:mm dd/MM/yyyy" in Vietnam, `minutesAgo` before now, the way
 * `lib/dates.ts` writes a time whatever zone the browser is in. */
function vietnamTime(minutesAgo: number): string {
  const parts = new Intl.DateTimeFormat("en-GB", {
    timeZone: "Asia/Ho_Chi_Minh",
    hourCycle: "h23",
    hour: "2-digit",
    minute: "2-digit",
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
  }).formatToParts(new Date(Date.now() - minutesAgo * 60_000));
  const part = (type: string) => parts.find((p) => p.type === type)!.value;
  return `${part("hour")}:${part("minute")} ${part("day")}/${part("month")}/${part("year")}`;
}

let caseUrl = "";
let approvalUrl = "";

test("An: the brief, the PO list and a PO case", async ({ page }) => {
  await signIn(page, AN);
  await stands(
    page,
    "/supply-chain/daily-brief",
    "Bản tin hôm nay",
    async (p) => {
      await expect(
        p.getByRole("button", { name: "Tóm tắt bằng AI" }),
      ).toBeVisible();
      await expect(p.getByRole("link", { name: "Mở Cần chú ý" })).toBeVisible();
    },
  );
  await stands(page, "/supply-chain/po-cases", "Hồ sơ PO", async (p) => {
    await expect(
      p.getByRole("radiogroup", { name: "Phạm vi hồ sơ" }),
    ).toBeVisible();
    await expect(
      p.getByRole("combobox", { name: "Lọc theo trạng thái" }),
    ).toBeVisible();
    await expect(
      p.getByRole("searchbox", { name: "Tìm theo số PO hoặc NCC" }),
    ).toBeVisible();
    await expect(p.getByRole("button", { name: /search/i })).toHaveCount(0);
    await expect(p.getByRole("link", { name: "PO-DEMO-003" })).toBeVisible();
  });
  await page.getByRole("link", { name: "PO-DEMO-003" }).click();
  await page.waitForURL(/\/po-cases\/[0-9a-f-]{36}$/);
  await stands(page, new URL(page.url()).pathname, /PO-DEMO-003/, async (p) => {
    await expect(p.getByText("Chứng từ", { exact: true })).toBeVisible();
    await expect(
      p.getByRole("combobox", { name: "Loại chứng từ" }),
    ).toBeVisible();
    await expect(
      p.getByRole("button", { name: "Chọn file" }).first(),
    ).toBeVisible();
  });
});

test("An: proposes a product and asks for its sample", async ({ page }) => {
  await signIn(page, AN);
  await stands(
    page,
    "/supply-chain/product-cases",
    "Hồ sơ phát triển sản phẩm",
    async (p) => {
      await expect(
        p.getByRole("radiogroup", { name: "Hồ sơ của ai" }),
      ).toBeVisible();
      await expect(
        p.getByRole("searchbox", {
          name: "Tìm theo mã đề xuất, tên sản phẩm hoặc Category",
        }),
      ).toBeVisible();
      await expect(p.getByRole("button", { name: /search/i })).toHaveCount(0);
    },
  );
  await page.getByRole("button", { name: "Đề xuất sản phẩm" }).first().click();
  const propose = page.getByRole("dialog", { name: "Đề xuất sản phẩm" });
  await propose.getByRole("textbox", { name: "Mã đề xuất" }).fill(PROPOSAL);
  await propose.getByRole("textbox", { name: "Tên sản phẩm" }).fill(PRODUCT);
  await propose.getByRole("combobox", { name: "Category" }).click();
  await page.getByRole("option", { name: "Nồi" }).click();
  await fits(page, "propose dialog ");
  await propose.getByRole("button", { name: "Đề xuất" }).click();
  await expect(propose).toBeHidden();
  // Proposing opens the new case.
  await page.waitForURL(/\/product-cases\/[0-9a-f-]{36}$/);
  caseUrl = new URL(page.url()).pathname;

  await stands(page, caseUrl, PRODUCT, async (p) => {
    await expect(p.getByRole("button", { name: "Yêu cầu mẫu" })).toBeEnabled();
    // An is not R&D: R&D's steps are not offered, or offered with a reason.
    await expect(
      p.getByRole("combobox", { name: "Loại chứng từ" }),
    ).toBeVisible();
  });
  await page.getByRole("button", { name: "Yêu cầu mẫu" }).click();
  const step = page.getByRole("dialog", { name: /Yêu cầu mẫu/ });
  await step.getByRole("textbox", { name: "NCC" }).fill("Elmich");
  await fits(page, "request_sample form ");
  await step.getByRole("button", { name: "Yêu cầu mẫu" }).click();
  await expect(step).toBeHidden();
});

test("Linh: receives the sample and passes it with its evaluation", async ({
  page,
}) => {
  test.skip(!caseUrl, "no case from the step before");
  await signIn(page, LINH);
  await page.goto(caseUrl);
  await page.getByRole("button", { name: "Đã nhận mẫu" }).click();
  const receive = page.getByRole("dialog", { name: /Đã nhận mẫu/ });
  await receive.getByRole("button", { name: "Đã nhận mẫu" }).click();
  await expect(receive).toBeHidden();

  await page.getByRole("button", { name: "Mẫu đạt" }).click();
  const pass = page.getByRole("dialog", { name: /Mẫu đạt/ });
  // The step needs its paper: the form says so and its button waits for it.
  await expect(
    pass.getByText("Chưa có Biên bản đánh giá mẫu nào cho bước này.", {
      exact: false,
    }),
  ).toBeVisible();
  await expect(pass.getByRole("button", { name: "Mẫu đạt" })).toBeDisabled();
  await pass.locator("input[type=file]").setInputFiles({
    name: "bien-ban-danh-gia.pdf",
    mimeType: "application/pdf",
    buffer: Buffer.from("%PDF-1.4\n%e2e\n"),
  });
  await pass
    .getByRole("button", { name: "Tải lên Biên bản đánh giá mẫu" })
    .click();
  await expect(pass.getByRole("button", { name: "Mẫu đạt" })).toBeEnabled();
  await fits(page, "pass_sample form ");
  await pass.getByRole("button", { name: "Mẫu đạt" }).click();
  await expect(pass).toBeHidden();
  await expect(
    page.getByText("Mẫu đã đạt; hồ sơ chờ BGĐ duyệt."),
  ).toBeVisible();
});

test("Khánh: the review in Duyệt, and a Zalo code once he has commented", async ({
  page,
}) => {
  test.skip(!caseUrl, "no case from the steps before");
  await signIn(page, KHANH);
  // This run's review, among any an earlier run left pending.
  const card = page.locator(".ant-card").filter({ hasText: PROPOSAL });
  await stands(page, "/approvals", "Duyệt", async (p) => {
    await expect(p.getByRole("button", { name: "Làm mới" })).toBeVisible();
    await expect(
      card.getByRole("link", { name: "BGĐ duyệt mẫu" }),
    ).toBeVisible();
    // Strict: no decision without a comment, and the page says so.
    await expect(card.getByRole("button", { name: "Duyệt" })).toBeDisabled();
    await expect(card.getByRole("button", { name: "Từ chối" })).toBeDisabled();
    await expect(
      card.getByText(
        "Loại yêu cầu này cần nhận xét: nhập nhận xét rồi mới quyết được.",
      ),
    ).toBeVisible();
  });
  const comment = card.getByRole("textbox", {
    name: "Nhận xét cho BGĐ duyệt mẫu",
  });
  await comment.fill("Xem trước khi quyết.");
  await expect(card.getByRole("button", { name: "Duyệt" })).toBeEnabled();
  await comment.fill("");
  await card.getByRole("link", { name: "BGĐ duyệt mẫu" }).click();
  await page.waitForURL(/\/approvals\/[0-9a-f-]{36}/);
  approvalUrl = new URL(page.url()).pathname;

  await stands(page, approvalUrl, "BGĐ duyệt mẫu", async (p) => {
    const getCode = p.getByRole("button", { name: "Lấy mã để quyết qua Zalo" });
    // A strict review: locked until there is a comment, and it says why.
    await expect(getCode).toBeDisabled();
    await expect(
      p.getByText("Loại yêu cầu này cần nhận xét trước khi lấy mã.").first(),
    ).toBeVisible();
    await expect(
      p.getByRole("textbox", { name: "Nhận xét (bắt buộc)" }),
    ).toBeVisible();
    // The view just recorded, in Vietnam's time whatever the browser's zone.
    const seen = `(${vietnamTime(0)}|${vietnamTime(1)}) \\(giờ Việt Nam\\)`;
    const viewedRow = p
      .locator(".ant-descriptions-row")
      .filter({ hasText: "Bạn xem lúc" });
    await expect(viewedRow.getByText(new RegExp(seen))).toBeVisible();
  });
  await page
    .getByRole("textbox", { name: "Nhận xét (bắt buộc)" })
    .fill("Mẫu đạt, đồng ý.");
  await page.getByRole("button", { name: "Lấy mã để quyết qua Zalo" }).click();
  const issued = page.getByRole("status").filter({ hasText: "Mã của bạn" });
  await expect(issued).toBeVisible();
  await expect(issued).toContainText(/DUYỆT \d{6}/);
  await expect(page.getByRole("button", { name: "Lấy mã mới" })).toBeEnabled();
  await fits(page, "approval with a code ");
});

test("Khánh: settings, the Zalo card and its workspace select", async ({
  page,
}) => {
  await signIn(page, KHANH);
  await stands(page, "/settings", "Cài đặt cá nhân", async (p) => {
    await expect(
      p.getByRole("button", { name: "Đổi Zalo khác" }),
    ).toBeVisible();
    await expect(p.getByRole("button", { name: "Ngắt kết nối" })).toBeVisible();
    await expect(
      p.getByRole("combobox", { name: "Workspace dùng cho Zalo" }),
    ).toBeVisible();
  });
});
