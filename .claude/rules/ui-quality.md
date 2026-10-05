---
paths:
    - "apps/web/**"
    - "packages/typescript/ui/**"
    - "docs/design/**"
    - "docs/products/**/ui-brief*"
    - "docs/products/**/ui-brief*/**"
---

# Screens that do their job — UI quality in this codebase

Nhận cùng nền tảng ngày 30/9/2026. Ngày 5/10/2026 mọi ví dụ của sản phẩm khác được
thay bằng ví dụ trung lập hoặc ví dụ chuỗi cung ứng (Elmich), ghi rõ là ví dụ, và mọi
quy tắc mượn khái niệm của sản phẩm đó (trang tài liệu, phát hiện, yêu cầu bắt buộc,
vai chỉ xem số tổng) được viết lại thành quy tắc trung lập. Bản này không trích ADR
nào của repo khác.

Not a style guide and not a second theme. This file is about how a screen
_behaves_: every state drawn, nothing the viewer may not see reaches the
browser, and a machine's guess never looks like a checked fact. Written, like
`code-quality.md` and `failure-modes.md`, as questions to ask **while writing
the component**. The product is operational: people open it to finish a task,
often against a deadline, so a screen that looks like its siblings is correct
and a screen that surprises is a defect unless the brief asked for it.

**Precedence.** `CLAUDE.md` wins. Colour, type, radius, breakpoints and motion
come from the antd theme in `@dw/ui` and nowhere else: a design handoff reaches
a screen through the theme, never as a value in a component, and the product
records that mapping once, in its decisions file. Words come from the context's
glossary, `CONTEXT.md`. The product's UI brief (the newest version, which
overrides an older handoff where it says so) wins on what one screen shows:
layout, copy, states, and which control is hidden or disabled. This file is the
default where they are silent. Where a design handoff is kept out of the
repository, take its facts from what the brief writes down as kept from it, not
from the drawing. Where two disagree, say which line you set aside and why.

---

## 1. One owner for every visual fact

Tokens are read from the theme (`theme.useToken()` or the `--ant-*` variables);
Tailwind does layout only (`CLAUDE.md` "Web UI"). Drafts that disagree on
palette or radius are settled once, in the theme, never per screen. The same concept uses the same
component and the same words on every screen: a second status badge inside a
context is `failure-modes.md` #2 drawn in pixels.

- **Breakpoints have one owner too.** antd switches at 576/768/992/1200 and a
  stock Tailwind `lg:` at 1024, so the navbar and a `lg:` layout would change
  32 px apart. Tailwind's `--breakpoint-*` map to antd's screen tokens, set
  once beside the colours.
- **Global CSS you add goes inside `@layer base`.** Unlayered CSS beats antd
  and every utility. The unlayered rules already in `globals.css` (the
  `:focus-visible` outline, the phone block, reduced motion) are revisited in
  the antd slice, not copied.
- **Feedback has one owner:** `App.useApp()` (`message`, `notification`,
  `modal`), which reads the theme and `vi_VN`. Not the static `message.*`, and
  not sonner's `toast`.

**Ask:** outside the theme, does the diff add a colour literal (`#…`, `rgba?(`,
`hsla?(`, `oklch(`), a Tailwind palette or type utility
(`(bg|text|border|ring)-(white|black|slate|gray|…)`, `text-(xs|sm|base|lg)`,
`font-(medium|semibold|bold)`), an unmapped radius (`rounded-[`,
`rounded-2xl`), a `font-family`, a duration or a `cubic-bezier`? Each hit is a
fact with two owners.

## 2. The first screenful answers the main question

Each screen answers its main question before anything else. Usually that is the
deadline that matters (giờ Việt Nam, and how long is left), whether the case
can move to its next step and what blocks it (counts by severity, UNKNOWN
included), and what is mine to do next; in supply chain, for example, which SLA
milestone of a PO case is due and whether the case can take its next step. Detail opens on demand; nothing a
decision needs sits behind hover or a collapsed panel.

**Ask:** with nothing clicked, can a person say whether this case can move on
and what they should do next?

## 3. Every control, every state

Eight states: default, hover, focus-visible, pressed, disabled, loading,
error, and success where the action has a result the person must see. antd
supplies them for its own controls, so **do not hand-build controls**: a row
that opens puts a `Link` in its identifying cell (an antd `onRow` click alone is
mouse-only), a chip is `Tag.CheckableTag` or `Segmented`, a card that opens is
wrapped in a `Link`. No `onClick` on a `div`, `tr` or `Card`.

- **Focus** is never removed, and returns to its trigger when a dialog closes
  (antd `Modal` does this by default; check it on the rendered page).
- **Loading** is `<Button loading={isPending}>`; antd already drops clicks while
  loading. Where a brief asks the width to hold, set a `minWidth`.
- **Idempotency.** A mutation carries an `Idempotency-Key` minted once per press,
  reused on a retry of the same payload and replaced after an edit: the API
  refuses a reused key with a new payload (`dependencies/idempotency.py`).
- **Success** shows after the server's 2xx, never optimistically, for anything
  that moves a gate, a verification or a price.
- **Hover is a bonus.** Touch has none; anything shown only on hover must also
  be reachable by tap and by keyboard.
- **Shortcuts:** a single-key shortcut works only while its panel has focus
  (WCAG 2.1.4); the label says Ctrl on Windows; every shortcut also has a
  visible control. Enter-to-submit and shortcuts ignore key events while
  `isComposing` is true, or Telex and Gboard submit half-typed words.

**Ask:** with the mouse unplugged, can I finish the task and always see where
focus is? On a slow network, how many records does a double-click create?

## 4. Every region, every state

A page, and each region that loads on its own: loading, empty, error,
forbidden, not found, offline, conflict, stale. One mapping from `errorCode()`
to a state lives in the shared state component, not per screen:

| Code                 | State                                                                                          |
| -------------------- | ---------------------------------------------------------------------------------------------- |
| `permission_denied`  | `Result status="403"` saying why and where to go                                               |
| `entitlement_denied` | the plan or read-only state, not 403 (entitlement and authorization are separate, `CLAUDE.md`) |
| `not_found`          | `Result status="404"`                                                                          |
| `conflict`           | "someone changed this": both values, what was typed kept, never a silent overwrite             |
| anything else        | `Result status="500"` with the server's sentence, `body.request_id`, and "Thử lại"             |

- **Loading** is `Skeleton`, shaped like the content; a `Table` uses its
  `loading` prop. A long job shows progress (done of total), the items it
  refused with the reason, and the items it processed with a warning.
- **Empty has three meanings.** _Nothing yet_ teaches and offers the first
  action (antd `Empty` with the action as its child). _Nothing matches_ names
  the filter and offers "Xóa bộ lọc". _Nothing so far_ on a partly loaded list
  says so, and that a search in the browser covers the loaded rows only. antd
  `Table`, `List` and `Select` draw a generic empty on their own: set
  `locale={{ emptyText }}` and pass `loading` until the first response, or the
  table says "no data" before the data arrives.
- **Error** text is the server's sentence through `errorMessage()`, never the
  machine code. A network failure is the offline state, not an error message.
- **Forbidden is not "not found".** Another tenant, or a workspace the viewer is
  not a member of, answers _not found_: a 403 would confirm the thing exists. A
  workspace they belong to offers to switch.
- **Offline** is detected once, in the `@dw/ui` shell, which draws the banner;
  a screen reads that state to disable its mutations. **Session expiry** warns a
  few minutes ahead with a way to stay signed in (WCAG 2.2.1) and never discards
  input.
- **Stale** says so when what is shown came from an older input ("Có phiếu mới
  hơn"; in supply chain, for example, a sign-off given before an SKU was added),
  instead of silently swapping it.

**Ask:** which of these states have I drawn and coded for this region, and
which will a user meet first on day one?

## 5. Disabled, hidden or locked — and the server decides anyway

- **Follow the screen's brief.** Where it is silent, an action the person could
  do under other conditions, or that their operational role lacks, is
  **disabled with the reason in words** (what blocks it, who can, what unlocks
  it: "Bạn đã tạo yêu cầu này nên không tự duyệt được (tách nhiệm)"). Hide only
  where the brief says: an area a role never reaches (for example a role the
  product limits to aggregates, outside its overview, whose URLs answer the
  forbidden state) and the support context, where verify, correct, approve and
  waive are absent.
- **Every disabled button has its reason in a `Tooltip`, always.** A disabled
  button cannot take focus and shows nothing on tap, so where the reason blocks
  deadline work (a gate, "Xác nhận"; in supply chain, for example, "ĐẶT HÀNG")
  the same sentence also sits beside it as visible text. Tooltips can be
  hovered, stay until dismissed and close with Esc (WCAG 1.4.13). Check mouse,
  keyboard and touch on the rendered page.
- **Support-grant mode** is a state of its own: an always-on banner
  (`role="status"`) naming the grant and its expiry, restricted fields masked,
  and an action after expiry refused with the reason.
- **Hiding a control is not authorization** (`CLAUDE.md`). The server half (the
  refusal and its negative test) is written in the API, where this file is not
  loaded: it belongs to `reviewing-feature-security` on that change.

**Ask:** if I delete this `disabled`, does the server still refuse, and which
test goes red?

## 6. What the viewer may not see never reaches the browser

Who sees which amount is the product's decision, recorded in its own ADRs; read
them before drawing any money. The traps: a role limited to aggregates never
sees a restricted value, not even summed; an internal price reaches only the
roles the product allows to see prices; public amounts are **not** locked,
because locking them teaches people the lock means nothing (for example, a list
price a supplier publishes to everyone). Restricted fields (CCCD, salary,
account numbers) are masked in the support context.

- **The API leaves the value out.** A number in the JSON is a leak even when
  nothing renders it; CSS that hides a value hides nothing.
- **Locked, not blank.** A cell shows the brief's lock icon and "Đã ẩn"; the
  sentence saying who can see it appears once, on the region. Never "0", never
  "—" (which reads as "no price"). One masked-value component in `@dw/ui` serves
  price, restricted data and support-grant exclusions.
- **Side doors, checked by name:** a check result's expected and actual values
  where they compare amounts; an approval's payload (`GET /approvals` returns it
  whole); the activity log; previews of uploaded files that carry prices (in
  supply chain, for example, a supplier quotation attached as a case document);
  previews and exports of generated documents;
  search and the command palette; toasts, notifications, email and Zalo cards;
  page title, URL and query string, export file name, `aria-label`; analytics,
  logs, traces, errors.
- **Derived leaks:** a sort by price, a bar's length, a share of total value, a
  severity that depends on the amount.
- **Exports and printouts** carry what the screen carries: verification state,
  unknown values, the version of the record, the time generated (giờ Việt Nam)
  and the same lock.

**Ask:** signed in as a role that may not see prices, does a known price appear
anywhere in the page's network responses or DOM?

## 7. Say how sure the machine is

A value the model read is not a value a person checked, and the product exists
so a person can tell the difference.

- **Every verification state** the glossary (`CONTEXT.md`) defines is drawn,
  not a subset. Expired, superseded and revoked never look like verified by a
  person (in supply chain, for example, a superseded BM04 profile drawn as the
  current one sends the supplier the wrong specification). Only a value a person verified or corrected reads as
  settled. A value typed by hand or through a support grant shows who entered
  it.
- **Unknown is neither pass nor fail.** Where a product rule makes a value
  mandatory and the value could not be decided, it blocks the step like a
  failure and counts among the blockers. Its own style: never the error red,
  never grey, never zero, never left out of a percentage, distinct from the
  "máy đọc" chip, and its colour means nothing else on the screen. The look is
  the theme's; the Vietnamese label is decided once, in the glossary. Something
  nothing checked shows as unchecked, never as passed.
- **A clean result says what it did not cover:** inputs not read, facts not
  extracted, rules not checked. Silence is never drawn as a pass.
- **Model confidence** (display-only) is secondary text at most: never a
  colour, a check or a sort key that makes a value look verified.
- **A value read from a document shows where it came from** (in supply chain,
  for example, the supplier update's `source_ref` quote beside the delay it
  extracted). Where the server requires the person to have seen the source
  before confirming, "Xác nhận" stays disabled until it has loaded for them.
- **Verification, correction and approval are never bulk actions:** no
  select-all, no "xác minh tất cả". A queue goes one item at a time, says how
  many remain, and moves focus to the next item after each decision.
- **AI-written text keeps its provenance tag** after it is read and approved;
  what clears is "chưa đọc". Numbers the code inserted are visibly locked.
- **Colour is never the only carrier:** every verdict, severity and status has
  text, and the screen reads in greyscale.

**Ask:** with the colour covered, can a person tell whether this value was read
by a machine or checked by a person, whether it is still valid, and where it
came from?

## 8. Numbers, money, time and Vietnamese text

- **Money** has one formatter, `lib/money.ts` beside `lib/dates.ts`, created with
  the first amount; `InputNumber`'s `formatter`/`parser` use it. `Intl`'s VND
  currency style prints "₫" while a brief may write "đ": the file decides once.
  Amounts are right-aligned in tabular figures. "tỷ" appears only where a brief
  abbreviates, with the full value reachable by hover, tap and keyboard.
- **Time** goes through `lib/dates.ts` only, which owns the zone, the label and
  the order. Every time is formatted in `Asia/Ho_Chi_Minh` and carries "giờ Việt
  Nam", on the value or once in a column header. A brief may write time first
  ("09:00 14/10/2026"): settle the order in the formatter, never per screen.
- **A date-only deadline is a calendar day, not a midnight.** It never goes
  through `new Date(iso)`, which shows one day early west of UTC, and a
  half-open "hết ngày" bound shows the last day included.
- **Time inputs:** a `DatePicker showTime` value is read as wall-clock
  `Asia/Ho_Chi_Minh` through `lib/dates.ts` (dayjs `utc` and `timezone`), never
  `value.toISOString()`, and the field carries "giờ Việt Nam".
- **Unknown is not none.** A deadline the machine could not read renders as
  UNKNOWN, never through `formatDateTime(null)`, whose "—" reads as "no
  deadline".
- **Relative time** ("còn 2 ngày 4 giờ") sits next to the absolute time, is
  computed against server time, keeps updating on a page left open overnight,
  and the page changes state when the deadline passes.
- **Vietnamese text:** real record names run long (in supply chain, for
  example, a product name carrying model, material, colour and size), so
  truncate in lists with the full text on hover and tap and whole on the
  detail; never truncate an identifier (a document number, a PO number, an item
  code or SKU) or an amount. No text below 12 px: stacked diacritics
  stop being legible.
- **Search and sort:** a filter finds "Hà Nội" from "ha noi" and "đ" from "d".
  A client sort uses `Intl.Collator('vi')`, or "Đ" lands after "Z". A list the
  server sorted is never re-sorted in the browser.
- **Words:** `lang` matches the language shown, and a screen is never half
  translated. Terms and role names come from `CONTEXT.md`, spelled exactly as
  it spells them, labels from one table keyed by code. Address the reader as
  "bạn", don't blame ("Số tiền phải là số nguyên", not "Bạn nhập sai"), and the
  machine never speaks as "tôi". Buttons name the outcome ("Gửi duyệt", not
  "OK").

**Ask:** is this screen still right on a laptop set to UTC+9, and with the
longest real record name in it?

## 9. Forms

The form conventions, in antd:
`<Form validateTrigger="onBlur" scrollToFirstError={{ focus: true }}>`, with
`onFinishFailed`'s `errorFields.length` feeding one `Alert type="error"` summary
("Còn 3 trường cần sửa"), which is what gets announced. antd links each field
error by `aria-describedby` itself; do not add `role="alert"` per field.

- Every field has a visible label; a placeholder is an example, never the label.
  `requiredMark` is set once on `ConfigProvider`.
- On blur, check what the person typed; "bắt buộc" appears on submit, not when
  someone tabs past an empty field (a per-rule `validateTrigger`).
- **Money inputs** group digits without moving the caret, accept a paste in
  either grouping ("18.450.000.000", "18,450,000,000", "18450000000 đ"), store an
  integer (`precision={0}`), and show the amount in words under the field where
  the document it goes into carries one, because a wrong amount in words gets
  that document rejected (in supply chain, for example, a deposit or payment
  document). Other decimals use the
  Vietnamese comma (12,5 m³); a pasted dot decimal is read correctly or refused,
  never scaled by 1 000.
- What the process already knows is filled in, not asked again (WCAG 3.3.7):
  the organisation's stored profile and the case's own facts.
- Autosave says "Đang lưu… / Đã lưu / Lưu thất bại — Thử lại" in a
  `role="status"` region; closing with unsaved changes asks first.
- A dialog keeps its actions in view (user rule 2026-08-28): antd `Modal`
  scrolls as one block, so a long body caps its height or moves into a `Drawer`,
  whose footer is pinned. On a phone the sheet is `Drawer placement="bottom"`.

**Ask:** type everything, drop the network, press save. What is on screen, and
what is lost?

## 10. Friction in proportion to the consequence

- **Routine and reversible:** no dialog. Undo sits in the toast, which pauses
  while hovered or focused and is reachable by keyboard; the same undo is in the
  activity log for as long as the server allows (WCAG 2.2.1). An error the
  person must act on never lives only in a toast that disappears.
- **Irreversible:** a confirmation that names what will be lost, by its name or
  identifier, never a bare "Bạn có chắc không?".
- **Destructive, high stakes:** type the identifier where the brief draws it
  (deleting a workspace, for example). A case
  is never deleted.
- **Approvals (a gate, a waiver):** no typing ritual, which proves typing, not
  review. Show exactly what is being approved (version, hash, time) with the
  brief's required checks, so the decision binds to what was seen (WCAG 3.3.4).
- Confirms come from `App.useApp().modal.confirm` with a danger OK and initial
  focus on Cancel, so Enter never deletes; a type-to-confirm keeps OK disabled
  until the text matches.

**Ask:** what is the undo, and if there is none, does the confirmation say what
will be lost?

## 11. Tables, lists and links

- antd `Table`: a stable `rowKey`; `pagination={false}` with a footer that
  always says where the list stands (the API pages by keyset, antd's pager
  assumes page numbers); `sticky` header; `scroll={{ x: 'max-content' }}`;
  amounts `align: 'right'`; row actions in a `fixed: 'right'` column; active
  filters as removable chips with "Xóa tất cả".
- **Real size:** test at the size the product's real documents and lists reach
  (in supply chain, for example, a tenant's whole PO case list and a PO carrying
  every SKU of a product line). A list that can
  pass about 200 rows is virtualised, and typing in a cell responds within
  100 ms.
- **Links:** every view a person would send to a colleague has its own URL (the
  workspace, the tab, the filters, the open item). A link from a notification or
  a Zalo or email card opens that item after sign-in, or says why it cannot.
  Back restores the filter and scroll. `<title>` names the record and the tab.
  The identifying cell is a real link that opens in a new tab; a control inside
  a row never also opens the row.
- **Charts** label their values, never rely on colour alone, have a table
  alternative, and hide small counts where the brief says.
- The change that adds the first antd `Table` deletes the table-to-card rules in
  `globals.css`'s phone block: they are unlayered and `!important`, so they beat
  antd and flatten `scroll.x`. The 16 px input rule in that block stays.

**Ask:** at phone width, can a person find the row and do the one thing they
came for, and can they send a colleague a link to it?

## 12. Layout, touch and accessibility

- **One shell,** the one `CLAUDE.md` decides; a brief drawn on another shell (a
  sidebar, say) is redrawn on this one. No sideways page scroll; a wide table
  scrolls inside its container.
- **The viewport list has one owner:** the projects in
  `apps/web/playwright.config.ts`. Add widths in the change that needs them (at
  least 320 px, which is WCAG reflow, and both sides of antd `lg`), plus one
  project with a non-Vietnam `timezoneId` so section 8 is tested, not trusted.
- **Touch targets** are at least 24×24 CSS px or spaced so a 24 px circle
  around each touches no other (WCAG 2.5.8); primary actions on touch use
  `size="large"`. A bigger minimum is a theme change (`controlHeightLG`), not a
  per-button one.
- **Contrast:** 4.5:1 for text; 3:1 for large text (24 px, or 18.7 px bold;
  nothing in a table counts), icons, field borders and focus rings (WCAG 1.4.3,
  1.4.11). Measure as rendered, text against the tint it sits on. Known traps:
  status colours on their own light tints, antd's light preset tags, and antd's
  secondary text, so a disabled reason or a locked-price sentence does not use
  `type="secondary"` until the theme lifts it. Where a handoff colour fails,
  contrast wins.
- **Names and announcements:** every control has an accessible name in the
  screen's language. Toasts through `App.useApp()` are announced; an in-place
  status gets `role="status"`. A sticky bar never covers the focused element
  (`scroll-padding` set to the bars' height; WCAG 2.4.11).
- **Motion** is what antd ships, timed by the theme's `motion*` tokens: a
  component writes no duration or easing. Animations a handoff brings (a
  staggered page entry, a long flash, an overshoot curve, Framer Motion) are a
  decision the product records once, with the theme, not built per screen.
  Never delay input, never animate a value a decision depends on (no counting up
  a price or a KPI). With reduced motion, the theme sets `motion: false` beside
  the `globals.css` rule, and nothing the motion carried is lost.

**Ask:** can a Playwright test reach every control with
`getByRole(role, { name })` in Vietnamese, and does the page hold at 320 px?

---

## How it is checked, and what the check cannot see

Runs today: `/code-review`'s Standards axis reading `.claude/rules/`, and
`apps/web/e2e` with one Desktop Chrome project. Owed, and to be tracked in this
repo's `.claude/plans/web-ui.md` (its "Open" list already names the first two):
the layer-order test, web tests in CI, and the viewport and time-zone projects
of section 12. Until those run, every guard this file asks for ships with its
own test that goes red without it (`failure-modes.md` #3): no price in the DOM
or the network for a role that may not see it, a disabled action's endpoint
refused when called directly.

What no review of a diff can see (`failure-modes.md` #0): whether the rendered
page reads well, whether real colour pairs pass, whether a flow makes sense to
a person under a deadline. Those are found by running the page and looking at
it. A clean review of the code is not a reviewed screen.

**The test:** put the screen beside its sibling. Same header, same status tags,
actions in the same place, same words? Anything different needs a line in the
brief. No bespoke look, no decorative motion, no wrapper around an antd
primitive, no state no user can reach.
