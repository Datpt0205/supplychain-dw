# 01 — PromptRegistry tự giam mọi biến, template chỉ mở raw có lý do

Status: resolved (2026-10-08, nhánh `feat/security-debts`)
Blocked by: —
Area: platform-runtime

## Mục tiêu

Hôm nay việc giam dữ liệu không tin cậy là việc của từng template: `untrusted_demo@1.0.0`
tự viết `<input>\n{input}\n</input>`, còn `PromptRegistry.render` chỉ `str.format`. Một
template quên block, hoặc một tài liệu chứa `</input>`, là đủ để phần sau của giá trị đọc
như lời của chính prompt. Registry phải tự giam để không prompt nào quên được.

## Việc đã làm

- `PromptArtifact.raw_variables: dict[str, str]` (tên → lý do). Mọi biến khác được
  `contain_untrusted` bọc thành `<input name="...">` với `&`, `<`, `>` đã escape. Validator:
  raw phải nằm trong `variables`, lý do không rỗng, template không được tự viết `<input`.
- `untrusted_demo@1.0.0` → `@1.1.0` (template bỏ block tự viết). Fixture eval
  `pf_prompt_injection` nay mang `</input>` trước chỉ thị độc; dataset `platform@1.1.0` →
  `@1.2.0` vì fixture đổi. Grader `runtime.prompt_injection` tìm `<input` (block có thuộc
  tính).
- `test_prompt_containment.py`: render mọi prompt ship trong `configs/prompts` với giá trị
  độc, khẳng định số block mở/đóng bằng số biến không tin cậy và không có `<`/`>` trong
  thân block.

## Quyết định tạm (Đạt giao)

- **Escape, không xoá:** model vẫn đọc được nội dung tài liệu (`&lt;` thay `<`).
- **Lint "raw được nuôi từ dữ liệu request":** không làm được bằng kiểm tĩnh — giá trị nào
  vào biến nào là quyết định của caller (`ModelRequest.variables`, `prompt_variables` của
  host), YAML không thấy được. Thay bằng hai rào: raw phải ghi lý do trong artifact, và
  `test_no_shipped_prompt_takes_a_raw_variable` đỏ khi prompt của platform khai một biến
  raw, nên thêm raw là một lần sửa test có người duyệt.
- **Ngoài phạm vi registry:** prompt tóm tắt của compaction (`configs/copy/runtime@*.yaml`,
  `context_compaction._render`) không đi qua registry; nó đưa message vào qua
  `get_buffer_string(format="xml")`. Ghi lại, chưa sửa.

## Tiêu chí chấp nhận

- [x] Giá trị chứa `</input>` không đóng được block (unit + eval).
- [x] Biến raw in nguyên văn; biến không khai raw luôn bị bọc.
- [x] Mutation: bỏ escape → 3 test đỏ + eval đỏ; bỏ bọc → 3 đỏ + eval đỏ; bỏ chặn template
      tự bọc → 1 đỏ.

## Nguồn

- `packages/python/dw_agent_runtime/src/dw_agent_runtime/model/prompts.py`
- `docs/adr/0010-prompt-variables-are-untrusted-by-default.md`
