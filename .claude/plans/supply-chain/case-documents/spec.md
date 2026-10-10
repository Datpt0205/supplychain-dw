# Chứng từ của hồ sơ (lát D)

Area: supply-chain · Nhánh: `main` · Viết: 5/10/2026

Riêng của context. Quyết định: [ADR 0021](../../../../packages/python/dw_supply_chain/docs/adr/0021-e11-case-documents-through-an-object-storage-port.md).

## Mục tiêu

Người làm một bước tải chứng từ của bước đó lên hồ sơ (phát triển hoặc PO), người cùng
workspace có quyền đọc tải xuống được, và không ai ngoài tenant, workspace đó thấy nó.

## Hiện trạng

- Hồ sơ PO không lưu chứng từ nào; area file ghi "Evidence partial", "Document
  Completeness not built".
- Nền tảng có client S3 (SeaweedFS) và `FeedbackAttachmentStoragePort`
  (`dw_platform/application/ports.py:212`); offboarding xóa theo tiền tố
  `feedback/{tenant_id}/`.

## Trong phạm vi

Ticket 01: bảng, port, adapter, route cho cả hai loại hồ sơ, khối chứng từ trên trang
Hồ sơ PO.

## Ngoài phạm vi

- Bước nào bắt buộc chứng từ nào (QE-02); các lát giai đoạn 1 tự đòi chứng từ của
  bước mình.
- Đọc nội dung file bằng mô hình.

## Tiêu chí xong

Ticket 01 `resolved`.

## Danh sách ticket

| #   | Ticket                                                            | Status   | Blocked by                                                        |
| --- | ----------------------------------------------------------------- | -------- | ----------------------------------------------------------------- |
| 01  | [Bảng, port lưu trữ, route chứng từ](issues/01-case-documents.md) | resolved | .claude/plans/supply-chain/port/issues/01-port-dw-supply-chain.md |
