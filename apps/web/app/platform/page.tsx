"use client";

import { useCallback, useEffect, useState } from "react";
import {
  App,
  Button,
  Card,
  Flex,
  Input,
  Table,
  Tag,
  Typography,
  theme,
} from "antd";
import {
  BankOutlined,
  DeleteOutlined,
  EditOutlined,
  LockOutlined,
  PlusOutlined,
  SafetyCertificateOutlined,
  UnlockOutlined,
} from "@ant-design/icons";
import type {
  PlatformOperator,
  PlatformTenant,
  PlatformUserRef,
} from "@dw/api-client";
import { PageHeader, RegionState } from "@dw/ui";
import { apiClient } from "../../lib/session";
import { useAuth } from "../../lib/auth/auth-context";
import { errorMessage } from "../../lib/error-message";
import { slugify } from "../../lib/slug";
import { tenantStatus } from "../../lib/tenant-status";
import { EmailPicker, type EmailOption } from "../../components/email-picker";
import { SupportConsole } from "../../components/platform/support-console";

export default function PlatformPage() {
  const { isPlatformOperator } = useAuth();

  if (!isPlatformOperator) {
    return (
      <RegionState
        kind="forbidden"
        title="Chỉ dành cho người vận hành nền tảng"
        description="Khu vực này dành cho người vận hành nền tảng. Hãy nhờ một người vận hành thêm bạn."
      />
    );
  }
  return <PlatformConsole />;
}

function PlatformConsole() {
  const { message } = App.useApp();
  const [tenants, setTenants] = useState<PlatformTenant[] | null>(null);
  const [operators, setOperators] = useState<PlatformOperator[] | null>(null);
  const [users, setUsers] = useState<PlatformUserRef[]>([]);
  const emailOptions: EmailOption[] = users.map((u) => ({
    email: u.email ?? "",
    display_name: u.display_name,
  }));

  const loadTenants = useCallback(() => {
    apiClient()
      .listTenants()
      .then(setTenants)
      .catch((error) => {
        setTenants([]);
        message.error(errorMessage(error));
      });
  }, [message]);
  const loadOperators = useCallback(() => {
    apiClient()
      .listOperators()
      .then(setOperators)
      .catch(() => setOperators([]));
  }, []);

  useEffect(() => {
    loadTenants();
    loadOperators();
    // The email pickers; a failure just leaves them empty, boxes still typeable.
    apiClient()
      .listPlatformUsers()
      .then(setUsers)
      .catch(() => setUsers([]));
  }, [loadTenants, loadOperators]);

  return (
    <div className="mx-auto max-w-5xl">
      <PageHeader
        icon={<BankOutlined />}
        title="Nền tảng"
        subtitle="Công ty, quản trị viên công ty và người vận hành nền tảng. Người vận hành không đọc dữ liệu nghiệp vụ của công ty nào."
      />
      <Flex vertical gap="middle">
        <CreateTenantCard onCreated={loadTenants} />
        <TenantsCard
          tenants={tenants}
          emailOptions={emailOptions}
          onChanged={loadTenants}
        />
        <OperatorsCard
          operators={operators}
          emailOptions={emailOptions}
          onChanged={loadOperators}
        />
        <SupportConsole emailOptions={emailOptions} />
      </Flex>
    </div>
  );
}

function CreateTenantCard({ onCreated }: { onCreated: () => void }) {
  const { message } = App.useApp();
  const [slug, setSlug] = useState("");
  const [slugEdited, setSlugEdited] = useState(false);
  const [name, setName] = useState("");
  // Plans are not enforced yet (basic/pro/enterprise behave the same), so the
  // picker is hidden and every new tenant gets the default plan silently.
  const planId = "professional";
  const [busy, setBusy] = useState(false);

  const submit = async () => {
    if (!slug.trim() || !name.trim()) return;
    setBusy(true);
    try {
      await apiClient().createTenant({
        slug: slug.trim(),
        name: name.trim(),
        planId,
      });
      message.success(`Đã tạo công ty “${name.trim()}”.`);
      setSlug("");
      setName("");
      setSlugEdited(false);
      onCreated();
    } catch (error) {
      message.error(errorMessage(error));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card title="Công ty mới">
      <Typography.Paragraph type="secondary">
        Tạo công ty kèm workspace mặc định “main” và một gói dịch vụ.
      </Typography.Paragraph>
      <Flex wrap align="end" gap="middle">
        <Flex vertical gap={4}>
          <label htmlFor="tenant-name">Tên</label>
          <Input
            id="tenant-name"
            className="w-56"
            placeholder="Công ty ABC"
            value={name}
            onChange={(e) => {
              setName(e.target.value);
              if (!slugEdited) setSlug(slugify(e.target.value));
            }}
          />
        </Flex>
        <Flex vertical gap={4}>
          <label htmlFor="tenant-slug">Mã</label>
          <Input
            id="tenant-slug"
            className="w-40"
            placeholder="abc"
            value={slug}
            onChange={(e) => {
              setSlug(e.target.value);
              setSlugEdited(true);
            }}
          />
        </Flex>
        <Button
          type="primary"
          icon={<PlusOutlined aria-hidden />}
          loading={busy}
          disabled={!slug.trim() || !name.trim()}
          onClick={() => void submit()}
        >
          Tạo
        </Button>
      </Flex>
    </Card>
  );
}

function TenantsCard({
  tenants,
  emailOptions,
  onChanged,
}: {
  tenants: PlatformTenant[] | null;
  emailOptions: EmailOption[];
  onChanged: () => void;
}) {
  const { message, modal } = App.useApp();
  const [assignFor, setAssignFor] = useState<string | null>(null);
  const [email, setEmail] = useState("");
  const [renameFor, setRenameFor] = useState<string | null>(null);
  const [newName, setNewName] = useState("");

  const rename = async (tenantId: string) => {
    if (!newName.trim()) return;
    try {
      const t = await apiClient().renameTenant(tenantId, newName.trim());
      message.success(`Đã đổi tên thành “${t.name}”.`);
      setRenameFor(null);
      setNewName("");
      onChanged();
    } catch (error) {
      message.error(errorMessage(error));
    }
  };

  const toggleLock = (t: PlatformTenant) => {
    const locking = t.status !== "locked";
    modal.confirm({
      title: locking ? `Khóa công ty “${t.name}”?` : `Mở khóa “${t.name}”?`,
      content: locking
        ? "Mọi thành viên của công ty sẽ không vào được tới khi mở khóa."
        : "Thành viên của công ty sẽ vào lại được.",
      okText: locking ? "Khóa" : "Mở khóa",
      okButtonProps: { danger: locking },
      cancelText: "Hủy",
      autoFocusButton: "cancel",
      onOk: async () => {
        try {
          await apiClient().setTenantLocked(t.id, locking);
          message.success(
            locking ? `Đã khóa ${t.name}.` : `Đã mở khóa ${t.name}.`,
          );
          onChanged();
        } catch (error) {
          message.error(errorMessage(error));
        }
      },
    });
  };

  const assign = async (tenantId: string) => {
    if (!email.trim()) return;
    try {
      const ref = await apiClient().assignOrgAdmin(tenantId, email.trim());
      message.success(`${ref.display_name} đã là quản trị viên công ty.`);
      setAssignFor(null);
      setEmail("");
      onChanged();
    } catch (error) {
      message.error(errorMessage(error));
    }
  };

  return (
    <Card title="Công ty">
      {tenants === null ? (
        <RegionState kind="loading" compact />
      ) : tenants.length === 0 ? (
        <RegionState
          kind="empty"
          compact
          title="Chưa có công ty nào"
          description="Tạo công ty đầu tiên ở trên."
        />
      ) : (
        <Table<PlatformTenant>
          rowKey="id"
          size="small"
          pagination={false}
          scroll={{ x: "max-content" }}
          dataSource={tenants}
          columns={[
            {
              title: "Công ty",
              key: "company",
              render: (_, t) => (
                <Flex vertical gap={4}>
                  <Typography.Text strong>{t.name}</Typography.Text>
                  <Typography.Text type="secondary" className="text-xs">
                    {t.slug}
                  </Typography.Text>
                  {assignFor === t.id && (
                    <Flex wrap gap="small">
                      <EmailPicker
                        className="w-56"
                        value={email}
                        onChange={setEmail}
                        options={emailOptions}
                        placeholder="Chọn hoặc gõ email…"
                      />
                      <Button
                        size="small"
                        type="primary"
                        onClick={() => void assign(t.id)}
                      >
                        Giao
                      </Button>
                      <Button size="small" onClick={() => setAssignFor(null)}>
                        Hủy
                      </Button>
                    </Flex>
                  )}
                  {renameFor === t.id && (
                    <Flex wrap gap="small">
                      <Input
                        size="small"
                        className="w-56"
                        aria-label="Tên mới"
                        value={newName}
                        onChange={(e) => setNewName(e.target.value)}
                        placeholder={t.name}
                      />
                      <Button
                        size="small"
                        type="primary"
                        onClick={() => void rename(t.id)}
                      >
                        Lưu
                      </Button>
                      <Button size="small" onClick={() => setRenameFor(null)}>
                        Hủy
                      </Button>
                    </Flex>
                  )}
                </Flex>
              ),
            },
            {
              title: "Workspace",
              dataIndex: "workspace_count",
              align: "right",
            },
            { title: "Thành viên", dataIndex: "member_count", align: "right" },
            {
              title: "Trạng thái",
              dataIndex: "status",
              render: (value: string) => (
                <Tag color={tenantStatus(value).color}>
                  {tenantStatus(value).label}
                </Tag>
              ),
            },
            {
              title: "Thao tác",
              key: "actions",
              align: "right",
              render: (_, t) => (
                <Flex gap="small" justify="end" wrap>
                  <Button
                    size="small"
                    icon={<SafetyCertificateOutlined aria-hidden />}
                    onClick={() => {
                      setAssignFor(t.id);
                      setEmail("");
                    }}
                  >
                    Quản trị viên
                  </Button>
                  <Button
                    size="small"
                    icon={<EditOutlined aria-hidden />}
                    onClick={() => {
                      setRenameFor(t.id);
                      setNewName(t.name);
                    }}
                  >
                    Đổi tên
                  </Button>
                  <Button
                    size="small"
                    icon={
                      t.status === "locked" ? (
                        <UnlockOutlined aria-hidden />
                      ) : (
                        <LockOutlined aria-hidden />
                      )
                    }
                    onClick={() => toggleLock(t)}
                  >
                    {t.status === "locked" ? "Mở khóa" : "Khóa"}
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

function OperatorsCard({
  operators,
  emailOptions,
  onChanged,
}: {
  operators: PlatformOperator[] | null;
  emailOptions: EmailOption[];
  onChanged: () => void;
}) {
  const { principalId } = useAuth();
  const { message, modal } = App.useApp();
  const { token } = theme.useToken();
  const [email, setEmail] = useState("");
  const [busy, setBusy] = useState(false);

  const add = async () => {
    if (!email.trim()) return;
    setBusy(true);
    try {
      const ref = await apiClient().addOperator(email.trim());
      message.success(`${ref.display_name} đã là người vận hành nền tảng.`);
      setEmail("");
      onChanged();
    } catch (error) {
      message.error(errorMessage(error));
    } finally {
      setBusy(false);
    }
  };

  const remove = (operator: PlatformOperator) => {
    modal.confirm({
      title: `Gỡ ${operator.display_name} khỏi người vận hành?`,
      okText: "Gỡ",
      okButtonProps: { danger: true },
      cancelText: "Hủy",
      autoFocusButton: "cancel",
      onOk: async () => {
        try {
          await apiClient().removeOperator(operator.user_id);
          message.success("Đã gỡ người vận hành.");
          onChanged();
        } catch (error) {
          message.error(errorMessage(error));
        }
      },
    });
  };

  return (
    <Card title="Người vận hành nền tảng">
      <Flex vertical gap="middle">
        <Typography.Text type="secondary">
          Ai được tạo và quản lý công ty trên nền tảng.
        </Typography.Text>
        <Flex wrap align="end" gap="small">
          <Flex vertical gap={4}>
            <label htmlFor="operator-email">Email</label>
            <EmailPicker
              id="operator-email"
              className="w-64"
              value={email}
              onChange={setEmail}
              options={emailOptions}
              placeholder="Chọn hoặc gõ email…"
            />
          </Flex>
          <Button
            type="primary"
            icon={<PlusOutlined aria-hidden />}
            loading={busy}
            disabled={!email.trim()}
            onClick={() => void add()}
          >
            Thêm người vận hành
          </Button>
        </Flex>
        {operators === null ? (
          <RegionState kind="loading" compact />
        ) : operators.length === 0 ? (
          <RegionState kind="empty" compact title="Chưa có người vận hành" />
        ) : (
          <ul className="m-0 list-none p-0">
            {operators.map((o) => (
              <li
                key={o.user_id}
                className="py-2"
                style={{
                  borderBottom: `1px solid ${token.colorBorderSecondary}`,
                }}
              >
                <Flex justify="space-between" align="center" gap="small">
                  <div className="min-w-0">
                    <Typography.Text strong ellipsis className="block">
                      {o.display_name}
                    </Typography.Text>
                    <Typography.Text type="secondary" className="text-xs">
                      {o.email ?? o.user_id}
                    </Typography.Text>
                  </div>
                  {/* An operator cannot remove themselves: the last one would
                      lock everybody out of provisioning. */}
                  {o.user_id !== principalId && (
                    <Button
                      type="text"
                      danger
                      icon={<DeleteOutlined aria-hidden />}
                      aria-label={`Gỡ ${o.display_name}`}
                      onClick={() => remove(o)}
                    />
                  )}
                </Flex>
              </li>
            ))}
          </ul>
        )}
      </Flex>
    </Card>
  );
}
