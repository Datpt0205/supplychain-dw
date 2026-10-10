# 01 — Lane nền ghi audit dưới tên chính nó (`system:<lane>`)

Status: resolved (2026-10-08, nhánh `feat/security-debts`)
Blocked by: —
Area: platform-runtime

## Mục tiêu

`audit_events.actor_id` là `uuid NOT NULL`, lane nền không có người đứng sau. Lane gửi
kênh ghi người nhận làm actor; hai lượt retention xoá cứng ký ức và tài liệu mà không ghi
gì. Sản phẩm (follow-up sweep) cần một quy ước để dùng, không chép một trong hai cách sai.

## Việc đã làm

- `dw_platform.domain.audit`: `system_actor(lane)` (uuid5 cố định theo tên đăng ký lane),
  `system_actor_label(lane)` = `system:<lane>`, `lane_audit_event(...)`.
- `dw_platform.adapters.persistence.lane_audit.append_across_tenants`: ghi từ giao dịch
  drain, bind tenant của từng sự kiện trước khi insert, cùng giao dịch với thay đổi.
- Áp dụng: `SqlMemoryRetention` (`memory.item.expired`), `SqlKnowledgeRetention`
  (`knowledge.document.purged`), `channel_deliveries._audit` (actor đổi từ người nhận sang
  lane, người nhận vào `details.recipient_user_id`).
- ADR 0011; dòng trong `CLAUDE.md` (Agent and tool rules).

## Quyết định tạm (Đạt giao)

- Tên lane = tên đăng ký trong worker (`retention`, `retention_knowledge`,
  `channel_delivery`): một tên, không đặt thêm tên thứ hai.
- Không audit dọn dẹp dòng vận hành (checkpoint, notification, nonce, inbound, delivery,
  approval code, spend guard, partition, evidence mồ côi): không phải hồ sơ do người tạo.
- Offboarding giữ actor là người yêu cầu (người quyết, lane thực thi).
- Ghi audit hỏng thì thay đổi không xảy ra (cùng giao dịch, fail closed).

## Tiêu chí chấp nhận

- [x] Unit: id cố định, version 5, tên lane sai bị từ chối, `details.actor` không đè được.
- [x] Integration: ký ức hết hạn và tài liệu xoá cứng có một dòng audit trong đúng tenant,
      actor là lane; tenant khác không thấy; delivery có actor là lane, người nhận ở details.
- [x] Mutation: bỏ ghi audit memory → 1 đỏ; knowledge → 1 đỏ; actor delivery về người
      nhận → 1 đỏ; bỏ bind tenant → 13 đỏ (RLS từ chối insert, cả lượt xoá rollback).
