"use client";

import { LockOutlined } from "@ant-design/icons";
import { Typography } from "antd";

/**
 * The one masked value (ui-quality §6): a lock and "Đã ẩn" where a price,
 * a restricted field or a support-grant exclusion would be. Never "0" and
 * never "—", which read as "no price". The sentence saying who can see the
 * value belongs once to the region, not to every cell; the server has
 * already left the value out of the response.
 */
export function MaskedValue() {
  return (
    <Typography.Text>
      <LockOutlined aria-hidden /> Đã ẩn
    </Typography.Text>
  );
}
