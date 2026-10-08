"use client";

import { useEffect, useMemo, useRef, useState, type DragEvent } from "react";
import { usePathname } from "next/navigation";
import {
  App,
  Button,
  Flex,
  Input,
  Modal,
  Select,
  Typography,
  theme,
} from "antd";
import {
  CloseOutlined,
  PictureOutlined,
  SendOutlined,
} from "@ant-design/icons";
import { errorMessage } from "../../lib/error-message";
import { apiClient } from "../../lib/session";
import { NAV_ITEMS } from "../../lib/nav/registry";

/**
 * The feedback form (spec 003 US5): exactly four things — which module, what
 * went wrong, what the person would do about it, and screenshots. Screenshots
 * arrive by paste, drag-drop or the file picker; the two questions the
 * reference form asked afterwards ("share samples?", "may we contact you?")
 * are deliberately not here.
 *
 * The limits mirror the server's (five images, 10 MB each, images only) so a
 * refusal is immediate and names the limit; the server checks again.
 */

// Same numbers as dw_platform.application.feedback_dto — one bug rarely needs
// more than a handful of screens, and a 4K PNG is well under 10 MB.
const MAX_IMAGES = 5;
const MAX_IMAGE_BYTES = 10 * 1024 * 1024;
const IMAGE_MIMES = new Set([
  "image/png",
  "image/jpeg",
  "image/gif",
  "image/webp",
]);
const OTHER_MODULE = "Khác";
const TEXT_MAX = 4000;

interface Picked {
  file: File;
  url: string;
}

/** The module whose page the person is on: the longest nav href that prefixes the path. */
function moduleForPath(pathname: string): string {
  let best: { label: string; length: number } | null = null;
  for (const item of NAV_ITEMS) {
    const matches =
      pathname === item.href || pathname.startsWith(item.href + "/");
    if (matches && (!best || item.href.length > best.length)) {
      best = { label: item.label, length: item.href.length };
    }
  }
  return best?.label ?? OTHER_MODULE;
}

function accept(
  files: FileList | File[],
  current: Picked[],
): Picked[] | string {
  const next = [...current];
  for (const file of Array.from(files)) {
    if (!IMAGE_MIMES.has(file.type)) {
      return `"${file.name}" không phải ảnh — chỉ nhận PNG, JPEG, GIF, WebP.`;
    }
    if (file.size > MAX_IMAGE_BYTES) {
      return `"${file.name}" quá ${MAX_IMAGE_BYTES / (1024 * 1024)} MB.`;
    }
    if (next.length >= MAX_IMAGES) {
      return `Tối đa ${MAX_IMAGES} ảnh cho một phản hồi.`;
    }
    next.push({ file, url: URL.createObjectURL(file) });
  }
  return next;
}

export function FeedbackDialog({ onClose }: { onClose: () => void }) {
  const pathname = usePathname();
  const { message } = App.useApp();
  const { token } = theme.useToken();
  const modules = useMemo(
    () => [...new Set(NAV_ITEMS.map((item) => item.label)), OTHER_MODULE],
    [],
  );
  const [module, setModule] = useState(() => moduleForPath(pathname));
  const [text, setText] = useState("");
  const [suggestion, setSuggestion] = useState("");
  const [images, setImages] = useState<Picked[]>([]);
  const [imageError, setImageError] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);

  // Object URLs are revoked when the dialog goes, not per removal: a removed
  // preview may still be painting when its slot is re-rendered.
  useEffect(
    () => () => images.forEach((image) => URL.revokeObjectURL(image.url)),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [],
  );

  function add(files: FileList | File[]) {
    const result = accept(files, images);
    if (typeof result === "string") {
      setImageError(result);
      return;
    }
    setImageError(null);
    setImages(result);
  }

  function onDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    add(event.dataTransfer.files);
  }

  async function submit() {
    const body = text.trim();
    if (!body) return;
    setSending(true);
    try {
      await apiClient().submitFeedback({
        module,
        message: body,
        suggestion: suggestion.trim() || undefined,
        page_path: pathname,
        images: images.map((image) => image.file),
      });
      message.success("Cảm ơn — phản hồi đã tới quản trị viên.");
      onClose();
    } catch (error) {
      message.error(errorMessage(error) || "Không gửi được phản hồi.");
    } finally {
      setSending(false);
    }
  }

  const required = (
    <span aria-hidden style={{ color: token.colorErrorText }}>
      {" "}
      *
    </span>
  );

  return (
    <Modal
      open
      onCancel={onClose}
      title="Gửi phản hồi"
      footer={
        <Flex justify="end" gap="small">
          <Button onClick={onClose} disabled={sending}>
            Hủy
          </Button>
          <Button
            type="primary"
            icon={<SendOutlined aria-hidden />}
            loading={sending}
            disabled={!text.trim()}
            onClick={() => void submit()}
          >
            Gửi
          </Button>
        </Flex>
      }
    >
      <Flex
        vertical
        gap="middle"
        onPaste={(event) => {
          const files = Array.from(event.clipboardData.files);
          if (files.length > 0) {
            event.preventDefault();
            add(files);
          }
        }}
      >
        <Typography.Text type="secondary">
          Bạn gặp lỗi ở đâu, lỗi gì, và bạn muốn nó ra sao — kèm ảnh màn hình
          nếu có.
        </Typography.Text>
        <Flex vertical gap={4}>
          <label htmlFor="feedback-module">Module đang gặp lỗi{required}</label>
          <Select
            id="feedback-module"
            value={module}
            onChange={setModule}
            options={modules.map((item) => ({ value: item, label: item }))}
          />
        </Flex>
        <Flex vertical gap={4}>
          <label htmlFor="feedback-message">Mô tả lỗi{required}</label>
          <Input.TextArea
            id="feedback-message"
            rows={4}
            maxLength={TEXT_MAX}
            value={text}
            onChange={(event) => setText(event.target.value)}
            placeholder="Bạn làm gì, chuyện gì xảy ra, bạn mong đợi gì?"
          />
        </Flex>
        <Flex vertical gap={4}>
          <label htmlFor="feedback-suggestion">
            Đề xuất giải pháp / Khuyến nghị
          </label>
          <Input.TextArea
            id="feedback-suggestion"
            rows={2}
            maxLength={TEXT_MAX}
            value={suggestion}
            onChange={(event) => setSuggestion(event.target.value)}
            placeholder="Không bắt buộc"
          />
        </Flex>
        <Flex vertical gap={4}>
          <span>Ảnh đính kèm</span>
          <div
            role="group"
            aria-label="Ảnh đính kèm"
            onDragOver={(event) => event.preventDefault()}
            onDrop={onDrop}
            className="border border-dashed p-3"
            style={{
              borderColor: token.colorBorder,
              borderRadius: token.borderRadiusLG,
            }}
          >
            {images.length > 0 && (
              <ul className="m-0 mb-3 flex list-none flex-wrap gap-2 p-0">
                {images.map((image, index) => (
                  <li key={image.url} className="relative">
                    {/* eslint-disable-next-line @next/next/no-img-element */}
                    <img
                      src={image.url}
                      alt={image.file.name}
                      className="size-20 object-cover"
                      style={{ borderRadius: token.borderRadius }}
                    />
                    <Button
                      size="small"
                      shape="circle"
                      icon={<CloseOutlined aria-hidden />}
                      aria-label={`Xoá ${image.file.name}`}
                      onClick={() =>
                        setImages((previous) =>
                          previous.filter((_, at) => at !== index),
                        )
                      }
                      className="!absolute -right-2 -top-2"
                    />
                  </li>
                ))}
              </ul>
            )}
            <Flex wrap align="center" gap="small">
              <Button
                size="small"
                icon={<PictureOutlined aria-hidden />}
                onClick={() => fileInput.current?.click()}
              >
                Chọn ảnh
              </Button>
              <Typography.Text type="secondary" className="text-xs">
                hoặc kéo-thả / dán ảnh (Ctrl+V) vào đây.
              </Typography.Text>
            </Flex>
            <input
              ref={fileInput}
              type="file"
              accept="image/*"
              multiple
              hidden
              onChange={(event) => {
                if (event.target.files) add(event.target.files);
                event.target.value = "";
              }}
            />
          </div>
          {imageError && (
            <Typography.Text type="danger" className="text-xs">
              {imageError}
            </Typography.Text>
          )}
        </Flex>
      </Flex>
    </Modal>
  );
}
