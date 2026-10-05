# 01 — Liên kết Zalo: token dùng một lần, route status/connect/disconnect, lane poll

Status: ready-for-agent
Blocked by: .claude/plans/supply-chain/port/issues/01-port-dw-supply-chain.md, .claude/plans/supply-chain/env/issues/01-env-example-and-init-env.md
Area: supply-chain

## Mục tiêu

Người dùng đã đăng nhập lấy được dòng `/start <token>`, gửi cho bot, và được liên kết;
token chỉ dùng được một lần; `/stop` hoặc nút ngắt gỡ liên kết. Chạy được ở máy cá
nhân không cần host công khai. Ứng viên đưa ngược.

## Việc cần làm

1. **`ZaloBotClient`:** cắt tin thành phần tối đa 1900 ký tự theo xuống dòng, như
   `_split_for_zalo` của repo `dw` cũ (`dw_connectors/adapters/zalo_bot.py:18-48`);
   `send_message` trả id của phần cuối. Không chuyển `send_chat_action` vì chưa ai gọi
   (failure-modes #1). Xóa bot token khỏi mọi thông điệp lỗi và log (SEC-20).
2. **Token dùng một lần:** token thêm `jti`; migration nền tảng (id hex ngẫu nhiên của
   alembic) tạo `platform.channel_link_nonces` (`jti` khóa chính, `user_id` FK
   `platform.users` `ON DELETE CASCADE` có index, `expires_at timestamptz`,
   `used_at timestamptz NULL`), grant cho `dw_app` trong migration, có trong
   `test_privileges.py`. `connect` ghi nonce; `handle_update` tiêu nonce bằng một câu
   điều kiện duy nhất rồi liên kết trong cùng giao dịch; 0 dòng thì từ chối (đã dùng,
   hết hạn, giả). Không đọc rồi mới cập nhật: hai `/start` đồng thời cùng token sẽ
   cùng qua.

    ```sql
    UPDATE platform.channel_link_nonces
       SET used_at = now()
     WHERE jti = :jti AND user_id = :uid AND used_at IS NULL AND expires_at > now()
    RETURNING jti
    ```

3. **Dọn nonce:** dòng hết hạn hơn một ngày bị xóa ở một lane worker có sẵn theo giờ
   (failure-modes #6).
4. **Chỉ hai lệnh** (ADR 0014): `/start <token>` và `/stop`, khớp nguyên văn. Thu
   `_STOP_WORDS` (`zalo_link.py:26`) về `{"/stop"}`; chữ khác ("hủy", "duyệt", "ok")
   chỉ nhận một câu hướng dẫn, không đổi gì.
5. **Tên sản phẩm tiêm vào** câu trả lời của bot (bỏ "Sale Intelligence",
   `zalo_link.py:96`), lấy từ settings của deployment.
6. **Settings:** `ApiSettings` và `WorkerSettings` đọc `ZALO_BOT_TOKEN`,
   `ZALO_LINK_SECRET`, `ZALO_BOT_LINK`, `ZALO_UPDATES_MODE` (mặc định `poll`); bỏ dấu
   `#` của các dòng đã có sẵn (đang tắt) trong `.env.example` cùng thay đổi này, để
   `ZALO_LINK_SECRET` đặt trong `.env` (chuỗi ngẫu nhiên, dùng chung cho api và worker). Không chuyển `ZALO_USER_*_ID`, `ZALO_USER_MAP_JSON`,
   `DW_APPROVAL_CHANNEL`.
7. **Route** `apps/api/src/dw_api/routes/v1/zalo.py` chuyển từ `sales_dw`
   (`routes/v1/zalo.py:58-97`): `GET /zalo/status`, `POST /zalo/connect` (trả
   `{code, deep_link, expires_at}`), `POST /zalo/disconnect`. Đổi `bot=` thành
   `sender=`; không SQL thô (status đi qua `SqlZaloLink.zalo_id_for`); `SqlZaloLink`
   tới route qua container, nối ở `bootstrap/wiring.py`; engine `dw_app`. Sửa docstring
   `zalo_link_repo.py:6-8` theo grant thật. Route chỉ có khi đủ token và link secret;
   thiếu thì 404, và client web thấy "chưa cấu hình".
8. **Lane poll** `zalo_link_poll` trong worker, chuyển từ `sales_dw`
   (`consumers/zalo_poll.py:22-36`): chỉ đăng ký khi `ZALO_UPDATES_MODE=poll` và đủ
   token, secret; một update lỗi được log rồi bỏ qua.

## Tiêu chí chấp nhận

- [ ] Unit: tin 4000 ký tự thành 3 phần, không phần nào quá 1900; lỗi httpx có token
      trong URL ra thông điệp không chứa token.
- [ ] Unit/integration: `/start` với token hợp lệ liên kết; gửi lại đúng token đó lần
      hai bị từ chối và liên kết không đổi; token hết hạn và token giả bị từ chối.
- [ ] **Test âm danh tính:** một dòng `external_identities` với `issuer='zalo'` không
      cho `SqlMembershipLookup.find_access` ra membership nào, kể cả khi `subject`
      trùng `sub` của một người dùng thật.
- [ ] **Test âm route:** người A gọi `disconnect` chỉ gỡ liên kết của A; `status` của A
      không lộ chat id của ai; gọi không có token Bearer trả 401. Liên kết theo người
      dùng, không theo tenant: test ghi rõ rằng `status` ở tenant thứ hai của cùng
      người trả "đã liên kết" là đúng thiết kế (ADR 0012).
- [ ] Không route hay lane nào gọi `access_context_factory` từ dữ liệu Zalo (test
      kiến trúc hoặc grep có trong CI).
- [ ] **Không quyết bằng chữ chat:** chữ tự do, kể cả "duyệt", "đồng ý", "ok",
      "approve <id>", không sinh quyết định approval, không đổi trạng thái nào, nhiều nhất
      một câu hướng dẫn. Một test khẳng định `ApproveAndResumeService.decide` không với
      tới được từ `handle_update` (import-linter hoặc test kiến trúc).
- [ ] "hủy", "huỷ", "stop", "/huy" không gỡ liên kết; chỉ `/stop` gỡ. Mutation: trả lại
      bộ từ dừng cũ thì test đỏ.
- [ ] **Không cấu hình thì không có cửa:** thiếu `ZALO_BOT_TOKEN` hoặc
      `ZALO_LINK_SECRET` thì `status`, `connect`, `disconnect` trả 404 và lane poll không
      đăng ký; mỗi trường hợp một test.
- [ ] **Hai `/start` đồng thời** cùng một token (hai giao dịch song song trên Postgres
      thật): đúng một liên kết, lần kia bị từ chối. Mutation: đổi câu tiêu nonce thành
      đọc rồi cập nhật thì test đỏ.
- [ ] `test_privileges.py` khẳng định grant của `channel_link_nonces`; gỡ grant trong
      migration thì test đỏ.
- [ ] `make ci` xanh; openapi và `platform.d.ts` sinh lại.

## Nguồn

- `docs/products/elmich/surveys/2026-10-05-dw-channels.md` mục 2, gap 1, 2, 4, 5, 7, 8; mục 3 (bảng dùng lại).
- `docs/products/elmich/surveys/2026-10-05-zalo-sales-dw.md` mục 1, 4 (token, lỗ phát lại), 8 (408, `result` là một object),
  10 (sửa trước khi dùng lại).
- `docs/products/elmich/surveys/2026-10-05-platform-auth-portal.md` mục 3 ("Grant bug that blocks porting").

## Comments
