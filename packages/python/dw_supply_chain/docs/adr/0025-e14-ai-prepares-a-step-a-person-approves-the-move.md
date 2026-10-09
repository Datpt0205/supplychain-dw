---
status: Accepted (Đạt, 2026-10-09)
date: 2026-10-09
source:
    - ../../../../../docs/products/elmich/process.md#32-bảng-17-bước
    - ../../../../../docs/products/elmich/poc-slides-brief.md # slide 4, 8: bản nháp chứng từ
    - ../../../dw_agent_runtime/src/dw_agent_runtime/adapters/agent_factory.py # build_agent, chưa ai dùng
    - ../../../dw_agent_runtime/src/dw_agent_runtime/contracts.py # ToolDefinition
    - ../../../../../.claude/plans/supply-chain/ai-automation/spec.md
---

# E14. AI chuẩn bị bước, người duyệt việc chuyển bước

Hướng của Đạt ngày 9/10/2026: "việc gì AI làm được thì để AI làm, người chỉ kiểm và
duyệt". Hôm nay AI chỉ đọc tin chat (năm prompt một lượt gọi); mọi chứng từ của 17 bước do
người viết ngoài ứng dụng rồi tải lên như file đóng kín, và slide 4, 8 của PoC hứa bản
nháp phiếu chỉnh sửa, tờ trình, BM04, email chốt NCC, PO mà không code nào làm.

**Quyết định:**

1. **Một lượt chuẩn bị mỗi lần vào bước.** Khi một hồ sơ vào trạng thái mà policy tenant
   `supply_chain_step_preparation` liệt kê, lane `supply_chain_step_preparation` khởi động
   một run của graph `supply_chain_step_preparation` (idempotent theo id dòng lịch sử và
   phiên bản policy). Run đọc hồ sơ, lịch sử, chứng từ qua port; trích xuất chứng từ nguồn
   chưa trích ([ADR 0021](0021-e11-case-documents-through-an-object-storage-port.md) sửa
   đổi 2026-10-09); soạn các bản nháp bước cần; chạy các phép kiểm của bước; dựng file từ
   mẫu; rồi trình **một approval**: "chuyển sang bước X với chứng từ Y".
2. **Mô hình đọc và viết, code quyết.** Mỗi trích xuất, mỗi bản nháp là một lượt gọi có
   cấu trúc, kiểm vào schema Pydantic theo loại chứng từ. Số tiền, tổng, số lượng, hạn do
   code tính. Phép kiểm (BM04 với thư trả lời của NCC, PO với PI, hóa đơn với PO và
   packing list, đếm với giao, tài khoản với danh mục NCC) là code so giá trị có kiểu. Ô
   thiếu hoặc mâu thuẫn thành "khoảng trống" có tên, không bao giờ đoán.
3. **Approval của đề xuất bước:** loại `supply_chain.step_proposal.<action>`;
   `required_scope` là scope duty của hành động đích, đọc từ policy duty lúc tạo và đóng
   dấu ([ADR 0020](../../../../../docs/adr/0020-e10-approval-decider-stamped-as-required-scope.md));
   payload mang id hồ sơ, phiên bản hồ sơ, hành động đích, id + phiên bản + sha256 của
   từng bản nháp, danh sách phát hiện. Người yêu cầu là lane (audit như chính nó,
   [ADR 0011](../../../../../docs/adr/0011-a-background-lane-audits-as-itself.md)).
4. **Duyệt là một giao dịch:** mỗi bản nháp thành một dòng `case_documents`
   (`origin = ai_prepared`, `draft_id`), rồi hành động được áp qua đúng bảng dispatch mà
   một cú bấm dùng, actor là người quyết. **Không duyệt:** bản nháp ghi "bị từ chối" kèm lý
   do bắt buộc; hồ sơ không đổi. Bản nháp không bao giờ thỏa điều kiện chứng từ của một
   bước khi chưa được duyệt.
5. **Bước có việc vật lý** (test mẫu, test trước SX, QC, trả tiền, đếm hàng): run chuẩn bị
   hồ sơ với gợi ý đặt **cạnh** ô kết quả để trống; người nhập kết quả rồi duyệt, trên web.
   Zalo chỉ báo, vì mã một lần không mang được kết quả. Đề xuất không có ô kết quả duyệt
   được trên Zalo bằng mã của cổng ([ADR 0014](../../../../../docs/adr/0014-e4-decisions-on-zalo-after-a-portal-view.md)).
6. **Đề xuất cũ thì hết hiệu lực.** Hồ sơ đổi phiên bản hoặc một chứng từ nguồn có bản mới
   sau khi đề xuất được trình: quyết định bị từ chối có tên (409), approval kết thúc
   `superseded`, lane chuẩn bị lại.
7. **Đường tay vẫn còn.** Người giữ duty vẫn bấm được hành động với file của mình như hôm
   nay. Mô hình hỏng, hết lượt chạy, file không đọc được: hồ sơ hiện "chưa chuẩn bị được",
   không đề xuất, không đoán; lane reconcile trình lại khi có lượt.
8. **Không agent nào đổi trạng thái hay gửi ra ngoài.** Vòng lặp agent duy nhất là trợ lý
   hồ sơ chỉ đọc (`build_agent`, autonomy A1, toolset `supply_chain_assistant`, mọi tool
   `side_effect_level: none` trừ `prepare_step` là `internal`, chỉ xin chuẩn bị lại).
9. **Policy:** `supply_chain_step_preparation@1.0.0`, theo (loại hồ sơ, trạng thái): nguồn,
   bản nháp, phép kiểm, hành động đích, có ô kết quả hay không. Nền tảng: rỗng (không chuẩn
   bị gì). Elmich ghi đè: mọi bước. Prompt, mẫu chứng từ, skill đi qua `TenantOverlay`.
10. **Mô hình:** profile `luna` cho soạn và đọc file; một tác vụ chuyển sang Qwen chỉ khi
    qua cổng eval của chính tác vụ đó (`supply_chain_preparation`).

## Phương án đã cân nhắc

- **Một agent có tool ghi cho cả 17 bước.** Bác: một super-agent, khó eval, và một tool
  đổi trạng thái là quyền quyết trong tay mô hình.
- **AI tự chuyển bước khi tin cậy cao.** Bác (Đạt): người duyệt mọi lần chuyển bước.
- **Bản nháp là `case_documents` có cờ.** Bác: `case_documents` chỉ thêm, `dw_app` không
  UPDATE, và một cờ trên bảng mà cổng chứng từ đọc là chỗ fail-open (failure-modes #7).

## Hệ quả

- Bảng mới `document_drafts` (+ quyết định), `document_extractions`; mẫu chứng từ có phiên
  bản; skill registry ở nền tảng; ticket `.claude/plans/supply-chain/ai-automation/`.
- `process.md` mục 4 có cột AI; mỗi bước nói AI chuẩn bị gì.
- Đo được: tỷ lệ bản nháp duyệt nguyên, sửa, từ chối (AI-20).
