# 01 — `run_dataset` nhận bảng grader; seam đăng ký ở `run_evals.py`

Status: resolved
Blocked by: —
Area: platform-runtime

## Mục tiêu

Một bounded context đăng ký grader của mình mà không sửa `dw_evals` và không để `dw_evals`
import nó. Bảng grader có một chủ: hàm dựng bảng ở `scripts/run_evals.py`; script và test
chạy mọi dataset cùng đọc hàm đó (spec, Hiện trạng).

## Việc cần làm

1. `dw_evals/runner.py`: `run_dataset(dataset, repo_root, graders: Mapping[str, Grader])`;
   `_grade` tra trong `graders`, không import `GRADERS` nữa (bỏ import ở `runner.py:16`).
   Không có giá trị mặc định: một lời gọi quên bảng là lỗi kiểu, không phải bảng rỗng chạy
   lặng lẽ (failure-modes #7).
2. `scripts/run_evals.py`: hàm `grader_table() -> dict[str, Grader]` bắt đầu từ `GRADERS` của
   nền tảng, rồi gộp bảng của từng context ở một chỗ đánh dấu
   `# --- BOUNDED CONTEXT GRADERS REGISTER HERE ---`, cùng khuôn với seam ở `wiring.py` và
   `dw_worker/main.py`. Gộp qua một hàm `merge_graders(*tables)` ném lỗi khi hai bảng trùng
   tên, kèm tên trùng; script dừng với mã khác 0. `main()` gọi `run_dataset(dataset,
REPO_ROOT, grader_table())`.
3. Test chạy mọi dataset (`test_eval_runner.py:28-33`) phải dùng đúng bảng của script, không
   bảng nền tảng: chuyển nó sang `apps/api/tests/unit/` (package đã phụ thuộc mọi context và
   đã nạp script bằng `importlib.util.spec_from_file_location`, như
   `test_release_manifest.py`), gọi `grader_table()`. Test còn lại trong `dw_evals` truyền
   `GRADERS` tường minh.
4. `scripts/new_context.py`: thêm seam này vào danh sách chỗ nó vá (dòng import và lời gọi
   đăng ký, viết dạng comment như lane worker nếu context mới chưa có grader), cập nhật
   `CLAUDE.md` "Adding a bounded context" và job `scaffold-smoke` cho khớp. Không sinh
   grader hay dataset (docstring dòng 48-52 giữ nguyên lý do).

## Tiêu chí chấp nhận

- [ ] Unit: `run_dataset` với một bảng chỉ có grader giả `x.ok` chấm ca dùng `x.ok` là đạt,
      và ca dùng `runtime.prompt_injection` là "unknown grader" (bảng truyền vào là bảng
      duy nhất được dùng). Đổi `_grade` về đọc `GRADERS` thì test đỏ.
- [ ] Unit: `merge_graders` với hai bảng cùng có một tên ném lỗi nêu tên đó; gỡ kiểm trùng
      thì test đỏ.
- [ ] `uv run python scripts/run_evals.py --smoke` vẫn xanh với `platform@1.0.0.json`, báo
      "security coverage ok".
- [ ] Test chạy mọi dataset ở `apps/api/tests/unit/` xanh, và đỏ khi một dataset dùng tên
      grader không có trong `grader_table()`.
- [ ] `lint-imports` xanh; `rg GRADERS packages/python/dw_evals/src` chỉ ra `graders.py`.
- [ ] `make ci` xanh; job `scaffold-smoke` xanh với seam mới.

## Nguồn

- `packages/python/dw_evals/src/dw_evals/runner.py:16, 61, 72`;
  `packages/python/dw_evals/src/dw_evals/graders.py:57` (`Grader`), `:215-220` (`GRADERS`).
- `scripts/run_evals.py:36` (glob `evals/datasets/*.json`), `:46` (`run_dataset`).
- `packages/python/dw_evals/tests/unit/test_eval_runner.py:16, 28-33`;
  `apps/api/tests/unit/test_release_manifest.py:17` (nạp script).
- `scripts/new_context.py:48-52`; `CLAUDE.md` "Adding a bounded context" bước 5.
- `.claude/rules/failure-modes.md` #2 (một bảng, một chủ), #7.

## Comments

- **2026-10-07, làm ở repo Elmich (`supplychain-dw`, lát S7), chưa đưa lên platform.**
  Như mô tả, trừ: `merge_graders` ở `dw_evals.graders` (nền tảng, test được trong
  package), `grader_table()` ở script gọi nó; grader của context ở
  `dw_<name>.testing.eval_graders` (miễn kiểm phụ thuộc như `dw_platform.testing`, image
  không đổi); contract "Platform packages do not import a bounded context" thêm
  `dw_evals` vào `source_modules`, và `new_context.py` thêm `dw_evals` khi sinh contract
  mới. Test đỏ khi gỡ: `_grade` đọc `GRADERS` (test bảng truyền vào), bỏ kiểm trùng
  (`merge_graders`), bỏ đăng ký context (test ở `apps/api`), `import dw_supply_chain` ở
  `dw_evals` (`lint-imports`). `CLAUDE.md` bước 5 sửa (câu hỏi mở 1 của spec). Chi tiết:
  `.claude/plans/supply-chain/stage-1/issues/07-stage-1-evals.md`, Comments S7.
