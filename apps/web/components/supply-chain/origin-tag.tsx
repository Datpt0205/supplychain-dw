import { RobotOutlined } from "@ant-design/icons";
import { StatusTag } from "@dw/ui";

const ORIGIN = {
  /** A value a model read from what a supplier sent (the handoff's "Máy đọc"). */
  model_read: {
    label: "Máy đọc",
    tip: "Mô hình đọc từ tin của NCC; người chưa kiểm.",
  },
  /** Text a model wrote. */
  model_written: {
    label: "AI viết",
    tip: "Do mô hình viết; đọc cùng dữ liệu nó dẫn.",
  },
} as const;

/**
 * Says a value came from a model, not a person (ui-quality §7): an outline
 * chip with the robot, distinct from every status tone. It stays after the
 * value is read.
 */
export function OriginTag({ origin }: { origin: keyof typeof ORIGIN }) {
  return (
    <StatusTag
      tone="outline"
      icon={<RobotOutlined aria-hidden />}
      tip={ORIGIN[origin].tip}
    >
      {ORIGIN[origin].label}
    </StatusTag>
  );
}
