# ON-02 — Nạp hồ sơ đang chạy ở trạng thái hiện tại

Status: ready-for-agent
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

- [ ] **Test âm:** tenant B và workspace khác cùng tenant không đọc, không ghi bảng mới; `test_rls_coverage.py`, `test_privileges.py` xanh.
- [ ] Hành động `import` không gọi được qua API (422).
- [ ] PIC không thuộc workspace: dòng bị từ chối.
- [ ] `make ci` xanh; integration `dw_supply_chain` xanh; `process.md` mục 4 cột AI cập nhật.

## Nguồn

- ADR 0027 (E16).
- `.claude/plans/supply-chain/ai-automation/spec.md`.

## Comments
