"use client";

import { useCallback, useEffect, useState } from "react";
import {
  Alert,
  App,
  Button,
  Card,
  Flex,
  Input,
  Tag,
  Typography,
  theme,
} from "antd";
import {
  ClusterOutlined,
  EditOutlined,
  InboxOutlined,
  PlusOutlined,
} from "@ant-design/icons";
import type { AdminWorkspace } from "@dw/contracts";
import { PageHeader, RegionState } from "@dw/ui";
import { apiClient } from "../../../lib/session";
import { useAuth } from "../../../lib/auth/auth-context";
import { errorMessage as errorText } from "../../../lib/error-message";
import { slugify } from "../../../lib/slug";

export default function WorkspacesPage() {
  const { hasScope } = useAuth();

  if (!hasScope("platform.workspaces.write")) {
    return (
      <RegionState
        kind="forbidden"
        description="Cần quyền quản lý workspace để xem trang này."
      />
    );
  }
  return <WorkspacesManager />;
}

function WorkspacesManager() {
  const [workspaces, setWorkspaces] = useState<AdminWorkspace[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setError(null);
    try {
      setWorkspaces(await apiClient().listAdminWorkspaces());
    } catch (e) {
      setWorkspaces([]);
      setError(errorText(e));
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  return (
    <div className="mx-auto max-w-4xl">
      <PageHeader
        icon={<ClusterOutlined />}
        title="Workspace"
        subtitle="Các workspace (phòng ban) của công ty."
      />
      <Flex vertical gap="middle">
        {error && <Alert type="error" showIcon title={error} />}
        <CreateWorkspaceCard onCreated={refresh} onError={setError} />
        <WorkspacesCard
          workspaces={workspaces}
          onChanged={refresh}
          onError={setError}
        />
      </Flex>
    </div>
  );
}

function CreateWorkspaceCard({
  onCreated,
  onError,
}: {
  onCreated: () => void;
  onError: (message: string) => void;
}) {
  const [name, setName] = useState("");
  const [slug, setSlug] = useState("");
  const [slugEdited, setSlugEdited] = useState(false);
  const [busy, setBusy] = useState(false);
  const { message } = App.useApp();

  const submit = async () => {
    if (!name.trim() || !slug.trim()) return;
    setBusy(true);
    try {
      await apiClient().createWorkspace({
        name: name.trim(),
        slug: slug.trim(),
      });
      message.success(`Đã tạo workspace “${name.trim()}”.`);
      setName("");
      setSlug("");
      setSlugEdited(false);
      onCreated();
    } catch (e) {
      onError(errorText(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card title="Tạo workspace">
      <Typography.Paragraph type="secondary">
        Mã được sinh tự động từ tên.
      </Typography.Paragraph>
      <Flex wrap align="end" gap="middle">
        <Flex vertical gap={4}>
          <label htmlFor="workspace-name">Tên</label>
          <Input
            id="workspace-name"
            className="w-56"
            placeholder="Vận hành"
            value={name}
            onChange={(e) => {
              setName(e.target.value);
              if (!slugEdited) setSlug(slugify(e.target.value));
            }}
          />
        </Flex>
        <Flex vertical gap={4}>
          <label htmlFor="workspace-slug">Mã</label>
          <Input
            id="workspace-slug"
            className="w-40"
            placeholder="van-hanh"
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
          disabled={!name.trim() || !slug.trim()}
          onClick={() => void submit()}
        >
          Tạo
        </Button>
      </Flex>
    </Card>
  );
}

function WorkspacesCard({
  workspaces,
  onChanged,
  onError,
}: {
  workspaces: AdminWorkspace[] | null;
  onChanged: () => void;
  onError: (message: string) => void;
}) {
  const [renameFor, setRenameFor] = useState<string | null>(null);
  const [newName, setNewName] = useState("");
  const { modal } = App.useApp();
  const { token } = theme.useToken();

  const rename = async (workspaceId: string) => {
    if (!newName.trim()) return;
    try {
      await apiClient().renameWorkspace(workspaceId, newName.trim());
      setRenameFor(null);
      setNewName("");
      onChanged();
    } catch (e) {
      onError(errorText(e));
    }
  };

  const archive = (workspace: AdminWorkspace) => {
    modal.confirm({
      title: `Lưu trữ workspace “${workspace.name}”?`,
      content: "Thành viên sẽ không vào workspace này được nữa.",
      okText: "Lưu trữ",
      okButtonProps: { danger: true },
      cancelText: "Hủy",
      autoFocusButton: "cancel",
      onOk: async () => {
        try {
          await apiClient().archiveWorkspace(workspace.workspace_id);
          onChanged();
        } catch (e) {
          onError(errorText(e));
        }
      },
    });
  };

  return (
    <Card title="Tất cả workspace">
      {workspaces === null ? (
        <RegionState kind="loading" compact />
      ) : workspaces.length === 0 ? (
        <RegionState
          kind="empty"
          compact
          title="Chưa có workspace nào"
          description="Tạo workspace đầu tiên ở trên."
        />
      ) : (
        <ul className="m-0 list-none p-0">
          {workspaces.map((w) => (
            <li
              key={w.workspace_id}
              className="py-3"
              style={{
                borderBottom: `1px solid ${token.colorBorderSecondary}`,
              }}
            >
              <Flex wrap justify="space-between" align="center" gap="small">
                <Flex vertical gap={4} className="min-w-0">
                  <Flex align="center" gap="small">
                    <Typography.Text strong ellipsis>
                      {w.name}
                    </Typography.Text>
                    {w.archived && <Tag>Đã lưu trữ</Tag>}
                  </Flex>
                  <Typography.Text type="secondary" className="text-xs">
                    {w.slug} · {w.member_count} thành viên
                  </Typography.Text>
                </Flex>
                <Flex gap="small">
                  <Button
                    size="small"
                    icon={<EditOutlined aria-hidden />}
                    onClick={() => {
                      setRenameFor(w.workspace_id);
                      setNewName(w.name);
                    }}
                  >
                    Đổi tên
                  </Button>
                  {!w.archived && (
                    <Button
                      size="small"
                      icon={<InboxOutlined aria-hidden />}
                      onClick={() => archive(w)}
                    >
                      Lưu trữ
                    </Button>
                  )}
                </Flex>
              </Flex>
              {renameFor === w.workspace_id && (
                <Flex wrap gap="small" className="mt-2">
                  <Input
                    size="small"
                    className="w-56"
                    aria-label="Tên mới"
                    value={newName}
                    onChange={(e) => setNewName(e.target.value)}
                    placeholder={w.name}
                  />
                  <Button
                    size="small"
                    type="primary"
                    onClick={() => void rename(w.workspace_id)}
                  >
                    Lưu
                  </Button>
                  <Button size="small" onClick={() => setRenameFor(null)}>
                    Hủy
                  </Button>
                </Flex>
              )}
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}
