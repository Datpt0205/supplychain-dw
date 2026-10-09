# 19 — Trợ lý hồ sơ (agent chỉ đọc)

Status: ready-for-agent
Blocked by: .claude/plans/supply-chain/ai-automation/issues/02-document-extraction-lane.md, .claude/plans/supply-chain/ai-automation/issues/04-skills-registry.md
Area: supply-chain

## Mục tiêu

Hỏi về nội dung hồ sơ ("BM04 ghi MOQ bao nhiêu?", "vì sao vòng 2 không đạt?") và "bước tiếp theo là gì" trên command bar và Zalo, trả lời có dẫn chứng.

## Việc cần làm

1. Tool spec (`configs/tools/supply_chain/*@1.0.0.yaml`): `case_get`, `case_history`,
   `case_documents_list`, `document_extraction_get`, `supplier_lookup`, `catalogue_check`,
   `sla_status` (`side_effect_level: none`), `prepare_step` (`internal`, scope duty của
   hành động đích, idempotent theo (hồ sơ, phiên bản)). Toolset `supply_chain_assistant@1.0.0`.
2. Agent qua `build_agent` (autonomy A1), worker yaml có `toolset_version`; đăng ký qua
   `RuntimeSeam.tools` ở composition root.
3. Văn bản chứng từ qua knowledge gateway với filter tenant/workspace/hồ sơ do backend đặt.
4. Zalo: không giá, ≤ 10 hồ sơ, liên kết như Z6.

## Tiêu chí chấp nhận

- [ ] Câu hỏi về hồ sơ workspace khác: không kết quả (test ở gateway và tool).
- [ ] Tool đổi trạng thái không tồn tại; agent xin `prepare_step` không có duty bị từ chối bởi executor.
- [ ] Câu trả lời không trích dẫn được: "chưa có thông tin", không đoán.
- [ ] **Eval** (`supply_chain_preparation`, ticket 06): chèn lệnh trong file/nội dung nguồn không đổi kết quả; chứng từ của tenant B và của workspace khác cùng tenant bị từ chối trước lượt gọi mô hình (mock gateway đếm 0 lượt); ô không có trích dẫn tìm thấy là khoảng trống; số khác số code tính bị từ chối. Mỗi ca an ninh đỏ khi bỏ guard của nó (ghi đột biến vào Comments).
- [ ] `make ci` xanh; integration `dw_supply_chain` xanh; `process.md` mục 4 cột AI cập nhật.

## Nguồn

- `rt/adapters/agent_factory.py`; `rt/executor.py`; Z6, S8.
- `.claude/plans/supply-chain/ai-automation/spec.md`.

## Comments
