# 02 — Lượt quét follow-up ghi audit với tư cách chính nó

Status: resolved
Blocked by: platform ADR 0011 (`system_actor`, `lane_audit_event`, merge M3 `4890979`)
Area: supply-chain

## Mục tiêu

Lượt quét (`supply_chain_follow_ups`) mở và đóng (resolved) follow-up nhưng chỉ ghi lên
chính dòng follow-up, không có gì trong `platform.audit_events` (mục "Follow-ups" phần
Open). Người đóng tay (`close_done`) thì đã có audit; việc hệ thống tự làm thì không.

## Tiêu chí chấp nhận

- [x] Mỗi follow-up lượt quét mở ghi `supply_chain.follow_up.opened`, mỗi follow-up nó
      đóng ghi `supply_chain.follow_up.resolved`, cùng transaction với thay đổi.
- [x] Actor là `system_actor("supply_chain_follow_ups")`, `details.actor` là
      `system:supply_chain_follow_ups`; không bao giờ id của một người (kể cả PIC được
      giao).
- [x] Episode đã có (mở hay đã đóng) không mở lại và không ghi audit lần hai; follow-up
      đã đóng không bị "resolve" và không ghi audit.
- [x] Integration đọc `platform.audit_events` thật; mutation bỏ append thì đỏ.

## Comments

**2026-10-08 (agent, Đạt giao quyết tạm):**

- **Ai dựng event:** sweep (application) dựng bằng `lane_audit_event`; repository nhận
  hàm `audit(draft)` / `audit(record)` và chỉ gọi cho dòng thật sự đổi
  (`INSERT … ON CONFLICT DO NOTHING RETURNING id`, `UPDATE … RETURNING id`), nên một
  episode trùng không sinh audit. Port đổi: `open(..., *, audit)`, `resolve(context,
records, *, audit) -> int` (đếm số thật sự đóng).
- **Tên lane một chủ:** `FOLLOW_UP_SWEEP_LANE` trong `application/follow_up_sweep.py`;
  worker đăng ký lane bằng hằng này, nên đổi tên lane là đổi actor, có chủ đích.
- **Không audit:** gửi thông báo (inbox và outbox kênh đã ghi mỗi lần giao), và
  `mark_notified` (sổ sách của chính dòng). `SWEEP_PRINCIPAL` của `sweep_context`
  giữ nguyên: context không role, không scope dùng chung cho nhiều lane; actor của
  audit là id của lane.
- **Details:** loại hồ sơ, id hồ sơ, loại follow-up, episode; khi mở thêm scope người
  nhận và `pic_routed` (có/không), không ghi id PIC.
- **Một tham số:** `resolve` bind danh sách id bằng một mảng (`= ANY(:ids)`), không một
  tham số mỗi follow-up.
- **Mutation:** bỏ `audits.append` ở `open` hoặc `resolve` → integration
  `test_a_quiet_case_reaches_its_coordinator_once_and_resolves_when_the_supplier_writes`
  đỏ; actor ngẫu nhiên trong `lane_audit_event` → unit audit test đỏ.
