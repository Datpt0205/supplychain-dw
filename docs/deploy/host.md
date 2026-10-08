# Triển khai lần đầu trên ba tên máy (overlay Caddy)

Quyết định: [ADR 0023](../adr/0023-e13-hosting-on-separate-hostnames.md). File:
`infra/compose/docker-compose.host.yml`, `infra/caddy/Caddyfile`.

Ba tên máy, mỗi cái một biến: web (`DW_WEB_HOST`), API (`DW_API_HOST`), đăng nhập
(`DW_AUTH_HOST`). Không tên miền nào nằm trong repo. Caddy là dịch vụ duy nhất mở
cổng ra ngoài (80, 443 TCP và UDP); mọi cổng khác chỉ trên `127.0.0.1`.

Mỗi bước dưới có một lệnh kiểm. Bước nào kiểm không ra đúng kết quả thì dừng ở đó.

## 0. Máy

- 2 vCPU / 8 GB là cỡ overlay prod tính cho (`docker-compose.prod.yml`). UAT nhỏ hơn được.
- Docker Engine có Compose v2, repo checkout ở thư mục deploy, `uv` (để chạy script trên
  máy chủ).
- Tường lửa mở **chỉ** 22, 80, 443 (TCP) và 443 (UDP, HTTP/3). Các cổng loopback của
  compose (Postgres, Qdrant, Valkey, S3, Keycloak, API, web) không được mở.
- Linux, không Docker Desktop: Docker Desktop chuyển cổng qua proxy riêng nên mọi
  request tới Caddy mang IP gateway, và giới hạn theo IP của API thành một ô chung
  (đã đo khi chạy thử, xem Comments của ticket hosting/01).

## 1. DNS

Ba bản ghi A (và AAAA nếu máy có IPv6) trỏ về IP máy: `DW_WEB_HOST`, `DW_API_HOST`,
`DW_AUTH_HOST`. Caddy chỉ lấy được chứng chỉ khi cả ba đã trỏ đúng và cổng 80/443 tới
được từ internet.

```bash
for h in app.example.com api.example.com auth.example.com; do dig +short "$h"; done
```

**CDN đứng trước** (Cloudflare v.v.) không phải mặc định. Nếu dùng:

- Thêm `trusted_proxies` với dải IP của CDN vào khối global của Caddyfile, nếu không
  mọi người gọi chung IP của CDN và giới hạn theo IP mất nghĩa.
- Cho user agent "Java" của Zalo đi qua trên host API (ADR 0015), nếu không webhook
  bị 403; không mở được thì ở lại `poll`.

## 2. `.env`

Chép `.env.example` thành `.env`, rồi đặt. Mật khẩu và secret sinh mới, không dùng giá
trị mẫu:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

| Biến                                                                                               | Giá trị                                            | Ghi chú                                                                                                                                                                                         |
| -------------------------------------------------------------------------------------------------- | -------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `DW_WEB_HOST`, `DW_API_HOST`, `DW_AUTH_HOST`                                                       | tên máy, không scheme                              | overlay đòi cả ba                                                                                                                                                                               |
| `DW_CADDY_LOCAL_CERTS`                                                                             | để trống                                           | `local_certs` chỉ để chạy thử không DNS                                                                                                                                                         |
| `POSTGRES_PASSWORD`, `DW_DB_*_PASSWORD`, `MINIO_ROOT_PASSWORD`, `KEYCLOAK_ADMIN_PASSWORD`          | secret mới                                         | `DW_DB_*` chỉ có tác dụng ở lần khởi tạo volume đầu                                                                                                                                             |
| `DW_API_AUTH_MODE`                                                                                 | `oidc`                                             | `dev` bị từ chối khi deployed                                                                                                                                                                   |
| `DW_API_DEV_SECRET`                                                                                | để trống                                           |                                                                                                                                                                                                 |
| `DW_MODEL_PROVIDER`                                                                                | `openai_compatible`                                | `mock` bị từ chối                                                                                                                                                                               |
| `OPENAI_BASE_URL`, `OPENAI_API_KEY`                                                                | gateway và khóa                                    |                                                                                                                                                                                                 |
| `DW_API_MODEL_PROFILE`                                                                             | một file trong `configs/models/`                   | không phải `balanced` (là mock)                                                                                                                                                                 |
| `DW_API_EMBEDDING_PROVIDER`                                                                        | `openai_compatible`                                | `hash` bị từ chối                                                                                                                                                                               |
| `DW_API_RERANK_PROVIDER`, `DW_API_RERANK_BASE_URL`, `DW_API_RERANK_API_KEY`, `DW_API_RERANK_MODEL` | `cohere_compatible` và khóa, hoặc `none`           |                                                                                                                                                                                                 |
| `DW_TASK_CONNECTOR`                                                                                | `none`                                             | `mock` bị từ chối                                                                                                                                                                               |
| `DW_APPROVAL_CODE_SECRET`                                                                          | secret mới, ≥ 32 ký tự, không bao giờ ghi vào repo | api và worker cùng giá trị (compose truyền cả hai); dưới 16 byte thì không dựng được khóa mã duyệt; trống = chỉ duyệt trên web (ADR 0014); đổi khóa thì mọi mã đang hiện (10 phút) hết hiệu lực |
| `ZALO_BOT_TOKEN`, `ZALO_LINK_SECRET`, `ZALO_BOT_LINK`                                              | của bot                                            | trống = không có Zalo                                                                                                                                                                           |
| `ZALO_UPDATES_MODE`                                                                                | `poll` lúc đầu                                     | sang `webhook` ở bước 8                                                                                                                                                                         |
| `ZALO_WEBHOOK_SECRET`                                                                              | secret mới, ≥ 32 ký tự                             | API từ chối khởi động nếu ngắn hơn trong chế độ `webhook`                                                                                                                                       |
| `DW_APPROVAL_REMINDER_SECONDS`                                                                     | SLA thật (ví dụ 1800)                              | 5 là số demo                                                                                                                                                                                    |

**Không** đặt trong `.env` khi dùng overlay host (overlay tự dựng từ ba tên máy):
`KC_HOSTNAME`, `DW_API_OIDC_ISSUER_URL`, `DW_API_CORS_ORIGINS`, `DW_API_PUBLIC_BASE_URL`,
`DW_PUBLIC_WEB_URL`, `NEXT_PUBLIC_API_BASE_URL`, `NEXT_PUBLIC_KEYCLOAK_URL`.

Kiểm cấu hình gộp trước khi dựng:

```bash
C="docker compose --env-file .env -f infra/compose/docker-compose.yml \
  -f infra/compose/docker-compose.prod.yml -f infra/compose/docker-compose.host.yml"
$C --profile full config -q && echo OK
$C --profile full config | grep -E "DW_API_CORS_ORIGINS|DW_API_OIDC_ISSUER_URL|KC_HOSTNAME:"
```

UAT: thay `docker-compose.prod.yml` bằng `docker-compose.uat.yml` trong mọi lệnh.

## 3. Dựng

```bash
$C --profile full up -d --build
$C ps
```

- `migrate` chạy `alembic upgrade head` trước api và worker; vai `sc_*` của
  supply-chain đến từ migration, không có bước seed riêng.
- Image web đóng `NEXT_PUBLIC_*` lúc build: đổi tên máy là `up --build` lại, không phải
  restart.
- Các lần sau: `scripts/deploy.sh <nhánh> production hosted` (hoặc `uat hosted`) dựng
  đúng ba file trên theo thứ tự base, profile, host, rồi chờ `api`, `worker`, `web`,
  `caddy` báo healthy (hỏi theo tên service, không theo tên container).

API từ chối khởi động khi cấu hình deployed sai (`validate_for_profile`): xem
`$C logs api | tail`. Thông báo nêu đúng biến.

## 4. Kiểm sau khi lên

```bash
W=app.example.com A=api.example.com K=auth.example.com
curl -fsS https://$A/api/v1/health                                  # 200
curl -s -o /dev/null -w '%{http_code}\n' https://$A/metrics          # 404: chỉ dw-internal
curl -s -o /dev/null -w '%{http_code}\n' https://$A/api/openapi.json # 404 khi deployed
curl -fsS https://$K/realms/dw/.well-known/openid-configuration | grep -o '"issuer":"[^"]*"'
#   "issuer":"https://auth.example.com/realms/dw"
curl -s -o /dev/null -w '%{http_code}\n' https://$K/admin/           # 404: quản trị không qua host
curl -s -D - -o /dev/null -X OPTIONS -H "Origin: https://$W" \
  -H "Access-Control-Request-Method: GET" https://$A/api/v1/me | grep -i access-control-allow-origin
curl -s -o /dev/null -w '%{http_code}\n' -X OPTIONS -H "Origin: https://evil.example" \
  -H "Access-Control-Request-Method: GET" https://$A/api/v1/me     # 400
curl -sI https://$W/ | grep -iE "strict-transport|^server"           # có HSTS, không có Server
```

Rồi đăng nhập ở `https://$W` bằng một người dùng tạo ở bước 5.

## 5. Keycloak

**Lần đầu** (database `keycloak` rỗng): `--import-realm` nạp `infra/keycloak/dw-realm.json`,
và client `dw-web` lấy redirect URI, web origin và post-logout từ `DW_PUBLIC_WEB_URL`
mà overlay đặt là `https://$DW_WEB_HOST`. Realm import có sẵn vài người dùng demo ở
trạng thái **disabled**; để nguyên hoặc xóa.

**Realm đã có** thì import bỏ qua, không ghi đè. Đổi tên máy web thì sửa tay: Clients →
`dw-web` → Valid redirect URIs `https://<web>/*`, Web origins `https://<web>`, Valid
post logout redirect URIs `https://<web>/*`.

**Trang quản trị** không đi qua Caddy. Mở bằng đường hầm SSH:

```bash
ssh -L 8686:127.0.0.1:${DW_KEYCLOAK_HOST_PORT:-8686} <máy>
# trình duyệt: http://localhost:8686/admin/
```

Cổng cục bộ ở hai đầu phải bằng nhau (`KC_HOSTNAME_ADMIN` là
`http://localhost:${DW_KEYCLOAK_HOST_PORT}`).

Người dùng: quản trị viên tạo trong realm `dw` (không tự đăng ký, ADR 0022). Quên mật
khẩu và xác minh email cần SMTP trong realm; chưa có thì tắt "Verify email" cho người
dùng tạo tay.

## 6. Tenant, người vận hành, vai

Các script chạy trên máy chủ, nối Postgres qua cổng loopback bằng `dw_migrator`:

```bash
export DW_DATABASE_URL="postgresql+asyncpg://dw_migrator:<DW_DB_MIGRATOR_PASSWORD>@127.0.0.1:${DW_POSTGRES_HOST_PORT:-5432}/dw"
```

1. Người vận hành đăng nhập web một lần (tạo danh tính).
2. `uv run python scripts/grant_platform_operator.py --email <email>`
3. Tạo tenant: `POST https://$A/api/v1/platform/tenants` với `slug`, `name`, `plan_id`
   (tạo luôn workspace `main`).
4. Quản trị viên của tenant: `POST /api/v1/platform/tenants/{id}/org-admins`, hoặc
   `uv run python scripts/grant_platform_admin.py --email <email> --tenant-slug <slug>`
   (mặc định `tenant-alpha` là slug demo, luôn truyền slug thật).
5. Membership và vai `sc_*` cho từng người: `/admin/workspaces` trên web hoặc
   `POST /api/v1/admin/members`.

`scripts/seed_supply_chain_demo.py` chỉ chạy với `DW_API_PROFILE=local`; không dùng ở đây.

## 7. Override của Elmich (SLA, đóng gói)

Hai file `scripts/elmich_sla_override.yaml` và `scripts/elmich_packaging_override.yaml`
là dữ liệu của tenant, ghi qua API (kiểm schema, quyền, audit). Lệnh seed của chúng chỉ
chạy ở `local`, nên trên máy chủ gửi thẳng:

```bash
uv run python -c "import json,sys,yaml; print(json.dumps(yaml.safe_load(open(sys.argv[1]))))" \
  scripts/elmich_sla_override.yaml > /tmp/sla.json
curl -fsS -X PUT -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  --data @/tmp/sla.json https://$A/api/v1/supply-chain/sla-policy
# tương tự elmich_packaging_override.yaml -> PUT /api/v1/supply-chain/packaging-policy
```

`$TOKEN` là access token của một người trong tenant Elmich có
`supply_chain.sla_policy.write` (SLA) và `supply_chain.action_duties.write` (đóng gói).
Chưa có màn hình nào cho việc này; lấy token từ header `Authorization` trong tab Network
của trình duyệt sau khi đăng nhập, và xóa `/tmp/*.json` sau đó. Số SLA còn chờ Elmich
xác nhận (ghi trong file).

## 8. Zalo: chuyển từ poll sang webhook

Chạy `poll` cho tới khi tên máy API đã có chứng chỉ (bước 4). Rồi:

1. `.env`: `ZALO_UPDATES_MODE=webhook`, `ZALO_WEBHOOK_SECRET` ≥ 32 ký tự.
2. `$C --profile full up -d api worker` (cả hai đọc cùng chế độ: một bot, một người đọc).
3. Đăng ký với Zalo (script đọc env, không đọc `.env`):

    ```bash
    set -a; . ./.env; set +a
    DW_API_PUBLIC_BASE_URL=https://$DW_API_HOST uv run python scripts/zalo_webhook.py set
    uv run python scripts/zalo_webhook.py info
    ```

4. Liên kết Zalo từ trang cài đặt (`/start` gửi cho bot) và xem `$C logs api worker`.

Webhook chỉ có trên host API (`/api/v1/zalo/webhook`); secret đi trong header
`X-Bot-Api-Secret-Token`, không trong đường dẫn. Caddyfile không ghi access log; nếu bật,
phải lọc header đó (`log { format filter { request>headers>X-Bot-Api-Secret-Token delete } }`).
**Chưa đo** Zalo có gửi header này không (ADR 0015 sửa đổi, ticket zalo-channel/07): mọi
lệnh gọi bị 403 nghĩa là không, khi đó `zalo_webhook.py delete` và quay về `poll`.

## 9. Sao lưu

- **Postgres `dw` và `keycloak`**: một dòng cron chạy `scripts/backup_postgres.sh`, dump
  cả hai (mất `keycloak` là mất mọi người dùng và realm):

    ```cron
    15 2 * * *  cd /home/ubuntu/base_agent && set -a && . ./.env && set +a && scripts/backup_postgres.sh >> /home/ubuntu/pg_backups/backup.log 2>&1
    ```

    `.env` cấp `COMPOSE_PROJECT_NAME`, `MINIO_ROOT_USER`, `MINIO_ROOT_PASSWORD`; thiếu khóa
    S3 thì script dừng trước khi dump. Mỗi database một tên file (`dw_…`, `keycloak_…`),
    xoay vòng riêng, chép sang bucket `dw-pg-backups`. Bucket này nằm trên **cùng máy**:
    chép thêm ra ngoài máy là việc phải làm (ngoài phạm vi lát này).

- **Volume `caddy-data`**: chứng chỉ và tài khoản ACME. Mất thì Caddy xin lại, nhưng CA
  giới hạn số lần cấp lại.
- **Khôi phục**: `scripts/restore_postgres.sh <file>` hoặc `--latest` (`pg_restore --clean`,
  ghi đè); `--latest` chỉ lấy bản mới nhất của database đang khôi phục. Keycloak:
  `PG_DB=keycloak scripts/restore_postgres.sh --latest`. Chạy thử khôi phục một lần trước
  khi có dữ liệu thật.

## Chạy thử không DNS

Đã chạy ngày 7/10/2026 (lệnh và kết quả trong Comments của
`.claude/plans/supply-chain/hosting/issues/01-caddy-overlay-and-runbook.md`):
`.env` thêm ba tên máy `*.example.test` và `DW_CADDY_LOCAL_CERTS=local_certs`, rồi
`curl --resolve <host>:443:127.0.0.1 --cacert <root.crt>` với `root.crt` chép từ
`/data/caddy/pki/authorities/local/root.crt` trong container. Lỗi "failed to install root
certificate ... read-only file system" trong log là bình thường: container chỉ đọc.
