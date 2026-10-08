# 05 — Vận hành: sao lưu Keycloak, deploy có overlay host, màn Cấu hình

Status: resolved
Blocked by: H (`hosting/issues/01`)
Area: supply-chain

## Mục tiêu

Những điều H để lại và một màn quản trị còn thiếu:

- Sao lưu chỉ dump `dw`; mất database `keycloak` là mất mọi người dùng và realm.
- `scripts/deploy.sh` không biết overlay host; lần dựng có Caddy chạy bằng tay.
- Build arg `NEXT_PUBLIC_CHAT_BASE_URL` không ai đọc.
- `DW_APPROVAL_CODE_SECRET` cần nói rõ trong `.env.example` và runbook (không bao giờ một
  giá trị).
- SLA của tenant và quy tắc test tiền sản xuất chỉ đặt được bằng script.

## Tiêu chí chấp nhận

- [x] `backup_postgres.sh` dump `dw` và `keycloak` trong một lần chạy (dòng cron trong đầu
      file và runbook); `restore_postgres.sh --latest` chỉ lấy bản của database đang khôi phục.
- [x] `deploy.sh <nhánh> uat|production hosted` dựng base, profile, host theo thứ tự.
- [x] Không còn `NEXT_PUBLIC_CHAT_BASE_URL` trong Dockerfile, compose, CI, merge env.
- [x] `.env.example` và `docs/deploy/host.md` nói độ dài, chỗ dùng, hậu quả khi đổi khóa;
      không có giá trị.
- [x] `/supply-chain/settings` (antd, cùng PageHeader/Card/Table của các trang Supply
      Chain): đọc và đặt SLA và quy tắc test tiền sản xuất qua `PUT /sla-policy`,
      `PUT /packaging-policy`; thiếu quyền ghi thì khóa với lý do bằng chữ; server từ chối
      dù màn hình cho gì.

## Comments

**2026-10-08 (agent, Đạt giao quyết tạm):**

- **Sao lưu:** `PG_DBS` (mặc định `dw keycloak`), `PG_DB` vẫn chọn một database khi chạy
  tay. Khóa S3 được kiểm trước khi dump. Phát hiện kèm: `--latest` của restore lấy object
  mới nhất của cả bucket, nên khi bucket có cả `keycloak_…` nó có thể khôi phục dump
  Keycloak vào `dw`; nay lọc theo tiền tố `${DB}_`.
- **Deploy:** `hosted` là tham số thứ ba, không đọc từ `.env` (cùng lý do môi trường là
  tham số); dev từ chối `hosted`. Phát hiện kèm: cổng chờ healthy grep tên container
  `dw-(api|worker|web)-1`, nhưng project này là `dw_elmichs`, nên không khớp gì và báo
  "all healthy" ngay. Nay hỏi `docker compose ps` theo tên service, service không chạy
  cũng là chưa healthy, và có `caddy` khi `hosted`. Workflow `deploy.yml` chưa truyền
  `hosted` (máy nào dùng Caddy thì đổi dòng đó khi có H2).
- **Màn Cấu hình:** hai thẻ, mỗi thẻ hỏi quyền đọc của chính nó (`sla_policy.read`,
  `action_duties.read`; quy tắc bao bì dùng quyền của duties như route). Sửa được: số ngày
  và trạng thái của từng mốc (chung và riêng Category), nhịp nhắc NCC; quy tắc test là một
  công tắc. Lưu gửi cả document (route thay nguyên khối). Chưa làm: thêm/bỏ Category, thêm
  mốc riêng cho một Category mới.
- **Server:** cả hai PUT đã từ chối khi thiếu quyền ghi. Mutation: đổi quyền kiểm ở
  `SetSLAPolicyOverride` thành quyền đọc → `test_writing_an_sla_policy_override_is_denied…`
  đỏ; ở `SetPackagingPolicyOverride` → ban đầu SỐNG (test chỉ dùng người không có cả quyền
  đọc), đã thêm ca người chỉ đọc bị 403, nay đỏ. Web: bỏ khóa theo quyền → vitest
  `settings.test.tsx` đỏ.
