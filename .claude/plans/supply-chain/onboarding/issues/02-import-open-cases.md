# ON-02 — Nạp hồ sơ đang chạy ở trạng thái hiện tại

Status: resolved
Blocked by: .claude/plans/supply-chain/onboarding/issues/01-import-suppliers-catalogue-users.md
Area: supply-chain

## Mục tiêu

Sản phẩm đang lấy mẫu và PO đang chạy mở ở đúng trạng thái, với một dòng lịch sử `import`.

## Việc cần làm

1. Hành động `import` (chỉ lệnh nạp dùng, không có trên giao diện): `from_state` NULL, lý do
   "nạp từ dữ liệu cũ", ngày vào trạng thái từ sheet (đánh dấu khai báo).
2. Mốc SLA chạy từ ngày đó; PIC từ sheet (phải là thành viên workspace).
3. CHECK lịch sử cho phép `import` chỉ là dòng đầu.

## Tiêu chí chấp nhận

- [x] **Test âm:** tenant B và workspace khác cùng tenant không đọc, không ghi bảng mới; `test_rls_coverage.py`, `test_privileges.py` xanh. _(Không bảng mới; unit: hồ sơ SP của workspace khác không tính là đã có, không nối được. Integration viết, chưa chạy.)_
- [x] Hành động `import` không gọi được qua API (422).
- [x] PIC không thuộc workspace: dòng bị từ chối.
- [ ] `make ci` xanh; integration `dw_supply_chain` xanh; `process.md` mục 4 cột AI cập nhật. _(Phần chạy được không Docker xanh; integration nợ.)_

## Nguồn

- ADR 0027 (E16).
- `.claude/plans/supply-chain/ai-automation/spec.md`.

## Comments

**2026-10-10, lát ON-02 (agent).** Đã làm (không Docker):

- Mẫu nạp thêm hai sheet "Hồ sơ SP" và "Hồ sơ PO" (`SHEETS`, mẫu trong `docs/products/elmich/` dựng
  lại); `ImportSupplyChainData` thêm `_product_cases`, `_po_cases` (cổng `OpenCaseImportPort`,
  `CategoryListPort`; `MemberDirectoryPort.workspace_member_ids`). Chạy thử không ghi gì; chạy lại
  cùng file: "Đã có, bỏ qua"; hồ sơ PO nối được với hồ sơ SP cùng file (cả khi chạy thử).
- Domain: `ProductAction.IMPORT` (`IMPORT_ONLY_ACTIONS`: route 422, lệnh `DomainError`, policy duty
  không nêu), `ProductDevelopmentCase.imported`, `domain/case_import.py` (`IMPORTABLE_PO_STATES`,
  `imported_po_case`, `check_dates`: ngày tương lai, vào bước trước ngày tạo), `ProductCaseStep.
occurred_at`; `CaseTransition.from_state` của PO nullable (API, web: "Nạp từ dữ liệu cũ").
- Migration `e623edd08e76`: action `import` vào CHECK; `ck_..._starts`; cột `action` + `from_state`
  nullable cho lịch sử PO; unique một dòng bắt đầu mỗi hồ sơ; trigger `refuse_late_start`.
- Mutation (unit + API): 16/16 đỏ (bước nạp được SP/PO, PIC thành viên, nhóm, chạy thử không ghi, đã
  có (SP, PO), quyền mở (SP, PO), ngày tương lai, vào bước trước ngày tạo, ngày của dòng import, route
  và lệnh nhận `import`, nối hồ sơ SP workspace khác, số vòng mẫu).
- Nợ: `tests/integration/test_open_case_import.py` (dòng import lùi ngày, vòng mẫu, unique, trigger,
  CHECK, RLS tra cứu) chưa chạy; dòng hàng PO chưa nạp (ADR 0027 sửa đổi ON-02 điểm 3).
