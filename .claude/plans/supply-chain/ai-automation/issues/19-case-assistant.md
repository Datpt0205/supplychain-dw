# 19 — Trợ lý hồ sơ (agent chỉ đọc)

Status: resolved
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

- [x] Câu hỏi về hồ sơ workspace khác: không kết quả (test ở gateway và tool). _(Không gateway/tool: test ở handler, kể cả store rò; eval `qa-sec-cross-*`.)_
- [x] Tool đổi trạng thái không tồn tại; agent xin `prepare_step` không có duty bị từ chối bởi executor. _(Không có tool nào: trợ lý không ghi gì; ADR 0025 sửa đổi AI-19.)_
- [x] Câu trả lời không trích dẫn được: "chưa có thông tin", không đoán. _("Không đủ bằng chứng trong hồ sơ để trả lời câu này.")_
- [x] **Eval** (`supply_chain_preparation`, ticket 06): chèn lệnh trong file/nội dung nguồn không đổi kết quả; chứng từ của tenant B và của workspace khác cùng tenant bị từ chối trước lượt gọi mô hình (mock gateway đếm 0 lượt); ô không có trích dẫn tìm thấy là khoảng trống; số khác số code tính bị từ chối. Mỗi ca an ninh đỏ khi bỏ guard của nó (ghi đột biến vào Comments).
- [ ] `make ci` xanh; integration `dw_supply_chain` xanh; `process.md` mục 4 cột AI cập nhật.

## Nguồn

- `rt/adapters/agent_factory.py`; `rt/executor.py`; Z6, S8.
- `.claude/plans/supply-chain/ai-automation/spec.md`.

## Comments

**2026-10-10, lát AI-19 (agent).** Đã làm (không Docker):

- `AskAboutCase` (`application/case_assistant.py`), bằng chứng trong `domain/case_answer.py`: hồ sơ,
  lịch sử, BM04 (giá chỉ khi được), số đo vòng mẫu, trường đã đọc của chứng từ (chỉ với
  `document.read`; chứng từ in số tiền chỉ với `commercial.read`; ô giá trong dòng hàng bỏ). Prompt
  `answer_case_question@1.0.0`, tác vụ `draft.case_answer`; không câu nào kiểm được hay mô hình sai
  schema: "Không đủ bằng chứng…". Không tool, không agent loop (ADR 0025 sửa đổi AI-19, lệch ticket
  có lý do).
- Cổng: `POST /po-cases/{id}/questions`, `/product-cases/{id}/questions`; thẻ "Hỏi về hồ sơ" trên hai
  trang hồ sơ (câu AI viết có nhãn và nguồn). Zalo: `ZaloCaseQueryCommand.assistant`, khi câu hỏi mở
  đúng một hồ sơ thì thêm câu trả lời nội dung, kênh Zalo (không giá, trần kênh không có chứng từ).
- Dataset `supply_chain_preparation@1.16.0` (routes 1.16.0, +15 ca `qa-*`, 284): MOQ trong BM04, vì
  sao vòng 2 không đạt, giá cho người có quyền, không đủ bằng chứng, dẫn mục lạ, số bịa, giá không
  quyền, giá qua Zalo, chứng từ không quyền, hồ sơ workspace khác (adapter rò), hồ sơ tenant khác,
  chứng từ workspace khác, chèn lệnh trong câu hỏi và trong trường đã đọc, mô hình sai schema.
- Mutation (unit): 14/14 đỏ (lần đầu 13/14: lọc chứng từ workspace khác chỉ là lớp hai, thêm test
  store rò). Eval: 5/6 đỏ; lọc chứng từ workspace khác sống sót ở eval vì fake giữ RLS (lớp hai có
  unit).
- Nợ: chạy thật qua Zalo (ZL); integration không có bảng mới.
