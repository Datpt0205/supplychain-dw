# 01 — Cổng commit cho mọi dạng commit, Stop hook theo phiên, plugin và rules nói đúng

Status: resolved
Blocked by: —
Area: platform-runtime

## Mục tiêu

Các cổng của harness gác đúng cái chúng nói là gác, trên Windows lẫn Linux, và tài liệu
của harness nói đúng điều đang chạy. Spec, mục "Quy tắc và quyết định" và "Quy tắc và kiểm
soát".

## Việc cần làm

1. **Cổng commit**
    - `.claude/settings.json`: matcher `"Bash|PowerShell"` cho `pre-commit-gate.sh`.
    - `pre-commit-gate.sh`: thay `case` bằng một regex khớp `git`, rồi không hay nhiều tùy
      chọn toàn cục (`-C <dir>`, `-c <k=v>`, `--git-dir=…`, `--work-tree=…`,
      `--no-pager`…), rồi `commit` là một từ riêng; khớp cả khi đứng sau `&&`, `;`, `|`.
      Đo trước những dạng tool PowerShell thật gửi (ví dụ `git commit -m @'…'@`), không
      đoán (`failure-modes.md` #4).
    - Script test (Git Bash và Linux): nạp JSON giả cho từng dạng — Bash và PowerShell;
      `git commit`, `git -C . commit`, `git -c user.name=x commit`,
      `cd x && git commit`, `git --no-pager commit` — với một invariant giả hỏng (biến môi
      trường trỏ `verify_invariants` sang một lệnh trả 1, hoặc cách tương đương không sửa
      tệp thật) → exit 2; ca âm `git commit-tree`, `git log --grep commit`,
      `echo "git commit"` → exit 0. Script cũng kiểm `settings.json` có matcher đúng.
2. **Tệp tắt cổng**: `.gitignore` thêm `.claude/no-commit-gate`, `.claude/no-stop-gate`,
   `.claude/.session-head.*`; comment đầu hai hook ghi "chỉ con người tạo tệp này".
   `permissions.deny` theo spec, nếu đo thấy chạy.
3. **Stop hook theo phiên**
    - `session-start.sh`: đọc stdin bằng `jq` (có fallback như `session-stop.sh`), lấy
      `session_id`; ghi `.claude/.session-head.<session_id>` (HEAD + ảnh chụp
      `git status --porcelain`) chỉ khi tệp chưa có. Xóa tệp mốc cũ quá 7 ngày.
    - `session-stop.sh`: đọc cùng `session_id`; chỉ tính đường dẫn mới hoặc đổi so với ảnh
      chụp; phần bẩn có sẵn được nêu riêng, không bắt commit. So commit với HEAD trong
      mốc. Không có mốc → hành xử như hôm nay nhưng nói rõ là không biết mốc.
    - Thêm ca vào script test: bẩn có sẵn + phiên không đổi gì → exit 0; phiên đổi một tệp
      → nhắc đúng tệp đó; SessionStart lần hai với `source=compact` không đổi mốc.
    - `session-start.sh:96`: bỏ con số ("the bug shapes this repository has produced"):
      `failure-modes.md` là chủ của số đếm.
4. **Plugin**: `CLAUDE.md` "Agent skills" thêm một câu: plugin cài theo từng checkout
   (`claude plugin install mattpocock-skills@claude-plugins-official --scope project`),
   kiểm bằng `claude plugin list`; chưa cài thì các lệnh `/ask-matt`, `/implement` không
   có. Cùng ý, ngắn hơn, ở `.claude/PLAN.md` lớp 5 và `docs/agents/issue-tracker.md`.
   Không đổi gì khác trong `CLAUDE.md`.
5. **`ui-quality.md`**: chép từ `C:/Users/phung/dw-elmichs/.claude/rules/ui-quality.md`;
   so với bản ở `C:/Users/phung/dw-proterial/.claude/rules/ui-quality.md` để lấy phần
   trung tính mà bản kia có hơn; gỡ tên sản phẩm, lời và ví dụ riêng của sản phẩm (thay
   bằng ví dụ trung tính hoặc bỏ); frontmatter `paths:` gồm `apps/web/**` và
   `packages/typescript/ui/**`. Không mâu thuẫn mục "Web UI" của `CLAUDE.md`; chỗ trùng
   thì trỏ về `CLAUDE.md`, không chép lần hai.
6. **Phạm vi rules**: đầu `code-quality.md` và `failure-modes.md` một câu: nạp mọi phiên có
   chủ ý vì áp cho mọi thay đổi (`CLAUDE.md` "Work style" mục 3). Không `paths:`.

## Tiêu chí chấp nhận

- Script test xanh; mỗi chốt trong bảng của spec có một lần gỡ làm nó đỏ, ghi lệnh và kết
  quả dưới `## Comments`.
- Một commit thật qua tool PowerShell trong phiên này bị cổng chặn khi invariant hỏng (thử
  trên nhánh nháp, rồi bỏ), ghi lại.
- `rg -i "elmich|proterial|bidding|e-hsdt|supply.chain" .claude/rules/ui-quality.md` không ra
  gì.
- `wc -l CLAUDE.md` không giảm ngoài câu thêm ở bước 4; `.claude/PLAN.md` dưới 80 dòng.
- `make ci` xanh.

## Nguồn

- Audit harness 6/10/2026, khu `dev-harness`: gap cổng vòng được (PowerShell, `git -C`),
  gap plugin, gap mốc Stop hook, gap Stop hook đếm cả cây, gap tệp tắt cổng, gap con số
  "seven", gap `ui-quality.md`, gap kích thước ngữ cảnh luôn nạp (real=true, kèm cảnh báo
  rằng hai rules không theo đường dẫn là có chủ ý).
- `.claude/settings.json`, `.claude/hooks/pre-commit-gate.sh:13-31`,
  `.claude/hooks/session-stop.sh:10-60`, `.claude/hooks/session-start.sh:75-100`,
  `.gitignore:60-67`, `~/.claude/plugins/installed_plugins.json`, `CLAUDE.md` "Agent
  skills", `.claude/PLAN.md` "How a feature is checked here".
- `failure-modes.md` #1, #3, #4, #5, #6.

## Comments

**6/10/2026, agent, đã làm.**

- **Đỏ trước:** `bash scripts/test_claude_hooks.sh` trên hook cũ: 33 qua, 35 trượt
  (`-C`/`-c`/`--no-pager`/`git.exe` lọt; `commit-tree` và `echo "git commit"` bị chặn
  nhầm; matcher chỉ `Bash`; hai tệp tắt cổng chưa gitignore; mốc dùng chung). Sau khi sửa:
  68/68 xanh trên Git Bash (máy này không có jq, nên chạy nhánh dự phòng sed/awk) và 68/68
  trên Alpine 3.20 có jq 1.7.1 (`docker run … alpine:3.20`, bash, git và jq cài trong
  container). Runner Linux của GitHub chưa chạy vì nhánh chưa push.
- **Đã đo, không đoán:** một phiên lồng nhau (`claude -p`, `--allowedTools PowerShell`)
  chạy trong worktree nháp `scratch/gate-probe`, có hook mới, PATH có một `uv` giả luôn trả
  1, và thêm một hook ghi lại payload. Payload thật:
  `"tool_name":"PowerShell","tool_input":{"command":"git -C . commit --allow-empty -m probe","description":…}`,
  một dòng JSON gọn. Cổng chặn commit; HEAD giữ nguyên `f7ba042`. Stop hook của phiên đó
  cũng chạy thật: mốc `.claude/.session-head.<uuid>` được ghi, và nó chỉ nêu
  `probe-input.json`, tệp duy nhất xuất hiện sau khi phiên bắt đầu. Worktree và nhánh nháp
  đã xóa. Không làm được trong chính phiên này: phiên này nạp hook của dw-elmichs (matcher
  `Bash`), không phải của repo này.
- **Mutation, mỗi chốt một lần** (sao lưu, sửa, chạy, khôi phục, `cmp` khớp từng byte):
  matcher về `Bash` → 1 đỏ; khôi phục `case *"git commit"*` → 18; regex quá rộng
  (`git.*commit`) → 8; bỏ vòng tùy chọn toàn cục → 14; bỏ gitignore hai tệp tắt cổng → 2;
  bỏ gitignore mốc → 1; Stop đếm cả cây → 6; SessionStart ghi đè mốc → 2 (ca compact);
  mốc theo trạng thái thay vì nội dung → 2; mốc dùng chung → 6; bỏ lọc ký tự `session_id` →
  1; bỏ hạn 7 ngày → 1; Stop không `touch` mốc → 1; không mốc thì im → 2; bỏ đường
  fail-closed khi payload không đọc được → 1. Hai chốt (lọc id, `touch`) lúc đầu sống sót
  mutation; test đã được viết lại cho tới khi chúng đỏ.
- **Lệch so với ticket, có lý do:**
    - Bỏ ca "phiên sau không nhận commit của phiên trước": commit không mang session id, nên
      Stop hook không phân biệt được. Giới hạn này được ghi trong `session-stop.sh`. Tệp
      chưa commit thì phân biệt được theo nội dung.
    - Không thêm `permissions.deny` cho hai tệp tắt cổng: không đo được trong phiên này, và
      một `touch` qua shell vẫn vượt qua nó, nên nó sẽ chỉ là trang trí (#1). Thay vào đó:
      gitignore, và chú thích đầu hai hook ghi "chỉ con người tạo".
    - Mốc không bị xóa ở nhánh thành công của Stop (spec có đề xuất): Stop chạy mỗi lượt,
      xóa thì lượt sau mất mốc. Thay vào đó, Stop `touch` mốc, và SessionStart xóa mốc
      không được chạm quá 7 ngày.
    - `code-quality.md` trỏ "Work style" mục 9, mục nói về nó; `failure-modes.md` trỏ mục 3.
- **Thêm, do review bảo mật tìm ra:** payload mà cả jq lẫn dự phòng đều không đọc được
  lệnh từ đó thì cổng so khớp trên cả payload thô (fail closed, #7), có test và mutation.
- **Kiểm khác:** `rg -i "elmich|proterial|bidding|e-hsdt|supply.chain" .claude/rules/ui-quality.md`
  → không ra gì (exit 1). Bản proterial không có gì trung tính mà bản elmichs thiếu (nó
  trích ADR 0008–0010 mà repo này không có). `CLAUDE.md` 344 → 352 dòng (chỉ câu plugin),
  `.claude/PLAN.md` 79 dòng. `claude plugin list` đã đo: "✔ enabled" ở repo này,
  "✘ failed to load" ở dw-elmichs (bật trong settings nhưng không cài).
- **`make ci` xanh:** ruff "All checks passed", mypy "no issues in 374 source files",
  unit 960 qua / 3 bỏ qua, architecture + invariants ok, contract 2 qua,
  `test-hooks` 68/68, eval-smoke `platform_smoke@1.1.0` 4/4, release-manifest-check OK.
  Không gói Python nào bị đổi nên không chạy integration và không bật infra.
- **Còn mở:** bước CI mới (`ci.yml`, "Coding-agent harness hooks") chưa được xem chạy
  thật trên GitHub. Alias (`git ci`) và `Start-Process git` không bị cổng thấy; đây là
  lời nhắc lúc commit, không phải sandbox.

**6/10/2026, kiểm độc lập.** Hai dạng vẫn lọt cổng, đo trên máy này (Git Bash và
PowerShell đều chạy cả hai): `Git commit` / `GIT.EXE commit` (Windows không phân biệt hoa
thường) và `git \` hay ``git -C . ` `` xuống dòng rồi `commit` (grep đọc từng dòng). Sửa: so
khớp `-i`, và nối dòng kết thúc bằng `\` hoặc backtick trước khi so. Thêm 4 ca (76/76 xanh);
mutation: bỏ `-i` → 4 đỏ, bỏ bước nối dòng → 4 đỏ. Mutation lại ba chốt của slice (vòng
tùy chọn toàn cục → 14 đỏ, đường fail-closed → 1, lọc theo mốc trong Stop → 6, mốc ghi một
lần → 2): khớp số đã ghi.
