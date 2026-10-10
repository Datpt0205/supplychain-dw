# 04 — Skill registry (nền tảng)

Status: resolved
Blocked by: —
Area: supply-chain

## Mục tiêu

Kiến thức quy trình (17 bước, chứng từ từng bước, hướng dẫn BM04, luật nhãn, AQL, hồ sơ hải quan, giọng thư NCC) là artifact có phiên bản, tenant ghi đè được, đưa vào prompt như phần khai báo.

## Việc cần làm

1. **Registry** trong `dw_agent_runtime` theo mẫu prompt/tool spec:
   `configs/skills/<domain>/<id>@<semver>.yaml` (`title`, `body`, `applies_to`), `TenantOverlay`,
   ghim trong release manifest, kiểm khi nạp (id, semver, `applies_to` là prompt có thật).
2. **Prompt** khai `skills: [id@range]`; registry chèn phần đó vào prompt (văn bản tin cậy,
   review như prompt), không qua biến `<input>`.
3. **Skill đầu:** `supply_chain.process_part_a`, `supply_chain.step_documents`,
   `supply_chain.bm04_guide`, `supply_chain.sample_evaluation`, `supply_chain.label_rules`
   (nội dung bắt buộc của nhãn, Nghị định 43/2017/NĐ-CP và 111/2021/NĐ-CP),
   `supply_chain.qc_aql`, `supply_chain.customs_file`, `supply_chain.supplier_email_style`.
4. Ghi một ADR nền tảng (skill là artifact có phiên bản) và đánh dấu upstream.

## Tiêu chí chấp nhận

- [x] Override của tenant A không tới tenant B; thiếu override thì dùng nền tảng.
- [x] Skill tham chiếu prompt không có thì nạp thất bại có tên.
- [x] Release manifest liệt kê skill và phiên bản một run đã dùng.
- [ ] `make ci` xanh; integration `dw_supply_chain` xanh; `process.md` mục 4 cột AI cập nhật. _(`make ci` xanh, `process.md` cập nhật; ticket này không có test cần Docker, bộ integration chung của context chưa chạy được ở máy này.)_

## Nguồn

- Lead, 9/10/2026: runtime không có skill (chỉ chú thích cũ về `configs/skills/sales_chat`).
- `.claude/plans/supply-chain/ai-automation/spec.md`.

## Comments

**2026-10-09, lát AI-04 (agent).** Đã làm (không Docker theo yêu cầu của Đạt):

- Nền tảng (ứng viên upstream, ADR nền tảng
  [0030](../../../../../docs/adr/0030-skills-are-versioned-artifacts.md)):
  `dw_agent_runtime.model.skills` (`SkillArtifact`, `SkillRegistry` qua `TenantOverlay`,
  `SkillRef` `id@1.2.0` hoặc `id@^1.2.0`, bản mới nhất thỏa phạm vi thắng),
  `TenantOverlay.tenant_values` (chỉ lớp của một tenant). `PromptArtifact.skills`;
  `PromptRegistry` đặt từng skill vào **phần system** dưới một tiêu đề cố định (văn bản tin
  cậy, không qua `<input>`), `RenderedPrompt.skills` nêu phiên bản đã dùng và checksum
  phủ cả lời của skill. `load_shipped_prompts(configs)` là cách duy nhất API, worker và
  eval dựng registry: kiểm khi nạp, `applies_to` phải là prompt có thật, prompt chỉ khai
  được skill có thật và có tên prompt đó trong `applies_to`; sai thì không khởi động, nêu
  tên.
- Tám skill đầu `configs/skills/supply_chain/*@1.0.0.yaml`: `process_part_a`,
  `step_documents`, `bm04_guide`, `sample_evaluation` (khai bởi bốn prompt trích xuất
  `extract_*@1.1.0`, lane dùng 1.1.0), `label_rules` (NĐ 43/2017/NĐ-CP, sửa bởi
  111/2021/NĐ-CP; tóm tắt để kiểm, không thay ý kiến pháp lý), `qc_aql`, `customs_file`,
  `supplier_email_style` (`applies_to: []`, prompt của AI-07, AI-16, AI-17 sẽ khai).
- Release manifest: mục `skills` (id, phiên bản, `applies_to`, checksum) và mỗi prompt
  bundle nêu phạm vi skill nó khai; test manifest kiểm mọi phạm vi được một skill trong
  manifest thỏa.

Mutation (control xanh trước, mỗi guard bỏ đi thì đỏ): đọc lớp của tenant khác; không kiểm
`applies_to`; caret nhận mọi phiên bản; prompt khai skill không nêu tên nó; skill không
được đặt vào system; checksum không phủ skill. 6/6 đỏ.

**Owed:** không có test cần Docker cho ticket này (registry thuần, không bảng). Tiêu chí
"integration `dw_supply_chain` xanh" là bộ integration chung của context, chưa chạy được ở
máy này (xem AI-01–03).

Chưa làm, ghi lại: nạp skill riêng của tenant từ lưu trữ lúc chạy (registry nhận
`load_bytes(..., tenant_id=)`; cửa lưu trữ làm chung với prompt khi tenant đầu tiên cần);
`RenderedPrompt.skills` chưa vào trace của lượt gọi (trace metadata của nền tảng chưa có
checksum prompt, làm cùng).
