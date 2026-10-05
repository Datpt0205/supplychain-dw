# 03 — Hạn giữ cho `supply_chain.follow_ups`

Status: ready-for-agent
Blocked by: .claude/plans/supply-chain/port/issues/01-port-dw-supply-chain.md
Area: supply-chain

## Mục tiêu

`follow_ups` lớn không giới hạn (failure-modes #6): mỗi hồ sơ mỗi đợt nhắc thêm một dòng,
`dw_app` không có DELETE (`dc2285c629d4_supply_chain_follow_ups.py:115`), không lane
dọn nào đọc bảng này; dòng chỉ mất khi hồ sơ PO bị xóa hoặc offboarding xóa `po_cases`
và cascade theo.

## Việc cần làm

1. Migration mới (revision ngẫu nhiên của alembic): hàm `SECURITY DEFINER`
   `supply_chain.prune_follow_ups(older_than interval)` xóa dòng đã đóng
   (`status IN ('done','resolved')`) có `closed_at` cũ hơn hạn; không bao giờ xóa dòng
   `open`. `GRANT EXECUTE` cho `dw_app`, không grant DELETE trên bảng.
2. Hạn giữ đọc từ policy `supply_chain_follow_ups` (một chủ của số, tenant đổi được
   qua `PolicyOverridePort`, có sàn nền tảng không hạ được); mặc định 180 ngày.
3. Gọi hàm từ lane retention có sẵn của worker (đăng ký ở composition root, không thêm
   nhánh vào vòng lặp lane).

## Tiêu chí chấp nhận

- [ ] Integration: dòng đã đóng quá hạn bị xóa; dòng `open` mọi tuổi và dòng đã đóng
      trong hạn còn nguyên; tenant khác không bị đụng.
- [ ] `test_privileges.py` vẫn xanh: `dw_app` không có DELETE/TRUNCATE trên `follow_ups`.
- [ ] Mutation: bỏ điều kiện `status <> 'open'` thì test đỏ (ghi vào Comments).

## Nguồn

- `.claude/rules/failure-modes.md` #6; mẫu `platform.prune_notifications()`.

## Comments
