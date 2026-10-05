# 02 — Chạy thật trên ba tên miền

Status: ready-for-human
Blocked by: .claude/plans/supply-chain/hosting/issues/01-caddy-overlay-and-runbook.md
Area: supply-chain

## Mục tiêu

Phần của slice H cần DNS, một máy chủ và một trình duyệt: không agent nào tự tick được.

## Việc cần làm

1. Đặt ba bản ghi DNS cho web, api, auth; điền `.env` của máy chủ theo
   `docs/deploy/host.md`.
2. Build ảnh web với `NEXT_PUBLIC_*` của tên miền, rồi:

    ```sh
    docker compose -f docker-compose.yml -f docker-compose.prod.yml -f docker-compose.host.yml --profile full up -d
    ```

3. Lần đầu import realm; kiểm redirect URI của `dw-web` là URL web thật.
4. Đăng nhập qua `https://<auth>` từ trình duyệt, gọi API từ trang web; đăng xuất về
   đúng trang.

## Tiêu chí chấp nhận

- [ ] Bốn bước chạy; ghi tên miền, lệnh và kết quả (gồm `iss` của token và header CORS
      thấy được) vào Comments.
- [ ] `/health` qua `https://<api>`; `/api/docs` không mở ở profile deployed.

## Nguồn

- `docs/products/elmich/surveys/2026-10-05-platform-auth-portal.md` mục 6.

## Comments
