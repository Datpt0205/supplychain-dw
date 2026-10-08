"use client";

import { useState } from "react";
import { FloatButton } from "antd";
import { MessageOutlined } from "@ant-design/icons";
import { FeedbackDialog } from "./dialog";

/**
 * The feedback entry point (spec 003 US5): a round button pinned to the
 * bottom-left corner of every signed-in page. Not a nav item on purpose —
 * feedback is a utility beside the app, not a module of it — and always in
 * view, so a person reports a bug from the page it happened on.
 */
export function FeedbackLauncher() {
  const [open, setOpen] = useState(false);
  return (
    <>
      <FloatButton
        type="primary"
        icon={<MessageOutlined aria-hidden />}
        aria-label="Gửi phản hồi"
        tooltip="Gửi phản hồi"
        onClick={() => setOpen(true)}
        style={{ insetInlineStart: 20, insetInlineEnd: "auto", bottom: 20 }}
      />
      {open && <FeedbackDialog onClose={() => setOpen(false)} />}
    </>
  );
}
