---
status: Accepted
date: 2026-10-05
source:
    - ../../CLAUDE.md # Adding a bounded context; Non-negotiable architecture
    - ../../CONTEXT-MAP.md
    - ../agents/domain.md # Decisions are not all settled
---

# E1. Sản phẩm Elmich dựng trong repo này; phần chung nằm trong package nền tảng để đưa ngược lên sau

`dw-elmichs` là repo sản phẩm cho Elmich (chuỗi cung ứng). Nhánh `main` bắt đầu từ
nền tảng ở commit `bf553f4`; remote `origin` là `supplychain-dw`, remote `platform`
là `codebase`. Repo nhận cập nhật của nền tảng bằng `git merge platform/main` và
không bao giờ push lên `platform`.

**Quyết định (Đạt, 5/10/2026):** mọi việc của sản phẩm làm ở repo này, push lên
`supplychain-dw`, không làm trước ở `codebase`. Những phần không riêng của Elmich
vẫn đặt trong package và app của nền tảng, viết trung lập với sản phẩm, để sau này
đưa ngược lên `codebase` được mà không phải tách code:

| Phần chung                                     | Nơi đặt                                                                                | ADR        |
| ---------------------------------------------- | -------------------------------------------------------------------------------------- | ---------- |
| Kênh Zalo: liên kết, poll, webhook, gửi tin    | `dw_connectors`, `dw_platform`, `apps/api/.../routes/v1/zalo.py`, lane worker          | 0012–0015  |
| Hộp thư gửi kênh `platform.channel_deliveries` | `dw_platform`, migration nền tảng, lane worker                                         | 0013       |
| Trang cài đặt cá nhân                          | `apps/web/app/settings/`                                                               | 0022       |
| Giới hạn người quyết một approval              | `dw_platform` (approval), `dw_agent_runtime` (`ApproveAndResumeService`), `/approvals` | 0020       |
| Đăng nhập cổng, overlay hosting                | `infra/keycloak`, `infra/compose`, `apps/api` settings                                 | 0022, 0023 |

Phần riêng của Elmich nằm trong `packages/python/dw_supply_chain`, `configs/*/supply_chain*`,
`evals/datasets/supply_chain@*`, `apps/web/app/supply-chain/` và override của tenant
Elmich. Tên Elmich, số SLA của Elmich và chữ "chuỗi cung ứng" không xuất hiện trong
phần chung.

## Cách giữ cho phần chung đưa ngược lên được

- `lint-imports` đã cấm nền tảng import context. Phần chung không đọc bảng của
  `supply_chain`; nó nhận context qua Protocol context khai và composition root nối.
- Một commit không trộn phần chung với phần sản phẩm. Tiền tố commit nói rõ:
  `feat(platform)`, `feat(connectors)`, `feat(web)` cho phần chung;
  `feat(supply-chain)` cho sản phẩm. Ticket của phần chung ghi "ứng viên đưa ngược".
- Migration của phần chung là migration nền tảng: không nhắc `supply_chain`, có
  grant và test RLS riêng, nằm trước migration sản phẩm nào phụ thuộc nó khi có thể.

## Phương án đã cân nhắc

- **Làm trước ở `codebase`, rồi merge sang** (khuyến nghị của bản khảo sát
  5/10/2026). Đạt không chọn: `codebase` đang phục vụ sản phẩm đấu thầu, và mỗi lát
  phải qua hai repo.
- **Viết mọi thứ thẳng vào code sản phẩm.** Bác: kênh Zalo và quyền người quyết sẽ
  là bản sao thứ hai của việc nền tảng nên sở hữu, và mỗi lần `git merge
platform/main` sẽ đụng vào chúng (failure-modes #2).

## Hệ quả

- `main` của repo này lệch dần khỏi `platform/main`. Mỗi lần merge có thể xung đột
  ở các seam đã đánh dấu (`bootstrap/wiring.py`, `main.py`, `apps/worker/.../main.py`,
  `apps/web/lib/nav/registry.ts`).
- **Đưa một migration ngược lên khó hơn đưa code.** Revision id của migration phần
  chung nằm trong chuỗi của repo này; khi `codebase` nhận nó, `down_revision` phải trỏ
  vào head của `codebase`, và lần merge sau về đây phải nối lại chuỗi sản phẩm. Làm
  một lần cho mỗi đợt đưa ngược, có test `alembic heads` = 1 sau merge.
- **Số ADR.** ADR hệ thống của nền tảng đánh tới 0010 (nhánh `bidding` của
  `codebase`); code còn dẫn ADR-001..003 không có văn bản. ADR của sản phẩm này lấy
  0011–0023. Nếu `codebase` sau này có ADR 0011 trở đi, lần merge sẽ trùng số; khi đó
  đổi số phía repo này và sửa liên kết, vì số ADR ở đây chưa được code nào dẫn.
- **Chỗ của ADR riêng context.** Theo `docs/agents/domain.md`, ADR chỉ của context
  (E6–E9, E11) thuộc `packages/python/dw_supply_chain/docs/adr/`. Chúng tạm nằm ở
  `docs/adr/` vì package đang được chuyển từ nhánh lưu trữ cùng lúc; dời vào package
  là một việc mở trong `.claude/plans/supply-chain.md`.

## Điểm mở

1. Đưa ngược lên lúc nào và theo lát nào: chưa định. Mặc định là sau khi Elmich chạy
   thật một đợt.
