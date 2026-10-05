# 01 — `.env.example` đầy đủ, `scripts/init_env.py`, mặc định Keycloak đúng cổng

Status: resolved
Blocked by: .claude/plans/supply-chain/port/issues/01-port-dw-supply-chain.md
Area: supply-chain

## Mục tiêu

Mọi biến settings đọc đều có trong `.env.example`, và ngược lại; `.env` đầu tiên sinh
bằng một lệnh (spec, Mục tiêu).

## Việc cần làm

1. `.env.example`: thêm các biến settings hoặc compose đọc hôm nay mà mẫu thiếu:
   `DW_API_CORS_ORIGINS`, `KC_HOSTNAME`, `DW_API_AUTO_PROVISION_MEMBERSHIP` (mặc định
   `false`), `DW_API_DEFAULT_TENANT_ID`/`_WORKSPACE_ID`/`_ROLE`, mỗi biến một dòng chú
   thích. Biến Zalo (`ZALO_*`, `DW_API_PUBLIC_BASE_URL`, interval của lane gửi kênh)
   đã có trong mẫu nhưng **tắt** (dòng bắt đầu bằng `#`, kèm chú thích "not wired
   yet"): `init_env.py` không sinh secret cho chúng và test đối chiếu không coi chúng là
   khóa. Ticket đọc chúng (`zalo-channel/issues/01`–`03`) bỏ dấu `#` cùng trường
   settings. Đây là ngoại lệ có chủ đích với "vào mẫu cùng ticket đọc chúng": giữ chú
   thích ở một chỗ, không bật khóa nào trước người đọc. Biến SMTP của realm vào cùng
   `personal-settings/issues/01`. Không khai biến hoạt động mà không ai đọc
   (failure-modes #1).
2. Bỏ các dòng `apps/chat`, `DW_CHAT_*`, `NEXT_PUBLIC_CHAT_BASE_URL`; bỏ
   `TELEGRAM_BOT_TOKEN` khỏi `.env.example` và compose. `NEXT_PUBLIC_CHAT_BASE_URL` bỏ ở
   mọi chỗ cùng lúc, không chỉ ở mẫu: `infra/compose/docker-compose.yml` (build arg và
   runtime của `web`), `docker-compose.dev.yml:103`, `infra/docker/web.Dockerfile:35,41`,
   `scripts/merge_server_env.py:94`, `.github/workflows/ci.yml:45`; nếu không, test đối
   chiếu ở bước 6 thấy compose truyền một biến mẫu không có.
3. Ghi cổng cục bộ của repo này (compose project `dw_elmichs`; 25432, 26333/26334,
   26379, 29000/29001, 28686, 28110) ở đầu file.
4. `scripts/init_env.py` (đã có trong cây làm việc, 5/10/2026): đọc `.env.example`,
   thay mỗi placeholder `change-me-<tên>` bằng một secret ngẫu nhiên
   (`secrets.token_hex(32)`), cùng placeholder cùng giá trị ở mọi chỗ (mật khẩu và URL
   nhúng nó khớp nhau); giá trị rỗng để rỗng (khóa do nhà cung cấp cấp). Một luật, có
   test: `test_init_env.py` khẳng định mọi khóa trông như secret (`PASSWORD`, `SECRET`,
   `TOKEN`, `_KEY`, `SALT`) trong mẫu là rỗng hoặc `change-me-*`. Từ chối ghi đè `.env`
   đã có (exit khác 0, nói rõ); không in secret.
5. Mặc định Keycloak của web không còn `localhost:8080`: `config.ts` và
   `web.Dockerfile:32` đã đổi về `localhost:8686`, cùng mặc định của compose (5/10/2026).
   Còn lại: quyết bỏ hẳn mặc định (bắt buộc env) hay giữ.
6. Một test đối chiếu: tập alias env của `ApiSettings` và `WorkerSettings` (cùng biến
   compose truyền vào) bằng tập khóa của `.env.example`, trừ một danh sách loại trừ có
   lý do từng dòng.

## Tiêu chí chấp nhận

- [ ] Test đối chiếu xanh; thêm một trường settings mà không thêm dòng mẫu thì test
      đỏ (ghi vào Comments).
- [ ] `init_env.py` chạy hai lần: lần hai từ chối, `.env` không đổi; hai lần chạy ở
      hai thư mục cho secret khác nhau; output không chứa giá trị secret nào.
- [ ] `make infra-up` với `.env` vừa sinh lên đủ dịch vụ (ghi lệnh vào Comments).
- [ ] `rg "localhost:8080" apps/web infra/docker` không còn kết quả.
- [ ] `make ci` xanh.

## Nguồn

- `docs/products/elmich/surveys/2026-10-05-platform-auth-portal.md` mục 4 (biến thiếu, dòng thừa), mục 1 (`config.ts:11-23`).
- `docs/products/elmich/surveys/2026-10-05-dw-channels.md` gap 2, 10 (`ZALO_*` chỉ có ở compose; `TELEGRAM_BOT_TOKEN` không
  ai đọc).
- `docs/products/elmich/surveys/2026-10-05-synthesis.md` mục 3, ".env".

## Comments

- 5/10/2026: một phần ENV đã nằm trong cây làm việc trước khi ticket được đóng
  (`scripts/init_env.py`, `apps/api/tests/unit/test_init_env.py`, `.env.example` sửa).
  Rà soát cùng ngày: khóa Zalo, `DW_API_PUBLIC_BASE_URL`,
  `DW_WORKER_CHANNEL_DELIVERY_INTERVAL_SECONDS` chuyển thành dòng tắt; `DW_KEYCLOAK_ISSUER`
  bỏ khỏi mẫu (seed đọc `DW_API_OIDC_ISSUER_URL`); `DW_ADMIN_DATABASE_URL`, `DW_DB_HOST`,
  `DW_DB_PORT` thêm thành dòng tắt; compose truyền `DW_API_CORS_ORIGINS` cho api và
  `NEXT_PUBLIC_API_BASE_URL` cho runtime web. Test đối chiếu (bước 6) chưa viết.

- 2026-10-05, quyết định của chủ repo: không dùng `scripts/init_env.py`. `.env` của máy
  dev chép từ `.env` của `codebase` (dùng lại mọi khóa có sẵn), đổi tên dự án compose,
  cổng hạ tầng 2xxxx và cổng app 8200/3200/8210/3210 để chạy song song với `codebase`
  (1xxxx, 8000/3000) và `dw-proterial` (3xxxx, 8300/3300); khóa nào code mới cần thì
  thêm vào `.env`. `init_env.py` và `test_init_env.py` đã xóa, `make bootstrap` quay lại
  cách của nền tảng (chép `.env.example` khi chưa có `.env`). Các bước 4 và tiêu chí về
  `init_env.py` ở trên không còn áp dụng; `.env.example` vẫn liệt kê đủ tên biến.
- 2026-10-05, resolved: `.env.example` lists every variable the services read with this repo's ports; `init_env.py` dropped by the owner (see the comment above). The local CORS origin now follows `DW_PUBLIC_WEB_URL` (no `DW_API_CORS_ORIGINS` needed locally), with a test that goes red on a hard-coded port.
