"use client";

import { useCallback, useEffect, useState } from "react";
import {
  App,
  Button,
  Card,
  Flex,
  Input,
  Select,
  Table,
  Typography,
  theme,
} from "antd";
import {
  DeleteOutlined,
  PlusOutlined,
  UserSwitchOutlined,
} from "@ant-design/icons";
import type { SupportRequest, SupportStaff } from "@dw/api-client";
import { RegionState } from "@dw/ui";
import { formatDateTime } from "../../lib/dates";
import { errorMessage } from "../../lib/error-message";
import { apiClient } from "../../lib/session";
import { EmailPicker, type EmailOption } from "../email-picker";

/**
 * The operators' side of customer-granted support access (ADR 0024): who is
 * on the support team, and the customers' grants waiting for a person. The
 * operator picks only from the support team; the server refuses anyone else
 * (409 `support_staff_required`). No business content or figures are shown.
 */
export function SupportConsole({
  emailOptions,
}: {
  emailOptions: EmailOption[];
}) {
  const [staff, setStaff] = useState<SupportStaff[] | null>(null);
  const [requests, setRequests] = useState<SupportRequest[] | null>(null);
  const { message } = App.useApp();

  const load = useCallback(() => {
    apiClient()
      .listSupportStaff()
      .then(setStaff)
      .catch((e: unknown) => {
        setStaff([]);
        message.error(errorMessage(e));
      });
    apiClient()
      .listSupportRequests()
      .then(setRequests)
      .catch((e: unknown) => {
        setRequests([]);
        message.error(errorMessage(e));
      });
  }, [message]);

  useEffect(load, [load]);

  return (
    <>
      <SupportRequestsCard requests={requests} staff={staff} onChanged={load} />
      <SupportStaffCard
        staff={staff}
        emailOptions={emailOptions}
        onChanged={load}
      />
    </>
  );
}

function SupportRequestsCard({
  requests,
  staff,
  onChanged,
}: {
  requests: SupportRequest[] | null;
  staff: SupportStaff[] | null;
  onChanged: () => void;
}) {
  const { message } = App.useApp();
  const [chosen, setChosen] = useState<Record<string, string>>({});
  const [busyId, setBusyId] = useState<string | null>(null);

  const assign = async (request: SupportRequest) => {
    const staffUserId = chosen[request.grant_id];
    if (!staffUserId) return;
    setBusyId(request.grant_id);
    try {
      const assigned = await apiClient().assignSupportRequest(
        request.grant_id,
        staffUserId,
      );
      message.success(
        `Đã giao ${assigned.code}, hết hạn ${formatDateTime(assigned.expires_at)}.`,
      );
      onChanged();
    } catch (e) {
      message.error(errorMessage(e));
    } finally {
      setBusyId(null);
    }
  };

  const staffOptions = (staff ?? []).map((s) => ({
    value: s.user_id,
    label: s.display_name,
  }));

  return (
    <Card title="Yêu cầu hỗ trợ chờ giao người">
      <Typography.Paragraph type="secondary">
        Quyền khách đã cấp, chờ đội vận hành chọn một nhân viên hỗ trợ. Quyền
        bắt đầu tính giờ từ lúc giao.
      </Typography.Paragraph>
      {requests === null ? (
        <RegionState kind="loading" compact />
      ) : requests.length === 0 ? (
        <RegionState
          kind="empty"
          compact
          title="Không có yêu cầu nào chờ giao"
        />
      ) : (
        <Table<SupportRequest>
          rowKey="grant_id"
          size="small"
          pagination={false}
          scroll={{ x: "max-content" }}
          dataSource={requests}
          columns={[
            {
              title: "Mã",
              dataIndex: "code",
              render: (value: string) => (
                <Typography.Text code>{value}</Typography.Text>
              ),
            },
            {
              title: "Khách",
              key: "customer",
              render: (_, r) => (
                <Flex vertical>
                  <Typography.Text strong>{r.tenant_name}</Typography.Text>
                  <Typography.Text type="secondary" className="text-xs">
                    {r.workspace_name}
                  </Typography.Text>
                </Flex>
              ),
            },
            {
              title: "Phạm vi",
              key: "scope",
              render: (_, r) => (
                <Flex vertical>
                  <Typography.Text>{r.scope_set_label}</Typography.Text>
                  <Typography.Text type="secondary" className="text-xs">
                    {r.resource_label}
                  </Typography.Text>
                </Flex>
              ),
            },
            {
              title: "Thời hạn",
              dataIndex: "duration_hours",
              render: (value: number) => `${value} giờ`,
            },
            { title: "Lý do", dataIndex: "reason", ellipsis: true, width: 240 },
            {
              title: "Gửi lúc",
              dataIndex: "requested_at",
              render: (value: string) => formatDateTime(value),
            },
            {
              title: "Giao cho",
              key: "assign",
              render: (_, r) => (
                <Flex gap="small">
                  <Select
                    className="w-44"
                    aria-label={`Nhân viên hỗ trợ cho ${r.code}`}
                    placeholder="Chọn nhân viên"
                    value={chosen[r.grant_id]}
                    onChange={(value: string) =>
                      setChosen((prev) => ({ ...prev, [r.grant_id]: value }))
                    }
                    options={staffOptions}
                    notFoundContent="Chưa có nhân viên hỗ trợ"
                  />
                  <Button
                    icon={<UserSwitchOutlined aria-hidden />}
                    disabled={!chosen[r.grant_id]}
                    loading={busyId === r.grant_id}
                    onClick={() => void assign(r)}
                  >
                    Giao
                  </Button>
                </Flex>
              ),
            },
          ]}
        />
      )}
    </Card>
  );
}

function SupportStaffCard({
  staff,
  emailOptions,
  onChanged,
}: {
  staff: SupportStaff[] | null;
  emailOptions: EmailOption[];
  onChanged: () => void;
}) {
  const { message, modal } = App.useApp();
  const { token } = theme.useToken();
  const [email, setEmail] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);

  const add = async () => {
    if (!email.trim()) return;
    setBusy(true);
    try {
      const ref = await apiClient().addSupportStaff(
        email.trim(),
        note.trim() || undefined,
      );
      message.success(`${ref.display_name} đã vào đội hỗ trợ.`);
      setEmail("");
      setNote("");
      onChanged();
    } catch (e) {
      message.error(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };

  const remove = (person: SupportStaff) => {
    modal.confirm({
      title: `Gỡ ${person.display_name} khỏi đội hỗ trợ?`,
      content: "Người này sẽ không mở được quyền hỗ trợ nào nữa.",
      okText: "Gỡ",
      okButtonProps: { danger: true },
      cancelText: "Hủy",
      autoFocusButton: "cancel",
      onOk: async () => {
        try {
          await apiClient().removeSupportStaff(person.user_id);
          onChanged();
        } catch (e) {
          message.error(errorMessage(e));
        }
      },
    });
  };

  return (
    <Card title="Nhân viên hỗ trợ">
      <Flex vertical gap="middle">
        <Typography.Text type="secondary">
          Người được giao quyền hỗ trợ của khách. Họ không thể là thành viên của
          công ty khách nào.
        </Typography.Text>
        <Flex wrap align="end" gap="small">
          <Flex vertical gap={4}>
            <label htmlFor="support-staff-email">Email</label>
            <EmailPicker
              id="support-staff-email"
              className="w-64"
              value={email}
              onChange={setEmail}
              options={emailOptions}
              placeholder="Chọn hoặc gõ email…"
            />
          </Flex>
          <Flex vertical gap={4}>
            <label htmlFor="support-staff-note">Ghi chú</label>
            <Input
              id="support-staff-note"
              className="w-56"
              maxLength={200}
              value={note}
              onChange={(e) => setNote(e.target.value)}
              placeholder="Không bắt buộc"
            />
          </Flex>
          <Button
            type="primary"
            icon={<PlusOutlined aria-hidden />}
            loading={busy}
            disabled={!email.trim()}
            onClick={() => void add()}
          >
            Thêm vào đội hỗ trợ
          </Button>
        </Flex>
        {staff === null ? (
          <RegionState kind="loading" compact />
        ) : staff.length === 0 ? (
          <RegionState kind="empty" compact title="Chưa có nhân viên hỗ trợ" />
        ) : (
          <ul className="m-0 list-none p-0">
            {staff.map((s) => (
              <li
                key={s.user_id}
                className="py-2"
                style={{
                  borderBottom: `1px solid ${token.colorBorderSecondary}`,
                }}
              >
                <Flex justify="space-between" align="center" gap="small">
                  <div className="min-w-0">
                    <Typography.Text strong ellipsis className="block">
                      {s.display_name}
                    </Typography.Text>
                    <Typography.Text type="secondary" className="text-xs">
                      {s.email ?? "—"}
                      {s.note ? ` · ${s.note}` : ""}
                    </Typography.Text>
                  </div>
                  <Button
                    type="text"
                    danger
                    icon={<DeleteOutlined aria-hidden />}
                    aria-label={`Gỡ ${s.display_name}`}
                    onClick={() => remove(s)}
                  />
                </Flex>
              </li>
            ))}
          </ul>
        )}
      </Flex>
    </Card>
  );
}
