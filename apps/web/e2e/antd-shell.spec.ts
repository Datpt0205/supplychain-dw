import { expect, test, type Page } from "@playwright/test";

/**
 * antd and Tailwind in one cascade, and the shell built on it.
 *
 * The layer order in globals.css fails silently when it is wrong: Tailwind
 * classes on antd components are ignored, or Tailwind's reset strips antd's
 * buttons, and nothing errors. So it is checked here, in a browser, from
 * computed styles - reading the CSS text would agree with a file that is
 * wrong. The page is a dev-only fixture that needs no API.
 */

const FIXTURE = "/dev-login/layer-check";

// What the browser reports for the theme's colours (@dw/ui theme.ts).
const LIGHT_PRIMARY = "rgb(0, 110, 220)";
const LIGHT_LINK = "rgb(0, 96, 192)";
const LIGHT_LAYOUT = "rgb(245, 245, 247)";
const DARK_LAYOUT = "rgb(0, 0, 0)";
const DARK_LINK = "rgb(77, 163, 255)";
// antd's seed tokens, which its dark algorithm re-derives unless the theme
// pins them: colorPrimary would come out #0363c4.
const DARK_SEEDS = {
  "--ant-color-primary": "#0071e3",
  "--ant-color-link": "#4da3ff",
  "--ant-color-success": "#30d158",
  "--ant-color-warning": "#ff9f0a",
  "--ant-color-error": "#d63a30",
};

test.use({ colorScheme: "light", viewport: { width: 1280, height: 800 } });

function fixtureButton(page: Page) {
  return page.getByRole("button", { name: "Kiểm tra" });
}

test("a Tailwind utility beats antd, and antd beats Tailwind's reset", async ({
  page,
}) => {
  await page.goto(FIXTURE);
  const button = fixtureButton(page);
  await expect(button).toBeVisible();
  const style = await button.evaluate((element) => {
    const computed = getComputedStyle(element);
    return {
      paddingLeft: computed.paddingLeft,
      background: computed.backgroundColor,
    };
  });
  // `px-10` is 2.5rem; antd's own padding for this button is 15px.
  expect(style.paddingLeft).toBe("40px");
  // The reset makes a button's background transparent; antd's primary must
  // survive it, in the theme's colour.
  expect(style.background).toBe(LIGHT_PRIMARY);
});

test("every antd variable the app's stylesheet names is defined", async ({
  page,
}) => {
  await page.goto(FIXTURE);
  await expect(fixtureButton(page)).toBeVisible();
  const { named, missing } = await page.evaluate(() => {
    const names = new Set<string>();
    // The app's own CSS arrives as <link> sheets; antd's are <style> tags and
    // name per-component variables that are not meant to be on <html>.
    for (const sheet of Array.from(document.styleSheets)) {
      if (!sheet.href) continue;
      for (const rule of Array.from(sheet.cssRules)) {
        for (const match of rule.cssText.matchAll(/var\((--ant-[a-z0-9-]+)/g)) {
          names.add(match[1]!);
        }
      }
    }
    const root = getComputedStyle(document.documentElement);
    return {
      named: names.size,
      missing: [...names].filter((name) => !root.getPropertyValue(name).trim()),
    };
  });
  // A sheet that was never read would pass the check below vacuously.
  expect(named).toBeGreaterThan(10);
  expect(missing).toEqual([]);
});

test("the theme follows the OS colour scheme, both ways", async ({ page }) => {
  await page.goto(FIXTURE);
  await expect(fixtureButton(page)).toBeVisible();
  const layout = () =>
    page.evaluate(() => getComputedStyle(document.body).backgroundColor);
  expect(await layout()).toBe(LIGHT_LAYOUT);
  await page.emulateMedia({ colorScheme: "dark" });
  await expect.poll(layout).toBe(DARK_LAYOUT);
  await page.emulateMedia({ colorScheme: "light" });
  await expect.poll(layout).toBe(LIGHT_LAYOUT);
});

test("dark: the palette's own seed colours, and a readable current item", async ({
  page,
}) => {
  await page.emulateMedia({ colorScheme: "dark" });
  await page.goto(FIXTURE);
  await expect(fixtureButton(page)).toBeVisible();
  const seeds = () =>
    page.evaluate((names) => {
      const root = getComputedStyle(document.documentElement);
      return Object.fromEntries(
        names.map((name) => [name, root.getPropertyValue(name).trim()]),
      );
    }, Object.keys(DARK_SEEDS));
  await expect.poll(seeds).toEqual(DARK_SEEDS);
  // antd's colorPrimary on the dark header is 3.62:1; the link colour passes.
  const current = page
    .getByRole("navigation", { name: "Điều hướng chính" })
    .getByRole("menuitem", { name: "Trang này" })
    .getByRole("link");
  await expect(current).toHaveCSS("color", DARK_LINK);
});

test("keyboard focus on an antd control shows the app's outline", async ({
  page,
}) => {
  await page.goto(FIXTURE);
  const button = fixtureButton(page);
  await expect(button).toBeVisible();
  for (let i = 0; i < 10; i += 1) {
    await page.keyboard.press("Tab");
    if (await button.evaluate((element) => element === document.activeElement))
      break;
  }
  await expect(button).toBeFocused();
  // antd's own ring is 3px of colorPrimaryBorder, rgb(122, 202, 255): 1.79:1
  // on white. The app's outline is ordered after antd's layer.
  await expect(button).toHaveCSS("outline-color", LIGHT_PRIMARY);
  await expect(button).toHaveCSS("outline-width", "2px");
  await expect(button).toHaveCSS("outline-style", "solid");
});

test("Tailwind's lg and the navbar's switch to a drawer are the same width", async ({
  page,
}) => {
  for (const [width, wide] of [
    [991, false],
    [992, true],
  ] as const) {
    await page.setViewportSize({ width, height: 800 });
    await page.goto(FIXTURE);
    await expect(fixtureButton(page)).toBeVisible();
    const menuButton = page.getByRole("button", { name: "Mở menu" });
    const tailwindLg = page.getByTestId("tailwind-lg");
    if (wide) {
      await expect(menuButton).toHaveCount(0);
      await expect(tailwindLg).toBeVisible();
    } else {
      await expect(menuButton).toBeVisible();
      await expect(tailwindLg).toBeHidden();
    }
  }
});

test("every Tailwind breakpoint is antd's", async ({ page }) => {
  await page.goto(FIXTURE);
  await expect(fixtureButton(page)).toBeVisible();
  const spans = page.locator("[data-antd-min]");
  await expect(spans).toHaveCount(5);
  for (const span of await spans.all()) {
    const min = Number(await span.getAttribute("data-antd-min"));
    expect(min).toBeGreaterThan(0);
    await page.setViewportSize({ width: min - 1, height: 800 });
    await expect(span).toBeHidden();
    await page.setViewportSize({ width: min, height: 800 });
    await expect(span).toBeVisible();
  }
});

test("wide: a sticky 56px header with the menu, the page's item current", async ({
  page,
}) => {
  await page.goto(FIXTURE);
  const header = page.locator("header");
  await expect(header).toHaveCSS("position", "sticky");
  await expect(header).toHaveCSS("height", "56px");
  const nav = page.getByRole("navigation", { name: "Điều hướng chính" });
  await expect(
    nav.getByRole("menuitem", { name: "Trang này" }),
  ).toHaveAttribute("aria-current", "page");
  await expect(
    nav.getByRole("menuitem", { name: "Trang khác" }),
  ).not.toHaveAttribute("aria-current", /.*/);
});

test("wide: Enter on an item navigates; a click is left to the link", async ({
  page,
}) => {
  await page.goto(FIXTURE);
  const nav = page.getByRole("navigation", { name: "Điều hướng chính" });
  const navigated = page.getByTestId("navigated");
  await nav.getByRole("menuitem", { name: "Trang khác" }).click();
  await expect(page).toHaveURL(/#other$/);
  // The link navigated by itself; the shell did not navigate a second time.
  await expect(navigated).toHaveText("");
  await nav.getByRole("menuitem", { name: "Trang này" }).focus();
  await page.keyboard.press("Enter");
  await expect(navigated).toHaveText("here");
});

test("wide: an item that overflowed into the menu's … is no tab stop", async ({
  page,
}) => {
  await page.goto(FIXTURE);
  await expect(fixtureButton(page)).toBeVisible();
  // Too narrow for either item. antd hides an overflowed item with opacity
  // and aria-hidden, not display or inert, so its link stays focusable.
  await page.addStyleTag({ content: "header nav { max-width: 110px; }" });
  const hiddenLinks = page.locator('header [aria-hidden="true"] a');
  await expect(hiddenLinks).toHaveCount(2);
  const stops: string[] = [];
  for (let i = 0; i < 6; i += 1) {
    await page.keyboard.press("Tab");
    stops.push(
      await page.evaluate(() => {
        const element = document.activeElement!;
        const hidden = element.closest('[aria-hidden="true"]') !== null;
        return `${element.tagName}${hidden ? " (aria-hidden)" : ""}`;
      }),
    );
  }
  // The walk went round the page: the menu and the button are both in it.
  expect(stops).toContain("UL");
  expect(stops).toContain("BUTTON");
  expect(stops.filter((stop) => stop.endsWith("(aria-hidden)"))).toEqual([]);
});

test("narrow: the same items in a drawer, which closes on a choice", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 800 });
  await page.goto(FIXTURE);
  await expect(
    page.getByRole("navigation", { name: "Điều hướng chính" }),
  ).toHaveCount(0);
  await page.getByRole("button", { name: "Mở menu" }).click();
  const drawer = page.getByRole("dialog");
  await expect(drawer).toBeVisible();
  const nav = drawer.getByRole("navigation", { name: "Điều hướng chính" });
  const current = nav.getByRole("menuitem", { name: "Trang này" });
  await expect(current).toHaveAttribute("aria-current", "page");
  // On the selected item's background antd's colorPrimary is 4.27:1; the
  // link colour passes.
  await expect(current.getByRole("link")).toHaveCSS("color", LIGHT_LINK);
  await nav.getByRole("menuitem", { name: "Trang khác" }).click();
  await expect(drawer).toBeHidden();
});
