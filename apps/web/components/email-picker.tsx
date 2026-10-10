"use client";

import { AutoComplete, Flex, Typography } from "antd";

export interface EmailOption {
  email: string;
  display_name: string;
}

/**
 * A combobox over signed-in accounts. Not a native <datalist>: that merges the
 * browser's own saved-address autofill into the list, which looks like the app
 * is suggesting random personal emails. This shows only the accounts passed in,
 * and stays typeable — a not-yet-seen email can be entered by hand.
 */
export function EmailPicker({
  id,
  value,
  onChange,
  options,
  placeholder,
  className,
  onOpen,
}: {
  id?: string;
  value: string;
  onChange: (value: string) => void;
  options: EmailOption[];
  placeholder?: string;
  className?: string;
  /** Called when the field is focused, so the caller can refresh its options
   *  (e.g. re-fetch people who signed in since the page loaded). */
  onOpen?: () => void;
}) {
  const q = value.trim().toLowerCase();
  const matches = options
    .filter(
      (o) =>
        o.email &&
        (!q ||
          o.email.toLowerCase().includes(q) ||
          o.display_name.toLowerCase().includes(q)),
    )
    .slice(0, 8);

  return (
    <AutoComplete
      id={id}
      className={className ?? "w-full"}
      value={value}
      onChange={onChange}
      onFocus={onOpen}
      placeholder={placeholder}
      options={matches.map((o) => ({
        value: o.email,
        label: (
          <Flex vertical>
            <Typography.Text>{o.email}</Typography.Text>
            <Typography.Text type="secondary" className="text-xs">
              {o.display_name}
            </Typography.Text>
          </Flex>
        ),
      }))}
    />
  );
}
