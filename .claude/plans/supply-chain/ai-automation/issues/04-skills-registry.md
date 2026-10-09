# 04 — Skill registry (nền tảng)

Status: ready-for-agent
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

- [ ] Override của tenant A không tới tenant B; thiếu override thì dùng nền tảng.
- [ ] Skill tham chiếu prompt không có thì nạp thất bại có tên.
- [ ] Release manifest liệt kê skill và phiên bản một run đã dùng.
- [ ] `make ci` xanh; integration `dw_supply_chain` xanh; `process.md` mục 4 cột AI cập nhật.

## Nguồn

- Lead, 9/10/2026: runtime không có skill (chỉ chú thích cũ về `configs/skills/sales_chat`).
- `.claude/plans/supply-chain/ai-automation/spec.md`.

## Comments
