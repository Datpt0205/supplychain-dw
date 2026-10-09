# AI chuẩn bị từng bước, người duyệt chuyển bước (lát AI-01–AI-20)

Area: supply-chain · Nhánh: `feat/elmich-a-d-s1` · Viết: 9/10/2026

Riêng của context, trừ skill registry (AI-04) và phần chung của trích xuất, bản nháp (ứng
viên upstream, ADR 0011). Quyết định:
[ADR 0025 (E14)](../../../../packages/python/dw_supply_chain/docs/adr/0025-e14-ai-prepares-a-step-a-person-approves-the-move.md)
(AI chuẩn bị, người duyệt),
[ADR 0021 sửa đổi 2026-10-09](../../../../packages/python/dw_supply_chain/docs/adr/0021-e11-case-documents-through-an-object-storage-port.md)
(nội dung file đến mô hình như dữ liệu không tin cậy),
[ADR 0026 (E15)](../../../../packages/python/dw_supply_chain/docs/adr/0026-e15-bm04-and-commercial-data-are-fields.md)
(BM04 và dữ liệu thương mại là trường),
[ADR 0028 (E17)](../../../../packages/python/dw_supply_chain/docs/adr/0028-e17-mkt-is-a-minimal-user-at-step-12.md)
(MKT ở bước 12),
[ADR 0029 (E18)](../../../../packages/python/dw_supply_chain/docs/adr/0029-e18-supplier-messages-drafted-by-ai-sent-by-a-person.md)
(tin gửi NCC). Nạp dữ liệu: `../onboarding/spec.md`; UAT: `../uat/spec.md`.

## Mục tiêu

Hướng của Đạt (9/10/2026): việc gì AI làm được thì AI làm, người chỉ kiểm và duyệt. Ở mỗi
bước 1–17, khi hồ sơ vào bước, AI soạn chứng từ bước cần, đọc file NCC gửi, chạy phép kiểm,
rồi trình "chuyển sang bước X với chứng từ Y"; bước chỉ đổi khi người có duty duyệt (web,
hoặc Zalo bằng mã của cổng). Việc vật lý (test mẫu, trả tiền, đếm hàng) vẫn của người: AI
chuẩn bị hồ sơ, gợi ý đặt cạnh ô kết quả để trống.

## Hiện trạng (kiểm ngày 9/10/2026)

- AI chỉ đọc tin chat: năm prompt một lượt gọi (`configs/prompts/supply_chain/`). Không bản
  nháp chứng từ nào; không code nào gọi docgen; "tờ trình" không là `DocumentType`.
- ADR 0021 cấm nội dung file vào mô hình; chỉ bốn hành động giai đoạn 1 và test trước SX
  đòi chứng từ; `purchase_order`, `deposit_docs`, `payment_docs` không bước nào đòi.
- BM04 là file; Hồ sơ PO không có giá, tiền tệ, điều khoản, ngày giao; cọc và thanh toán
  không có số tiền.
- `build_agent` có mà chưa ai dùng; `configs/tools`, `configs/toolsets` rỗng; runtime không
  có skill; `apps/docgen` là sandbox shell, không phải trình dựng mẫu.
- SLA của Elmich đều `pending_business_confirmation`: không cảnh báo nào bật (QE-04).
- Slide 4, 8 của PoC hứa bản nháp phiếu chỉnh sửa, tờ trình, BM04, email chốt NCC, PO, và
  BM04 đánh dấu ô thiếu: chưa có gì làm.

## Thiết kế

- **Run chuẩn bị** (graph `supply_chain_step_preparation`, worker riêng): đọc hồ sơ → trích
  chứng từ nguồn chưa trích → soạn bản nháp (lượt gọi có cấu trúc, số do code tính, ô thiếu
  là khoảng trống) → phép kiểm (code) → dựng file từ mẫu → approval
  `supply_chain.step_proposal.<action>` (`required_scope` = scope duty của hành động đích,
  đóng dấu) → duyệt: bản nháp thành `case_documents` (`origin = ai_prepared`) và hành động
  được áp trong một giao dịch, actor là người quyết.
- **Kích hoạt:** dòng lịch sử vào trạng thái có trong policy `supply_chain_step_preparation`
  (nền tảng rỗng, Elmich mọi bước); chứng từ tải lên → lane trích xuất; follow-up phía NCC →
  bản nháp nhắc NCC.
- **Mẫu chứng từ** có phiên bản, `TenantOverlay`, dựng bằng một script cố định trong
  sandbox docgen (không script do mô hình viết). Mẫu trung tính trước, mẫu thật của Elmich
  là override sau.
- **Skill** (kiến thức quy trình) qua registry mới của nền tảng, `TenantOverlay`, ghim
  trong release manifest.
- **Trợ lý hồ sơ** (AI-19) là vòng agent duy nhất, chỉ đọc.
- **Mô hình:** `luna` cho soạn và đọc file; Qwen chỉ khi qua cổng eval của tác vụ.

## Luật chung của mọi ticket

- Mô hình đọc và viết; code quyết. Không tool hay node nào của mô hình đổi trạng thái, gửi
  ra ngoài, hay duyệt chứng từ.
- Mỗi trích xuất, mỗi bản nháp có eval: chèn lệnh trong file NCC, chứng từ của tenant hoặc
  workspace khác (từ chối trước lượt gọi), thiếu bằng chứng (ô không trích dẫn là khoảng
  trống), số bịa (tổng khác code thì từ chối); mỗi ca an ninh đỏ khi bỏ guard của nó.
- Mỗi bảng mới: `tenant_id`, `workspace_id`, RLS FORCE hình workspace, grant trong
  migration, test âm xuyên tenant và workspace, `test_rls_coverage.py`,
  `test_privileges.py` xanh.
- Không giá trong Zalo (E15).
- Chạy `reviewing-feature-security` và kiểm đột biến trước khi đóng ticket.
- `process.md` mục 4 cột AI cập nhật khi ticket xong.

## Ngoài phạm vi

- Gửi tin ra ngoài từ hệ thống; đọc hộp thư (E18).
- ERP; Phần B (nội dung marketing).
- AI tự chuyển bước.

## Tiêu chí xong

Ticket 01–20 `resolved`; UAT-02 chạy với Elmich; `process.md` mục 4 cột AI không còn ô
"chưa".

## Danh sách ticket

| #   | Ticket                                                                                     | Size | Status          | Blocked by |
| --- | ------------------------------------------------------------------------------------------ | ---- | --------------- | ---------- |
| 01  | [Dữ liệu thương mại và BM04 là trường](issues/01-commercial-data-and-bm04-fields.md)       | L    | resolved        | —          |
| 02  | [Lane trích xuất chứng từ](issues/02-document-extraction-lane.md)                          | L    | resolved        | —          |
| 03  | [Bản nháp và mẫu chứng từ](issues/03-drafts-and-templates.md)                              | L    | resolved        | —          |
| 04  | [Skill registry](issues/04-skills-registry.md)                                             | M    | resolved        | —          |
| 05  | [Đề xuất bước: chuẩn bị và duyệt để chuyển](issues/05-step-proposals.md)                   | L    | resolved        | 02, 03     |
| 06  | [Eval chuẩn bị và cổng Qwen](issues/06-preparation-evals-and-qwen-gate.md)                 | M    | resolved        | 02, 03     |
| 07  | [Tin gửi NCC: AI soạn, người gửi](issues/07-supplier-messages.md)                          | M    | resolved        | 03, 05     |
| 08  | [Bước 1: đọc danh sách SP đề xuất](issues/08-step-1-proposal-list.md)                      | M    | resolved        | 02, 05     |
| 09  | [Bước 3–5: biên bản, phiếu, kiểm vòng](issues/09-steps-3-5-evaluation-and-revision.md)     | L    | resolved        | 04, 05     |
| 10  | [Bước 6: tờ trình BGĐ](issues/10-step-6-bod-submission.md)                                 | M    | resolved        | 03, 05     |
| 11  | [Bước 7: BM04 điền sẵn, nêu khoảng trống](issues/11-step-7-bm04-prefill.md)                | L    | resolved        | 01, 02, 05 |
| 12  | [Bước 8: email chốt NCC, kiểm thư trả lời](issues/12-step-8-supplier-confirmation.md)      | M    | resolved        | 07, 11     |
| 13  | [Bước 9: đề xuất mã hàng, SKU, kiểm danh mục](issues/13-step-9-item-code-proposal.md)      | M    | resolved        | 11, ON-01  |
| 14  | [Bước 10: PO nháp](issues/14-step-10-purchase-order.md)                                    | M    | resolved        | 01, 12     |
| 15  | [Bước 11, 16: đặt cọc, thanh toán, đối chiếu](issues/15-steps-11-16-deposit-payment.md)    | L    | resolved        | 14         |
| 16  | [Bước 12: MKT tối thiểu, kiểm bản in](issues/16-step-12-mkt-and-proof-check.md)            | L    | ready-for-agent | 04, 11     |
| 17  | [Bước 13–15: file NCC, QC, vận chuyển, đóng cont](issues/17-steps-13-15-supplier-files.md) | L    | ready-for-agent | 05, 14     |
| 18  | [Bước 17: phiếu nhập kho, đối chiếu số đếm](issues/18-step-17-warehouse.md)                | M    | ready-for-agent | 17         |
| 19  | [Trợ lý hồ sơ (agent chỉ đọc)](issues/19-case-assistant.md)                                | L    | ready-for-agent | 02, 04     |
| 20  | [Báo cáo và đo tỷ lệ AI được duyệt](issues/20-reports-and-acceptance.md)                   | M    | ready-for-agent | 05         |

ON-01 là `../onboarding/issues/01-import-suppliers-catalogue-users.md`.
