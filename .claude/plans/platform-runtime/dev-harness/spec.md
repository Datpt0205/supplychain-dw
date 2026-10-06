# Harness của coding agent: cổng commit không bị vòng qua, Stop hook đếm đúng việc của phiên

Status: resolved
Area: platform-runtime · Nhánh: `feat/platform-hardening` · Viết: 6/10/2026

Lát nền tảng, trung tính với sản phẩm: các sản phẩm nhận nó qua `git merge`. Nguồn: audit
harness 6/10/2026 (khu `dev-harness`, `file-memory-and-instructions`, `reuse`), kiểm lại
trên `main` (`4cb45dc`) của repo này.

## Hiện trạng (đã kiểm trong repo và máy này)

- **Cổng commit chỉ nghe tool Bash.** `.claude/settings.json` đăng ký
  `pre-commit-gate.sh` với `"matcher": "Bash"`. Trên Windows, `git commit` chạy qua tool
  PowerShell không qua cổng. Hook đọc `.tool_input.command`, trường mà tool PowerShell
  cũng có.
- **Cổng chỉ khớp chuỗi `git commit` liền nhau.** `pre-commit-gate.sh:28-31`
  (`case … *"git commit"*`), nên `git -C <dir> commit` và `git -c k=v commit` lọt.
- **Tệp tắt cổng không bị gitignore.** `pre-commit-gate.sh:18` (`.claude/no-commit-gate`)
  và `session-stop.sh:16` (`.claude/no-stop-gate`); `.gitignore:63-67` chỉ có
  `worktrees/`, `.commit-gate-seen`, `.session-head`. Một `touch` lỡ commit tắt cổng cho
  mọi bản clone.
- **Stop hook đếm mọi tệp bẩn trong cây** (`session-stop.sh:32`,
  `git status --porcelain | wc -l`), kể cả việc của phiên khác hay workflow song song; nó
  đẩy agent commit việc của người khác.
- **Mốc phiên dùng chung và bị ghi đè.** `session-start.sh:83` ghi
  `.claude/.session-head` mỗi lần SessionStart (cả `resume`, `clear`, `compact`), một tệp
  cho cả checkout, không đọc stdin (nơi có `session_id`, `source`).
- **Plugin `mattpocock-skills`:** `enabledPlugins` bật trong `.claude/settings.json`, và
  `~/.claude/plugins/installed_plugins.json` có bản cài `scope: project` với
  `projectPath: C:\Users\phung\codebase` — tức là CÓ cài cho checkout này. Audit nói "không
  hoạt động" là đúng ở dw-elmichs, sai ở đây. Điều sai ở cả hai nơi: `CLAUDE.md` ("Agent
  skills"), `PLAN.md` (lớp 5) và `docs/agents/*` không nói rằng cài plugin là việc của
  từng checkout, nên một sản phẩm merge từ nền tảng đọc như có công cụ mà nó chưa cài.
- **`ui-quality.md` không có ở nền tảng.** Có ba bản sửa tay khác nhau ở các sản phẩm;
  bản đầy đủ nhất là `C:/Users/phung/dw-elmichs/.claude/rules/ui-quality.md` (420 dòng,
  đã có `paths:`), nhưng mang lời và ví dụ của sản phẩm.
- **`code-quality.md` (146 dòng) và `failure-modes.md` (150 dòng) không có `paths:`**, nên
  nạp mọi phiên. Nội dung của cả hai áp cho mọi thay đổi (`CLAUDE.md` "Work style" mục 3
  bảo mọi thay đổi chạy chúng), nên nạp mọi phiên là đúng; điều thiếu là chúng không nói
  vậy. `session-start.sh:96` còn viết "the seven shapes of bug" trong khi
  `failure-modes.md` có tám (#0–#7).

## Mục tiêu

1. Cổng commit chạy cho commit qua Bash và PowerShell, và cho `git -C <dir> commit`,
   `git -c k=v commit` (cùng các dạng có nhiều tùy chọn toàn cục), có script test chứng
   minh từng dạng bị chặn khi invariant hỏng và không chặn lệnh không phải commit.
2. Hai tệp tắt cổng bị gitignore và được ghi là chỉ con người dùng.
3. Stop hook chỉ tính tệp mà phiên này đổi (ít nhất tách tệp đổi từ đầu phiên khỏi phần
   bẩn có sẵn); mốc theo từng `session_id`, không dùng chung, không bị `resume`/`compact`
   ghi đè.
4. `CLAUDE.md`, `PLAN.md`, `docs/agents` nói đúng trạng thái plugin: cài theo từng
   checkout, và cách kiểm.
5. `.claude/rules/ui-quality.md` có ở nền tảng, trung tính sản phẩm, `paths:` cho
   `apps/web/**` (và `packages/typescript/ui/**`).
6. `code-quality.md` và `failure-modes.md` ghi rõ phạm vi của mình; không rút gọn
   `CLAUDE.md`.

## Quy tắc và quyết định

- **Không rút gọn `CLAUDE.md`:** nó là kiến trúc ghi nhận; tách nó ra cần một yêu cầu rõ
  ràng (audit đề xuất, chưa ai yêu cầu). Ghi đề xuất vào "Câu hỏi còn mở".
- **Không gắn `paths:` cho `code-quality.md`, `failure-modes.md`:** nội dung của chúng
  không theo đường dẫn. Thêm một câu ở đầu mỗi tệp: nạp mọi phiên có chủ ý, và vì sao.
- **Tắt cổng là hành động của người:** gitignore hai tệp; thêm `permissions.deny` cho
  Write/Edit trên hai đường dẫn đó trong `.claude/settings.json` nếu cú pháp deny theo
  đường dẫn chạy được (đo, `failure-modes.md` #4); không được thì ghi lại và chỉ gitignore.
- **Mốc phiên có hạn:** tệp `.claude/.session-head.<session_id>` bị xóa ở nhánh thành công
  của Stop hook hoặc khi cũ quá N ngày ở SessionStart (`failure-modes.md` #6).

## Trong phạm vi

- `.claude/settings.json`, `.claude/hooks/*.sh`, `.gitignore`, một script test mới (ví dụ
  `scripts/test_claude_hooks.sh` hoặc test pytest gọi hook bằng JSON giả), Makefile nếu
  thêm target.
- `CLAUDE.md` mục "Agent skills" (chỉ câu về cài plugin), `.claude/PLAN.md` lớp 5,
  `docs/agents/issue-tracker.md`.
- `.claude/rules/ui-quality.md` (mới), `.claude/rules/code-quality.md`,
  `.claude/rules/failure-modes.md` (chỉ phần đầu), `session-start.sh` (bỏ con số).

## Ngoài phạm vi

- Đưa `verify_invariants.py`/`check_hygiene.py` vào `.pre-commit-config.yaml` cho commit của
  người, và test chứng minh `verify_invariants.py` đỏ được: audit nêu, đáng làm, ticket
  riêng nếu Đạt muốn.
- Cài plugin ở các repo sản phẩm, và chuyển bài học cá nhân sang `~/.claude/rules/`: việc
  trên máy, không phải mã nền tảng.

## Quy tắc và kiểm soát

| Chốt                              | Test đỏ khi gỡ                                                                                              |
| --------------------------------- | ----------------------------------------------------------------------------------------------------------- |
| PowerShell qua cổng               | Matcher về `"Bash"` → script test (kiểm `settings.json`, chạy hook với JSON của tool PowerShell) đỏ         |
| `git -C`/`git -c` qua cổng        | Khôi phục `case *"git commit"*` → ca `git -C . commit`, `git -c user.name=x commit` không bị chặn, đỏ       |
| Không chặn nhầm                   | Regex quá rộng → ca `git commit-tree`, `echo "git commit"` trong chuỗi, `git log --grep commit` bị chặn, đỏ |
| Tệp tắt cổng không vào git        | Gỡ dòng gitignore → `git check-ignore .claude/no-commit-gate` trong script đỏ                               |
| Stop hook chỉ tính việc của phiên | Quay về đếm `git status` toàn cây → ca "bẩn có sẵn từ trước phiên, phiên không đổi gì" bị nhắc, đỏ          |
| Mốc theo phiên, không bị ghi đè   | SessionStart `source=compact` ghi đè mốc → ca "commit trước compact vẫn được thấy" đỏ                       |

## Tiêu chí xong của slice

- Ticket 01 `Status: resolved`, chứng minh đỏ dưới `## Comments`.
- Script test của hook chạy trong `make ci` (hoặc một target CI gọi) và xanh trên Windows
  Git Bash lẫn Linux runner.
- Dòng của lát trong `.claude/plans/platform-runtime.md`.

## Phụ thuộc

- Không chờ lát nào.

## Câu hỏi còn mở

1. Tách các mục chỉ áp cho một phần cây ra khỏi `CLAUDE.md` (Web UI sang `ui-quality.md`,
   Data model rules sang rule theo `db/migrations/**`, `**/adapters/persistence/**`) để về
   gần mốc 200 dòng? Cần Đạt quyết, vì `CLAUDE.md` là kiến trúc ghi nhận.

## Danh sách ticket

| #   | Ticket                                                                                                                  | Status   | Blocked by |
| --- | ----------------------------------------------------------------------------------------------------------------------- | -------- | ---------- |
| 01  | [Cổng commit cho mọi dạng commit, Stop hook theo phiên, plugin và rules nói đúng](issues/01-harness-gates-and-rules.md) | resolved | —          |
