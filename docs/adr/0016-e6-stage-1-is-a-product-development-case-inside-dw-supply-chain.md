---
status: Proposed
date: 2026-10-05
source:
    - ../products/elmich/process.md#32-bảng-17-bước # bước 1-9
    - ../products/elmich/process.md#4-đối-chiếu-với-code-và-ticket
    - ../../packages/python/dw_supply_chain/src/dw_supply_chain/domain/po_case.py # CaseState, apply_action
    - ../../configs/policies/supply_chain_action_duties@1.0.0.yaml
---

# E6. Giai đoạn 1 (bước 1–9) là Hồ sơ phát triển sản phẩm, một aggregate riêng trong `dw_supply_chain`

Bước 1–9 của quy trình Elmich chưa có code. Quyết định: chúng là aggregate
`ProductDevelopmentCase` (Hồ sơ phát triển sản phẩm) trong cùng context
`dw_supply_chain`, cạnh `POCase`, theo đúng các mẫu `POCase` đã dùng: dataclass có
method canh điều kiện, enum hành động đóng, một bảng dispatch
`apply_product_action`, mỗi chuyển trạng thái ghi một dòng lịch sử, duty của từng
hành động là policy tenant ghi đè được, approval qua graph HITL có tiền tố nghiêm.

## Trạng thái và hành động

| Từ                      | Hành động (bước)                                                                      | Tới                                 | Ai                                                                             |
| ----------------------- | ------------------------------------------------------------------------------------- | ----------------------------------- | ------------------------------------------------------------------------------ |
| (mới)                   | `propose` (1)                                                                         | `proposed`                          | duty `ordering`; PIC đóng dấu từ người tạo                                     |
| `proposed`              | `request_sample` (2)                                                                  | `sample_requested`                  | `ordering`                                                                     |
| `sample_requested`      | `receive_sample` (2)                                                                  | `sample_testing`                    | `rnd`                                                                          |
| `sample_testing`        | `pass_sample` (3, Đạt)                                                                | `pending_bod_review`                | `rnd`, kèm Biên bản đánh giá mẫu; khởi động run approval bước 6                |
| `sample_testing`        | `request_revision`\* (3, 4)                                                           | `revision_requested`                | `rnd`, kèm Phiếu yêu cầu chỉnh sửa                                             |
| `revision_requested`    | `receive_revised_sample` (5)                                                          | `sample_testing`, vòng mẫu + 1      | `rnd`                                                                          |
| `sample_testing`        | `reject_sample`\* (3, Hủy)                                                            | `cancelled`                         | `rnd`                                                                          |
| `pending_bod_review`    | `bod_approve` / `bod_reject`\* (6)                                                    | `profile_in_progress` / `cancelled` | graph áp sau approval `supply_chain.product_action.bod_review`                 |
| `profile_in_progress`   | `complete_profile` (7)                                                                | `supplier_confirmation`             | `rnd`, kèm BM04                                                                |
| `supplier_confirmation` | `confirm_with_supplier` (8)                                                           | `item_coding`                       | `supply_lead`, kèm email xác nhận của NCC                                      |
| `item_coding`           | `issue_item_code`, `add_sku`, `remove_sku` (9)                                        | `item_coding`                       | `ordering`                                                                     |
| `item_coding`           | `submit_for_signoff` (9)                                                              | `pending_signoff`                   | `ordering`; cần mã hàng chính thức và ít nhất một SKU; khởi động run trình ký  |
| `pending_signoff`       | `signoff_approve` (9, bước cuối)                                                      | `ready_to_order`                    | graph áp sau mọi bước approval `supply_chain.product_action.signoff`           |
| `pending_signoff`       | `signoff_reject`\* (9, bất kỳ bước nào)                                               | `item_coding`                       | graph áp; nhận xét của người không duyệt là lý do; mã hàng, SKU giữ nguyên     |
| `ready_to_order`        | `place_order` (ĐẶT HÀNG)                                                              | `ordered` (kết thúc)                | `ordering`; tạo Hồ sơ PO ([ADR 0017](0017-e7-hand-off-via-order-requested.md)) |
| trạng thái đang chạy    | `wait_for_external`\*, `flag_blocked`\*, `flag_manual_review`\*, `resume`, `cancel`\* | như `POCase`                        | `exceptions`, `ordering`                                                       |

\* bắt buộc lý do. Nhãn tiếng Việt của từng trạng thái ở glossary.

**Approval áp cả hai kết cục.** `bod_approve`, `bod_reject`, `signoff_approve`,
`signoff_reject` không phải nút người dùng bấm: chỉ graph áp chúng, sau quyết định
approval. Khác `advance_case_graph` của Hồ sơ PO, nơi không duyệt thì không áp gì
(`test_advance_case_approval.py::test_rejecting_applies_nothing`): ở đây không duyệt
cũng là một chuyển trạng thái, với nhận xét của người quyết làm lý do. Run do
`pass_sample` (bước 6) và `submit_for_signoff` (bước 9) khởi động trong cùng lệnh;
không có nút "trình" riêng.

**PIC** là `pic_user_id`, NOT NULL, đóng dấu từ `context.principal_id` lúc `propose`,
không bao giờ suy lại (quy tắc PIC của Elmich, process.md mục 3). Đổi PIC là hành
động `reassign_pic` có lý do và audit.

**Duty và vai:** bước của Cung ứng dùng duty `ordering` sẵn có (cùng người làm
bước 10–16); thêm duty `rnd` (vai `sc_rnd`) và `supply_lead` (vai `sc_supply_lead`,
TP Cung ứng). BGĐ và Kế toán không có duty: họ quyết approval, giới hạn bằng
`required_scope` ([ADR 0020](0020-e10-approval-decider-stamped-as-required-scope.md)).

## Vì sao cùng context, và vì sao aggregate riêng

- Cùng ngôn ngữ (NCC, PIC, SKU, Category), cùng đội Cung ứng, và bàn giao ĐẶT HÀNG
  phải là một giao dịch. Context riêng sẽ cần Protocol, outbox cho bàn giao và một
  bản thứ hai của máy duty, approval, SLA.
- Không thêm trạng thái vào `POCase`: danh tính khác (chưa có số PO), vòng đời khác
  (vòng mẫu), và một sản phẩm có thể sinh nhiều PO (QE-12).

## Giả định chờ Elmich

- Vòng chỉnh sửa đi qua `revision_requested` rồi về `sample_testing`; "quay lại bước 2"
  và "lặp lại bước 3" được hiểu là cùng một vòng (QE-07). Không giới hạn số vòng.
- BGĐ không duyệt là hủy (QE-08).
- Bước 8 là xác nhận trong ứng dụng kèm file email, không đọc hộp thư (QE-09).

## Hệ quả

- Bước 12 (sơ đồ con thiết kế màu và bao bì) không thuộc ADR này; nó là một luồng
  con của Hồ sơ PO ở giai đoạn 2, chưa xếp ticket.
- Bảng mới: `product_dev_cases`, `product_dev_case_state_transitions`,
  `product_sample_rounds`, `sample_revision_requests`; mỗi bảng có `tenant_id`,
  `workspace_id`, RLS FORCE, và test âm xuyên tenant, xuyên workspace.
