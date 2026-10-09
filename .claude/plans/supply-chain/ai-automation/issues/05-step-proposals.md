# 05 — Đề xuất bước: chuẩn bị và duyệt để chuyển (E14)

Status: ready-for-agent
Blocked by: .claude/plans/supply-chain/ai-automation/issues/02-document-extraction-lane.md, .claude/plans/supply-chain/ai-automation/issues/03-drafts-and-templates.md
Area: supply-chain

## Mục tiêu

Khi hồ sơ vào bước có trong policy, một run chuẩn bị soạn, đọc, kiểm, rồi trình "chuyển sang bước X với chứng từ Y"; bước đổi chỉ khi người có duty duyệt.

## Việc cần làm

1. **Policy** `supply_chain_step_preparation@1.0.0`: theo (loại hồ sơ, trạng thái): nguồn,
   bản nháp, phép kiểm, hành động đích, `physical: bool`. Nền tảng rỗng; override Elmich
   (script như `elmich_sla_override.yaml`) bật các bước đã có công thức.
2. **Graph** `supply_chain_step_preparation` (`configs/workers/supply_chain_step_preparation.yaml`,
   worker riêng): nạp → trích nguồn còn thiếu → soạn → kiểm → dựng → approval
   `supply_chain.step_proposal.<action>`, `required_scope` = scope duty của hành động đích
   đóng dấu lúc tạo; payload: hồ sơ, phiên bản hồ sơ, hành động, bản nháp (id, phiên bản,
   sha256), phát hiện. Node không SQL, không SDK.
3. **Lane** `supply_chain_step_preparation`: dòng lịch sử mới vào trạng thái có trong policy →
   khởi động run, idempotent theo (id dòng lịch sử, phiên bản policy) qua seam thread của
   `worker_runs`; lane reconcile trình lại khi hết lượt hay hỏng (mẫu
   `supply_chain_product_review_reconcile`).
4. **Duyệt** trong một giao dịch: bản nháp → `case_documents` (`ai_prepared`) → áp hành động
   qua dispatch hiện có, actor = `decided_by`; audit. **Không duyệt:** lý do bắt buộc, bản
   nháp `rejected`, hồ sơ không đổi.
5. **Hết hiệu lực:** phiên bản hồ sơ hay chứng từ nguồn đổi → quyết định bị 409, approval
   `superseded`, chuẩn bị lại.
6. **Bước vật lý:** form duyệt trên web có ô kết quả để trống, gợi ý bên cạnh; Zalo chỉ báo.
   Bước khác duyệt được trên Zalo bằng mã cổng (Z5).
7. **Web:** khối "AI đã chuẩn bị" trên trang hồ sơ (bản nháp, phát hiện, nút tới approval),
   trạng thái "chưa chuẩn bị được" kèm lý do.

## Tiêu chí chấp nhận

- [ ] **Test âm:** tenant B và workspace khác cùng tenant không đọc, không ghi bảng mới; `test_rls_coverage.py`, `test_privileges.py` xanh.
- [ ] Người không giữ duty của hành động đích không quyết được (kể cả `platform_admin`, A2); đột biến bỏ đóng dấu scope thì đỏ.
- [ ] Hồ sơ đổi sau khi trình: duyệt bị 409, hồ sơ không đổi, run mới được trình.
- [ ] Duyệt hai lần (web và Zalo cùng lúc) áp đúng một lần.
- [ ] Bước vật lý: approval không nhận kết quả rỗng; gợi ý không bao giờ là giá trị mặc định của ô (test form và API).
- [ ] Mô hình hỏng/hết lượt: không approval, hồ sơ hiện lý do, reconcile trình sau.
- [ ] Đường tay: người giữ duty bấm hành động với file của mình vẫn được; đề xuất đang chờ thành `superseded`.
- [ ] Policy nền tảng rỗng: tenant khác không có run nào (test).
- [ ] **Eval** (`supply_chain_preparation`, ticket 06): chèn lệnh trong file/nội dung nguồn không đổi kết quả; chứng từ của tenant B và của workspace khác cùng tenant bị từ chối trước lượt gọi mô hình (mock gateway đếm 0 lượt); ô không có trích dẫn tìm thấy là khoảng trống; số khác số code tính bị từ chối. Mỗi ca an ninh đỏ khi bỏ guard của nó (ghi đột biến vào Comments).
- [ ] `make ci` xanh; integration `dw_supply_chain` xanh; `process.md` mục 4 cột AI cập nhật.

## Nguồn

- ADR 0025 (E14); ADR 0020; ADR 0014; `workflows/advance_product_case_graph.py` (mẫu interrupt, superseded).
- `.claude/plans/supply-chain/ai-automation/spec.md`.

## Comments
