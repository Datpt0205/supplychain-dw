# 08 — Giai đoạn 1 trong daily brief và command bar

Status: ready-for-agent
Blocked by: .claude/plans/supply-chain/stage-1/issues/07-stage-1-evals.md
Area: supply-chain

Tách từ ticket 07 ngày 7/10/2026 (S7, lead; Đạt ủy quyền): 07 làm phần eval cho các đường
mô hình và cổng code giai đoạn 1 ĐÃ có; ba việc dưới đây là tính năng mới (nhóm brief mới,
prompt mới, truy vấn hồ sơ phát triển), mỗi việc kèm ca eval của nó.

## Mục tiêu

TP Cung ứng nhận báo cáo hằng ngày về mẫu đã test (bước 3) trong daily brief, và hỏi được
về hồ sơ phát triển bằng command bar; mỗi đường mô hình mới có ca eval an ninh đủ ba loại.

## Việc cần làm

1. **Daily brief** (tất định; dạng và kênh chờ QE-19, làm 2 và 3 trước nếu chưa có trả
   lời): nhóm "Mẫu đã đánh giá hôm nay" (đạt, cần chỉnh sửa, hủy, theo PIC), "Chờ BGĐ
   duyệt", "Chờ trình ký", "Quá hạn giai đoạn 1"; policy brief phiên bản mới, thứ tự
   tenant đặt.
2. **Tóm tắt AI của brief:** bộ kiểm câu coi `proposal_code` và tên sản phẩm của các nhóm
   được dẫn là token kiểm được, như số PO và tên NCC.
3. **Command bar:** prompt `case_query_understanding` phiên bản mới hiểu "hồ sơ phát
   triển" (liệt kê theo trạng thái, PIC, Category; mở theo `proposal_code`); code giải kế
   hoạch trên dữ liệu của tenant; từ chối thay vì nới rộng. Z6 dùng lại nguyên (Z6 mục 2).
4. **Tên và ghi chú do người gõ** (`product_name`, `evaluation_note`, `requested_changes`)
   vào prompt nào thì nằm trong `<input>` có escape (như `message_as_data` của prompt đề
   xuất; grader `supply_chain.proposal_prompt_containment` là khuôn).
5. **Dataset** bản kế tiếp của `supply_chain@1.4.0` (giữ mọi ca) thêm ít nhất:
    - prompt injection trong ghi chú đánh giá mẫu, vào tóm tắt brief;
    - xuyên tenant: câu hỏi nêu `proposal_code` chỉ có ở tenant khác ra không giải được,
      không lộ gì; trường tenant chèn trong kế hoạch bị bỏ;
    - thiếu bằng chứng: câu tóm tắt nói "mẫu X đạt" khi không nhóm nào dẫn X thì bị bỏ;
      "mở hồ sơ" không có mã thì từ chối.
6. **Grader:** khóa có sẵn `supply_chain.case_query_plan`,
   `supply_chain.brief_summary_grounding` (nay ở `dw_supply_chain.testing.eval_graders`,
   đăng ký ở `scripts/run_evals.py`); khóa mới chỉ khi có cổng mới.

## Tiêu chí chấp nhận

- [ ] Eval smoke xanh; mỗi ca an ninh đỏ khi gỡ đúng lớp chặn của nó (Comments).
- [ ] Unit brief: nhóm mẫu đúng ngày theo giờ Việt Nam; chỉ hồ sơ của tenant và workspace
      đang xem.
- [ ] **Test âm:** brief của tenant B không chứa hồ sơ của A; câu hỏi ở workspace W2 không
      trả hồ sơ của W1.
- [ ] Chạy thử với model thật (`make check-model`) cho ba câu hỏi mẫu về hồ sơ phát triển;
      ghi kết quả vào Comments.
- [ ] `make ci` xanh.

## Nguồn

- Ticket 07 (bản trước khi tách), mục 1–4 và các ca dataset tương ứng.
- `docs/products/elmich/process.md` mục 3.2, bước 3 ("báo cáo hàng ngày cho Trưởng phòng
  Cung ứng").
- `docs/products/elmich/surveys/2026-10-05-pdf-gap.md` mục 1 "Daily brief", "AI".
- Area file lưu trữ, "Open": `delay_impact_analysis` đưa tên NCC ra ngoài `<input>`.

## Comments

- Báo cáo hằng ngày là một nhóm của brief, không phải tin riêng; dạng và kênh chờ QE-19.
- 2026-10-07 (Z6): phần dùng chung đã có ở Z6, bước 3 của ticket này chỉ mở rộng hiểu
  biết, không dựng đường mới: Zalo gọi đúng `AnswerCaseQuery` (`channel="zalo"`), câu trả
  lời chat do `presentation/zalo_case_query.answer_text` dựng từ `CaseQueryAnswer`. Khi
  bước 3 thêm hồ sơ phát triển vào `CaseQueryAnswer` thì thêm dòng hồ sơ phát triển vào
  `answer_text`, scope đọc hồ sơ phát triển vào `QA_CEILING`, bỏ chữ "hồ sơ phát triển
  sản phẩm chưa hỗ trợ" của `read_only_hint`, và thêm ca `supply_chain.chat_case_answer`
  cho nó. Hôm nay câu hỏi về hồ sơ phát triển đọc thành `unsupported` (model thật,
  Comments Z6).
