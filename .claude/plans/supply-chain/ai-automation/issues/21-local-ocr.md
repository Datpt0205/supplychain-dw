# 21 — OCR cục bộ cho ảnh và PDF quét

Status: resolved
Blocked by: —
Area: supply-chain
Decided: Đạt, 2026-10-10: Docling là đường đọc, EasyOCR (`vi`) là bộ OCR của nó.

## Mục tiêu

Ảnh (PNG/JPEG) và PDF quét (không có lớp chữ) đang là `unreadable` (ADR 0021, sửa đổi AI-02 điểm
2). Đọc chúng bằng OCR chạy trong tiến trình worker, không gửi file cho mô hình, để văn bản đi qua
ĐÚNG đường của PDF có lớp chữ: che số tài khoản (`redact_identifiers`, đọc digest tài khoản trước
khi che với loại `reads_accounts`), rồi một lượt gọi có cấu trúc, rồi kiểm trích dẫn (grounding).
Dòng OCR độ tin thấp được đánh dấu; không gì đến mô hình trước khi che.

## Điều kiện (viết trước khi làm)

1. Đo trước khi dựa vào (failure-modes #4): bộ OCR thật sự cài được, chạy trên một ảnh tiếng Việt
   thật và một trang PDF quét, kết quả so với văn bản gốc.
2. Nếu không cài được hoặc đọc tiếng Việt quá kém: dừng, ghi chính xác lý do ở đây, giữ
   `unreadable`.
3. Ngưỡng "đủ tốt" đặt trước khi đo: trên trang sạch, ≥ 90% từ có dấu tiếng Việt đọc đúng nguyên
   văn; và điểm tin cậy phải phân biệt được dòng sai (một dòng sai dấu mà điểm cao thì "đánh dấu
   độ tin thấp" là trang trí, failure-modes #1). Lý do: grounding chỉ kiểm trích dẫn có trong văn
   bản OCR; văn bản OCR sai thì một giá trị sai vẫn "có trích dẫn", nên chất lượng OCR là một phần
   của bảo đảm, không phải tiện ích.

## Đo (2026-10-10, agent)

**Cài được.** `rapidocr 3.9.2` và `docling 2.128.0` có trong `uv.lock` qua extra `parsers` của
`apps/worker` (kèm `torch 2.14.0+cpu`); `uv sync --all-packages --extra parsers` cài trong 20 s.
`onnxruntime` KHÔNG có trong lock; RapidOCR mặc định cần nó, nên đo bằng `onnxruntime 1.31.0` cài
tạm vào venv (gỡ sau khi đo). Docling chọn RapidOCR (onnxruntime, rồi torch) cho OCR, ánh xạ `vi`
sang bộ nhận dạng PP-OCRv6: cùng mô hình đo dưới đây, nên đo RapidOCR là đo đường OCR của docling.

**Dữ liệu.** Văn bản tiếng Việt thật: "Quy trình Quản lý Cung ứng Elmich" (PDF có lớp chữ của chính
Elmich, trang 1 và 3; không đưa vào repo). Lớp chữ (`pypdf`) là đáp án. Mỗi trang dựng thành ba
ảnh: `clean` (200 dpi), `mild` (xám, xoay 0,5°, JPEG q75: một bản quét tốt), `scan` (150 dpi, xoay
1,2°, mờ, nhiễu muối tiêu, JPEG q55: một bản chụp/quét kém); `mild` và `scan` còn lưu thành PDF chỉ
có ảnh (không lớp chữ: đúng loại đang `unreadable`). Chấm: tỷ lệ từ có dấu tiếng Việt đọc đúng
nguyên văn (177 và 291 từ), tỷ lệ từ đúng, độ giống ký tự.

| Bộ nhận dạng (CPU)                          | Ảnh   | Từ có dấu đúng (tr.1 / tr.3) | Từ đúng     | Dòng điểm thấp | s/trang |
| ------------------------------------------- | ----- | ---------------------------- | ----------- | -------------- | ------- |
| RapidOCR PP-OCRv6 small (mặc định, `vi`)    | clean | 0,36 / 0,29                  | 0,45 / 0,51 | 0 / 0 (< 0,8)  | 4–5     |
|                                             | mild  | 0,36 / 0,28                  | 0,45 / 0,50 | 0 / 1          | 4–5     |
|                                             | scan  | 0,22 / 0,22                  | 0,34 / 0,43 | 3 / 1          | 4       |
| RapidOCR PP-OCRv6 medium                    | clean | 0,36 / 0,29                  | 0,45 / 0,51 | 0 / 0          | 68–75   |
| RapidOCR `latin` PP-OCRv5 mobile            | clean | 0,29 / 0,25                  | 0,37 / 0,49 | 1 / 0          | 5–6     |
| EasyOCR `vi` (KHÔNG có trong lock, so sánh) | clean | 0,97 / 0,97                  | 0,60 / 0,80 | 0 / 8 (< 0,5)  | 28      |
|                                             | mild  | 0,96 / 0,98                  | 0,60 / 0,80 | 2 / 10         | 25–29   |
|                                             | scan  | 0,32 / 0,33                  | 0,20 / 0,22 | 55 / 73        | 18–21   |

(Từ đúng thấp hơn từ có dấu ở EasyOCR vì thứ tự đọc bảng khác lớp chữ, không phải đọc sai chữ.)

**Vì sao RapidOCR không dùng được, chính xác:**

1. **Bộ chữ của mô hình không có chữ tiếng Việt.** Từ điển của PP-OCRv6 small và medium (18 708 ký
   tự) thiếu 88 trên 120 nguyên âm mang thanh (ả ạ ằ ắ … ự ỳ ỹ ỷ ỵ và chữ hoa), và không có dấu
   kết hợp (U+0300/0301/0303/0309/0323) để ghép; `latin` PP-OCRv5 (503 ký tự) thiếu 93. Mô hình
   không thể viết ra "Quản", "ứng", "triển": nó viết "Qun", "ng", "trin" hoặc "Quån", "úng". Không
   chỉnh tham số nào sửa được; phải đổi mô hình.
2. **Điểm tin cậy không bắt được lỗi đó.** Trên trang sạch, mọi dòng điểm ≥ 0,81 (small) và ≥ 0,90
   (medium) trong khi 2/3 từ có dấu sai. "Đánh dấu dòng độ tin thấp" sẽ không đánh dấu dòng nào:
   một kiểm soát chỉ có trên giấy.
3. **Grounding không cứu được.** Trích dẫn được kiểm trên chính văn bản OCR; tên hàng, tên NCC,
   mô tả bị mất chữ vẫn "có trích dẫn", và ô so khớp với BM04/PO (tên, mô tả) sẽ báo lệch giả hoặc
   khớp sai. Số (số lượng, đơn giá) ít bị ảnh hưởng hơn, nhưng một lát chỉ đọc số là một thiết kế
   khác, không phải ticket này.

**Kết luận: dừng, giữ `unreadable`.** Không thêm mã nào: ảnh và PDF quét vẫn `unreadable`
(`InProcessDocumentText` của worker chỉ dùng `InProcessDocumentParser`: PDF lớp chữ, DOCX, XLSX,
EML; không docling), trang vẫn nêu "máy không đọc được, người kiểm bằng mắt". Test có sẵn giữ hành
vi: `test_an_unreadable_file_is_recorded_unreadable_with_no_fields_and_no_call` (ảnh PNG:
`unreadable`, 0 lượt gọi, không trường). ADR 0021 thêm sửa đổi 2026-10-10 (AI-21) ghi phép đo này.
Venv trả về đúng lock sau khi đo (`uv sync --all-packages`).

## Đo lần hai: Docling + EasyOCR (2026-10-10, agent, sau quyết định của Đạt)

`easyocr 1.7.2` vào lock qua extra `parsers` của `dw_knowledge` (worker kéo `dw-knowledge[parsers]`).
Docling 2.128.0 có `EasyOcrOptions(lang=["vi"])`, ảnh vào bằng `InputFormat.IMAGE`, và điểm tin cậy
từng dòng có trên `page.parsed_page.textline_cells` (`generate_parsed_pages=True`). Cùng hai trang,
cùng ba biến thể, cùng cách chấm như trên; trọng số tải một lần ở máy dev (HF cache, `~/.EasyOCR`).

| Đường đọc (CPU 4 nhân)                               | Ảnh           | Từ có dấu đúng (tr.1 / tr.3) | Từ đúng     | Dòng < 0,5     | s/trang |
| ---------------------------------------------------- | ------------- | ---------------------------- | ----------- | -------------- | ------- |
| **Docling + EasyOCR, adapter đã ship**               | clean         | 0,97 / 0,93                  | 0,84 / 0,76 | 1% / 6%        | 72–100¹ |
|                                                      | mild (JPEG)   | 0,98 / 0,97                  | 0,86 / 0,83 | 4% / 6%        | 43–47   |
|                                                      | scan (JPEG)   | 0,28 / 0,29                  | 0,17 / 0,18 | 57% / 63%      | 41–45   |
|                                                      | mild (PDF)    | 0,96 / 0,93                  | 0,84 / 0,77 | 6% / 8%        | 37–42   |
|                                                      | scan (PDF)    | 0,34 / 0,43                  | 0,36 / 0,25 | 37% / 47%      | 39–44   |
| Docling, mô hình bảng tắt                            | clean         | 0,97 / 0,93                  | 0,58 / 0,74 | như trên       | —       |
| Docling, backend PDF mặc định (docling-parse)        | mild (PDF)    | 0,77 / 0,80                  | 0,53 / 0,68 | 13% / 15%      | —       |
| Docling, `confidence_threshold` mặc định 0,5         | clean / mild PDF | 0,97 / 0,66 (tr.3)        | —           | **0 / 0**      | —       |
| EasyOCR đọc dòng rời (đo lần một, để so)             | clean         | 0,97 / 0,97                  | 0,60 / 0,80 | 0 / 8 dòng    | 28      |
|                                                      | scan          | 0,32 / 0,33                  | 0,20 / 0,22 | 46% / 52%      | 18–21   |

¹ hai trang đầu chạy khi máy còn bận; trang sau 37–47 s. Ngưỡng đặt trước: ≥ 90% từ có dấu trên trang
sạch (đạt: 97% / 93%) và điểm tin cậy phân biệt dòng sai (đạt: trang tốt ≤ 9% dòng < 0,5, trang kém ≥ 37%).

Đọc từ bảng, và quyết định theo đó:

1. **Docling + EasyOCR đạt; không cần phương án dự phòng "EasyOCR trực tiếp".** Tiếng Việt do EasyOCR
   (giống hệt đọc rời), Docling thêm thứ tự đọc: từ đúng thứ tự 0,84 so với 0,60 trên trang 1 (bảng
   nhiều cột); tắt mô hình bảng thì về 0,58. Đó là lý do Docling được chọn, và nó có thật.
2. **Docling mặc định bỏ im lặng dòng < 0,5** (`EasyOcrOptions.confidence_threshold`): khi đó bản
   quét kém báo 0 dòng nghi ngờ, và PDF quét tốt mất chữ (0,66) mà không ai biết (failure-modes #0).
   Adapter đặt ngưỡng 0, giữ mọi dòng với điểm; lane quyết.
3. **PDF quét đi qua backend pdfium** (`PyPdfiumDocumentBackend`): backend mặc định đọc cùng bản quét
   tốt chỉ 77–80%; cùng file đó render bằng pdfium rồi EasyOCR đọc 97%, nên lỗi ở backend, không ở
   file. Với pdfium: 96% / 93%.
4. **Ngưỡng `MAX_DOUBTFUL_SHARE` = 30%** dòng < 0,5 (`LOW_CONFIDENCE`) thì `unreadable`: trên mọi
   trang tốt đo được (≤ 9%), dưới mọi trang kém (≥ 37%). Biên phía kém hẹp nhất là PDF quét kém tr.1
   (37%); adapter đã ship chấm đúng cả 10 biến thể (6 đọc, 4 `unreadable`).
5. **Giới hạn:** 5 trang, 300 s mỗi chứng từ (`DW_WORKER_OCR_MAX_PAGES`, `DW_WORKER_OCR_TIMEOUT_SECONDS`):
   chứng từ PI/hóa đơn/UNC 1–3 trang; 5 × ~50 s vừa 300 s. Quá trang thì Docling từ chối trước khi đọc
   (`max_num_pages`); quá giờ thì Docling dừng giữa các trang (`document_timeout`) và adapter coi
   `PARTIAL_SUCCESS` là từ chối: cả hai thành `unreadable`.

## Đã quyết (Đạt, 2026-10-10): Docling + EasyOCR, theo bốn điểm dưới

EasyOCR `vi` đạt ngưỡng trên trang sạch và quét tốt (96–98% từ có dấu), và điểm tin cậy của nó CÓ
phân biệt bản kém (55/120 và 73/141 dòng < 0,5 trên `scan`, nơi chỉ còn 1/3 từ đúng). Nó chưa
trong lock. Dùng nó cần:

1. Thêm `easyocr` (Apache-2.0; kéo `scikit-image`, `python-bidi`, `opencv-python-headless`) vào
   extra `parsers` của worker, và ghim; mô hình (`craft_mlt_25k`, `latin_g2`) tải từ GitHub ở lần
   chạy đầu, nên phải nướng vào image worker (không tải lúc chạy, không mạng ở uat/production).
2. Chi phí: 20–30 s/trang trên CPU này; một PI 2 trang ~1 phút trong lane (lane tuần tự).
3. Ngưỡng: một văn bản có > X% dòng điểm < 0,5 thì `unreadable` (đo trên `scan`: 46–52%); dòng
   điểm thấp nhưng văn bản đạt thì đánh dấu ở trích dẫn (`low_confidence`) và ô dẫn dòng đó thành
   khoảng trống `low_confidence`, không giữ giá trị.
4. Loại chứng từ có tài khoản (`reads_accounts`: PI, hóa đơn, UNC): digest tài khoản đọc từ văn
   bản OCR là một số có thể sai một chữ số; so "tài khoản khác" sẽ báo động giả. Đề xuất: với ảnh,
   không đọc digest (giữ "người kiểm bằng mắt" cho tài khoản), chỉ che.

Khi Đạt chọn: thêm dataset ca OCR (ảnh sạch, quét kém → `unreadable`, chèn lệnh trong ảnh, số tài
khoản trong ảnh bị che trước lượt gọi: mock gateway không thấy số), rồi làm theo các điểm trên.

## Comments

- 2026-10-10 (agent): mở và đóng phần đo trong cùng phiên; số liệu ở trên, script đo không đưa vào
  repo (dữ liệu là tài liệu nội bộ của Elmich, đọc từ máy của Đạt).
- 2026-10-10 (agent, sau quyết định của Đạt): làm theo bốn điểm, đo lần hai ở trên.
    - **Nền tảng (`dw_knowledge`, để đưa lên `codebase` sau):** `OcrPort`, `OcrLine`, `OcrReading`
      trong `ports.py`; adapter `adapters/docling_ocr.py` (`DoclingOcrReader`: Docling + EasyOCR `vi`,
      ngưỡng Docling 0, PDF qua pdfium, `max_num_pages`, `document_timeout`, `artifacts_path` thì tắt
      tải). `docling` và `easyocr` khai ở extra `parsers` của `dw_knowledge` (một chủ phiên bản);
      worker kéo `dw-knowledge[parsers]`. Trước đó `docling` được khai ở worker mà không ai import
      (failure-modes #1); nay adapter đọc nó.
    - **Supply chain:** `DocumentText.recognised` (dòng + điểm); domain `LOW_CONFIDENCE` 0,5,
      `MAX_DOUBTFUL_SHARE` 0,30, `Doubt` (vị trí dòng nghi ngờ trong văn bản đã che, theo nguyên từ),
      `GapReason.LOW_CONFIDENCE`, `ACCOUNTS_UNREAD_FIELD`; lane: quá ngưỡng thì `unreadable` trước
      khi che và gọi, che cả dòng nghi ngờ, trích dẫn trên dòng nghi ngờ thành khoảng trống, loại
      `reads_accounts` từ OCR không đọc digest; audit ghi `ocr`. `payment_check`: `NOT_READ` và phát
      hiện `account_not_read` ("người so bằng mắt"), nhãn web cùng tên.
    - **Worker:** `InProcessDocumentText(ocr=...)`: PDF có lớp chữ đọc từ lớp chữ, chỉ PDF không lớp
      chữ và ảnh qua OCR; `build_ocr_reader` ở `composition.py` (không có docling: local tắt OCR,
      uat/production dừng khi khởi động); settings `ocr_artifacts_path`, `ocr_max_pages` (5),
      `ocr_timeout_seconds` (300); `validate_for_profile` đòi `ocr_artifacts_path` ở uat/production.
      Lane đề xuất danh sách SP (AI-08) không dùng OCR (không có ngưỡng điểm ở lane đó).
    - **Image worker:** bước nướng trọng số (`download_models(... with_easyocr=True,
      easyocr_languages=["vi"])` vào `/app/models/ocr`) và `DW_WORKER_OCR_ARTIFACTS_PATH`.
    - **Dataset** `supply_chain_preparation@1.18.0` (295 ca, routes 1.18.0): `prep-ocr-clean-eval`,
      `prep-ocr-bad-scan-eval`, `prep-ocr-injection-eval`, `pay-ocr-account-unc`; grader đọc
      `ocr_lines` qua `ScriptedOcrReader` và kiểm `accounts_unread`. Sửa ca `case_answer` của AI-06
      chuyển sang 1.19.0.
    - **Còn nợ (Docker để dành cho Đạt):** build image worker (bước nướng trọng số chưa chạy; kiểm
      `/app/models/ocr` có layout, tableformer, `EasyOcr/craft_mlt_25k.pth`, `EasyOcr/latin_g2.pth` và
      worker khởi động với `HF_HUB_OFFLINE=1`); integration lane trích xuất với một ảnh thật trong
      bucket; scan image bằng trivy (image nặng thêm opencv, scikit-image). Ca thực tế chưa đo: ảnh
      chụp điện thoại (nghiêng, bóng), PDF nhiều trang trộn lớp chữ và ảnh (hôm nay chỉ OCR khi
      không có lớp chữ nào), mã vạch đọc từ ảnh (`reads_barcodes`: chữ số sai thì kiểm checksum báo).
