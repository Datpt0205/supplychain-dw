# Web UI shell

> **Inherited from `codebase`** (platform seed, `main` `bf553f4`, merged into this
> product repo as `platform/main`). It changes here only when work in this repo
> touches the area; after each `git merge platform/main` the upstream copy is the
> reference. Product work lives in `.claude/plans/supply-chain.md`.

`apps/web` and the shared `@dw/ui` package. CLAUDE.md's "Required stack"
names **Next.js, TypeScript strict, Tailwind, shadcn/ui** and "one shared UI
shell". Changing either is an architecture decision that has to be recorded
there, not a refactor.

## What the shell is (measured 2026-09-28)

- **Framework:** Next 15.5.25 (App Router), React 19.2.8, TypeScript 5.9.3.
- **Styling:** Tailwind 4.3.3; colour tokens in `app/globals.css`. There is
  no dark mode and no i18n library; the Vietnamese copy is hard-coded, and
  `lib/dates.ts` formats dates by hand.
- **Components:**
    - `@dw/ui`: 12 shadcn-style modules. Select and Switch are native elements,
      not Radix.
    - `components/ui`: 7 shadcn files, used by the assistant-ui pieces.
    - Also in use: radix-ui, lucide-react, sonner.
- **Usage:**
    - 42 of 70 non-test `.tsx` files import `@dw/ui`.
    - 927 `className=` uses in 59 files.
    - Most-used components: Button 54, TableCell 44, Card 31.
- **Missing kinds of widget:** no date inputs and no form library.
  `@tanstack/react-table` is declared, but its only user,
  `components/data-table.tsx`, is imported by nothing.

## Ant Design assessment (Đạt asked, 2026-09-28)

Measured in a scratch app with the repo's exact Next and React versions. The
repo was not touched.

- **antd v6.6.5 works:** peer `react >=18`, no React-19 patch needed, and
  `next build` produced zero warnings. `vi_VN` locale, `message` and
  `Modal.confirm` all ran clean in Playwright.
- **Cost:**
    - +250 kB first-load JS on a page using ConfigProvider, Table, Form,
      Select and DatePicker (104 → 354 kB);
    - about 420 KB of CSS inlined into that page's HTML;
    - a dev compile of 3 518 modules against 545.
- **Tailwind conflict:** Tailwind classes on antd components are silently
  ignored unless both `AntdRegistry layer` and
  `@layer theme, base, antd, components, utilities;` come before
  `@import "tailwindcss"`. That setup fails silently when it is wrong.

**Recommendation: option C.** Keep the stack, and add a focused library when a
screen first needs one:

- TanStack Table through the existing `DataTable`;
- react-hook-form with zod for complex forms;
- react-day-picker for the first date field.

Why not A or B: option A (full switch, about 60 files) and option B (antd for
tables, forms and dates only, about 10–12 files) both pay a fixed cost up
front: bundle size, two styling systems, and a CSS layer order that breaks
silently. That buys widgets the app does not use yet. A also still leaves the
927 Tailwind layout classes and the assistant-ui pieces on Tailwind.

**If Đạt still wants antd, the decision recorded in CLAUDE.md must settle:**

1. Replace shadcn/ui or coexist with it, and which component comes from
   which system.
2. v6 only.
3. The CSS layer order, with a test that fails without it.
4. One owner of the colour tokens, feeding both Tailwind and the antd theme.
5. The accepted bundle increase.
6. Who owns date formatting: `lib/dates.ts`, or antd/dayjs.
7. Whether `@dw/ui` becomes antd wrappers, so "one shared UI shell" stays
   true.

## Decision (Đạt, 2026-09-28): switch to antd v6

Đạt chose antd over option C after seeing a working antd v6 prototype with
a top navbar. CLAUDE.md "Web UI" records the seven
points above. Its settled answers are: replace shadcn/ui, page by page; the
antd theme owns the tokens; dayjs behind `lib/dates.ts`; and `@dw/ui` holds
the theme, the shell and the shared composites, not primitive wrappers.

## First slice: the antd shell (2026-10-02, committed)

Spec and tickets: [`web-ui/antd-shell/`](web-ui/antd-shell/spec.md) (18
tickets; 01 CI and 02 this slice resolved, 03–09 ready, 10 waits on Đạt; 11–18
added 2026-10-03 for the design parts no ticket owned: button and field
conventions, skip link, session-expiry warning, dark mode before hydration,
colour-mode choice, command palette, `lang="vi"` and menu labels).

- **Dependencies:** antd 6.6.5, @ant-design/icons 6.3.4,
  @ant-design/nextjs-registry 1.3.0 and dayjs 1.11.23 in `apps/web`.
  `@dw/ui` takes antd, the icons, dayjs, react and react-dom as peers (and
  as devDependencies for its own typecheck), so `apps/web` is the one
  manifest that installs them. The theme reaches the app's own antd
  components only through one antd module (ConfigProvider is a React
  context). The devDependency copy can still split it if the manifests
  drift; the app's antd components would then render antd's default blue,
  and the layer test expects the fixture's antd Button in the theme's
  #0071e3. Checked: one antd and one @ant-design/cssinjs in the store, the
  registry's included. The Dockerfile already copies every
  `packages/typescript` manifest.
- **Theme** (`@dw/ui` `theme.ts`): palette A from the S0 spec's token table,
  light and dark, including the status text colours (success, warning,
  error) and the dark error split into #d63a30 for fills and #ff6b61 for
  text. antd re-derives its seed tokens (primary, link, success, warning,
  error) in dark mode and keeps only map tokens as set: `#0071e3` rendered
  as `#0363c4`. A second algorithm after the dark one puts the seeds back.
  The menu's current item uses the link colour in the drawer and the "…"
  popup, where antd's primary on the selected background is 4.27:1 in light
  (the link colour 5.57:1). On the dark header the current and hovered item
  use it too: antd's primary there is 3.62:1. Be Vietnam Pro comes from
  next/font (latin and vietnamese, weights 400–700), followed by antd's own
  system stack.
- **CSS variables:** antd v6 always emits them, on `.<cssVar.key>`; names are
  `--ant-color-primary` and so on. The key is fixed as `css-var-dw` and the
  layout also puts it on `<html>`, so portals that are not antd's read them.
- **Provider** (`ThemeProvider`): ConfigProvider with `vi_VN`, antd `App`, and
  `dayjs.locale("vi")`. Light or dark follows `prefers-color-scheme`.
- **`globals.css`:**
    - the layer statement;
    - every Tailwind colour, the radius and `--font-sans` name an `--ant-*`
      variable;
    - breakpoints 576/768/992/1200/1600;
    - `color-scheme: light dark`;
    - cursor and reduced motion are in `@layer base`;
    - the focus outline is in `@layer components`, after antd's, so it also
      replaces antd's own ring (3px of `colorPrimaryBorder`: 1.79:1 on
      white, 1.27:1 on the dark card) on antd's controls;
    - the phone block stays unlayered, as before. In `@layer base` it needed
      `!important` everywhere, and there that outranks antd and every
      utility, `!` included;
    - the phone block now starts below 576px (it was 639px, Tailwind's old
      `sm`);
    - `success-text`, `warning-text` and `destructive-text` name antd's
      status text colours. The `@dw/ui` Badge and the destructive Alert use
      them: light success on its own tint was 2.96:1 and warning 3.27:1,
      against 5.50:1 and 4.89:1 now;
    - the surface, table, scrollbar and selection rules stay unlayered. Their
      colours are antd's, except the scrollbar greys, which antd has no token
      for;
    - the unused `--sidebar*` tokens are gone.
- **Shell:** `AppShell` in `@dw/ui`. `app-frame.tsx` keeps every gate state and
  feeds it the same nav filter. Nav badges still come from `useNavBadges`.
  `nav-links.tsx` was removed.
- **Menu keyboard:** antd hides an item that overflowed into "…" with
  opacity and `aria-hidden`, not `display` or `inert`, so its link kept a
  tab stop while invisible, and every visible link cost a second stop after
  the menu's own. The links now carry `tabIndex={-1}`: the menu is one tab
  stop with arrow keys inside it, Enter on an item calls `onNavigate`
  (`router.push`), and a click is still the link's, so a new tab works.
- **Dark mode would have hidden text,** so `bg-white` became a token in the
  `@dw/ui` Input/Textarea/Select, the bell, the workspace switcher and the
  modal bands. The switcher also shrinks now, so a 390px phone no longer
  scrolls sideways (it overflowed by 63px).
- **Layer test:** `e2e/antd-shell.spec.ts` against the dev-only fixture
  `/dev-login/layer-check`. 11/11 green on `next dev` (the first six were
  also green on `next start`). Each of these turns it red:
    - no layer statement: padding 15px instead of 40px;
    - no registry `layer`: 15px;
    - `antd` before `base`: background `rgba(0,0,0,0)`;
    - `--breakpoint-lg` 1024px, `--breakpoint-sm` 640px or
      `--breakpoint-2xl` 1536px (every breakpoint is compared with antd's
      token);
    - aria-current removed;
    - a dark mode that ignores the OS;
    - a misspelt `--ant-*` name;
    - no seed-pinning algorithm: the five dark seeds differ;
    - no dark menu colour: the current item is `rgb(0, 113, 227)`;
    - no menu `itemSelectedColor`: the light drawer's current item is
      `rgb(0, 113, 227)`;
    - the focus outline back in `@layer base`: antd's `rgb(122, 202, 255)`;
    - a fixture link without `tabIndex={-1}`: a tab stop inside
      `aria-hidden`;
    - the shell navigating on a click too, or not on Enter.

    The margin utility `ml-6` stays 24px with the order wrong, so it cannot
    catch the bug.

- **Gate test:** `components/__tests__/app-frame-gate.test.tsx` keeps the
  fixture behind the auth gate unless the build is dev-auth. It goes red when
  the dev check is dropped from the bypass.

**First-load JS** (`next build`, oidc):

| Route      | Before | After  |
| ---------- | ------ | ------ |
| /          | 152 kB | 301 kB |
| /approvals | 158 kB | 307 kB |
| shared     | 103 kB | 103 kB |

Every route grew by about 149 kB, because the shell (ConfigProvider, App,
Layout, Menu, Drawer, Grid) is on every page. That is inside the accepted
+250 kB. The numbers come from builds with `output: "standalone"` commented
out. On this Windows host the standalone step fails with a symlink EPERM,
before this change and after it. CI builds on Linux.

## Open

- `lib/money.ts`. (`lib/dates.ts` is in Asia/Ho_Chi_Minh with the time first
  and "giờ Việt Nam" since 2026-10-06, through `Intl`, not dayjs; supply-chain
  slice W.)
- Playwright projects for viewports (320px, both sides of 992px) and a
  non-Vietnam time zone.
- Replace sonner with `App.useApp()`; its toasts stay light in dark mode.
- `<html lang="vi">` since 2026-10-06 (slice W); the platform pages' copy is
  still English, so those read under the wrong language until translated.
- Replace shadcn page by page. In dark mode, the literal colours left in
  pages read with low contrast: `text-slate-*` and `text-red-600` on memory
  and integrations, the sky link in markdown answers, the country select, and
  the feedback asterisks. The unlayered table rules in `globals.css` went
  with slice W (2026-10-06): shadcn tables on phones now scroll instead of
  turning into cards.
- The platform pages now render in palette A at antd's 14px, and follow the
  OS into dark mode. The server renders light, so a dark OS sees light until
  hydration: on `next dev` the body read `rgb(245, 245, 247)` before it
  turned black; on `next start` hydration came before the first read. A
  static `@media (prefers-color-scheme: dark)` block of the global
  `--ant-*` values is not enough: antd emits component tokens as literal
  values per component (the SSR HTML has `--ant-layout-header-bg:#ffffff`
  and `--ant-menu-item-selected-bg:#e6f7ff`, no `var()`), so the header and
  menu would stay light. A fix needs those too, or a
  signal the server can read before paint (a cookie or client hint), which
  makes every route dynamic.
- Design v3 draws the menu's current item bold (600) in the main text
  colour. What ships is antd's weight with the link colour, except on the
  light header, where antd's primary (4.70:1) stays.
- `text-destructive` in pages on the dark card: #d63a30 is 3.65:1 (antd's
  derived #dc3e34 was 3.87:1, also failing). The Badge and the destructive
  Alert use the error text colour #ff6b61; pages keep `text-destructive`
  until each is replaced.
- Dark `colorLinkHover` is derived by antd as #2e547e, 2.18:1 on the card.
  For ticket 07's contrast test.
- antd's Checkbox and Radio draw focus on a sibling of the focused input,
  so they keep antd's 1.79:1 ring. The first form that uses them.
- `tabIndex={-1}` on the menu labels is a convention the app follows
  (`AppShellItem` says so). The e2e checks the fixture's labels; nothing
  checks `app-frame`'s.
- The menu collapses into "…" quickly: at 1280px an org admin sees three
  items. The "…" item's accessible name is antd's "ellipsis".
- The fixture answers HTTP 200 in an oidc build, because the gate's loading
  screen is what the server renders. Its payload is only the not-found
  fallback.
- next/font/google downloads at build time, so an offline build fails.
- `pnpm audit --audit-level high` passes since 2026-10-03: the brace-expansion
  advisories are fixed by raised override floors. braces GHSA-vfj7-8cjw-p6xm
  has no fixed release and is ignored in `pnpm-workspace.yaml`, which says when
  to drop the entry; it reaches only eslint-config-next's lint globs.
- With reduced motion, the theme does not yet set `motion: false`.
- CI does not run the web vitest suite or the Playwright specs, so every
  guard above runs only where someone runs it. Ticket 10, waiting on Đạt's
  decision (`PLAN.md`).
- The E-HSDT v3 theme (slice W, 2026-10-06, `supply-chain.md`): light primary
  #006edc (the handoff's #0071e3 is 4.31:1 on the page), field border #8c8c91,
  pill buttons, `StatusTag` tones per colour mode, JetBrains Mono as
  `fontFamilyCode`, `PageHeader` and `RegionState` in `@dw/ui`. Contrast pairs
  are measured in `components/__tests__/theme.test.tsx`, not yet on the
  rendered page; the e2e's `LIGHT_PRIMARY` follows the new primary and was not
  run.
