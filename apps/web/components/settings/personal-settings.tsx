"use client";

import { useEffect, useState } from "react";
import { Alert, Button, Card, Flex, Skeleton, Tag, Typography } from "antd";
import type { ReactNode } from "react";
import { ReloadOutlined } from "@ant-design/icons";
import type { Me } from "@dw/api-client";
import { PageHeader } from "@dw/ui";
import { useAuth } from "../../lib/auth/auth-context";
import { AUTH_MODE, KEYCLOAK_REALM, KEYCLOAK_URL } from "../../lib/auth/config";
import { errorMessage } from "../../lib/error-message";
import { roleLabels } from "../../lib/nav/roles";
import { apiClient } from "../../lib/session";
import { ZaloConnectCard } from "./zalo-connect-card";
import { ZaloWorkspaceSelect } from "./zalo-workspace-select";

type MeState =
  | { kind: "loading" }
  | { kind: "ready"; me: Me }
  | { kind: "error"; message: string };

/** Keycloak's own account page: where a password or a name is changed. */
const ACCOUNT_URL = `${KEYCLOAK_URL}/realms/${KEYCLOAK_REALM}/account`;

/**
 * "Cài đặt cá nhân": who I am, where I work, and my own Zalo link.
 *
 * Read-only apart from Zalo. Name and email come from the verified sign-in
 * (`/auth/bootstrap`, already loaded by the auth context); roles in the open
 * workspace come from `GET /me`; the workspace list is the viewer's own
 * memberships and nobody else's. Open to every signed-in member, no scope.
 */
export function PersonalSettings() {
  const { displayName, email, memberships, active } = useAuth();
  const [me, setMe] = useState<MeState>({ kind: "loading" });

  const loadMe = () => {
    setMe({ kind: "loading" });
    apiClient()
      .getMe()
      .then((result) => setMe({ kind: "ready", me: result }))
      .catch((error: unknown) =>
        setMe({ kind: "error", message: errorMessage(error) }),
      );
  };

  useEffect(loadMe, []);

  return (
    <Flex vertical gap="large" className="mx-auto w-full max-w-3xl">
      <PageHeader title="Cài đặt cá nhân" />

      <Card title="Hồ sơ">
        {/* A definition list, not antd Descriptions: that one is a <table>, and
            globals.css's phone block still flattens tables into cards. */}
        <dl className="m-0 flex flex-col gap-3">
          <Field label="Họ tên">{displayName || "—"}</Field>
          <Field label="Email">{email ?? "—"}</Field>
          <Field label="Vai trò ở workspace đang mở">
            {me.kind === "loading" ? (
              <Skeleton.Input active size="small" />
            ) : me.kind === "error" ? (
              <Flex wrap gap="small" align="center">
                <Typography.Text type="danger">{me.message}</Typography.Text>
                <Button
                  size="small"
                  icon={<ReloadOutlined aria-hidden />}
                  onClick={loadMe}
                >
                  Thử lại
                </Button>
              </Flex>
            ) : (
              roleLabels(me.me.roles) || "—"
            )}
          </Field>
        </dl>
        {AUTH_MODE === "oidc" && (
          <Typography.Paragraph className="!mb-0 !mt-4">
            Họ tên, email và mật khẩu đổi ở{" "}
            <Typography.Link
              href={ACCOUNT_URL}
              target="_blank"
              rel="noopener noreferrer"
            >
              trang tài khoản đăng nhập
            </Typography.Link>
            .
          </Typography.Paragraph>
        )}
      </Card>

      <Card title="Workspace của bạn">
        {memberships.length === 0 ? (
          <Alert
            type="info"
            showIcon
            message="Bạn chưa thuộc workspace nào. Liên hệ quản trị viên để được thêm vào."
          />
        ) : (
          <ul className="m-0 flex list-none flex-col gap-3 p-0">
            {memberships.map((m) => (
              <li key={m.workspaceId} className="min-w-0">
                <Flex wrap gap="small" align="center">
                  <Typography.Text strong>{m.workspaceName}</Typography.Text>
                  {active?.workspaceId === m.workspaceId && (
                    <Tag color="processing">Đang mở</Tag>
                  )}
                </Flex>
                <Typography.Text>
                  {m.tenantName} · {roleLabels(m.roles) || "—"}
                </Typography.Text>
              </li>
            ))}
          </ul>
        )}
      </Card>

      <ZaloConnectCard>
        <ZaloWorkspaceSelect memberships={memberships} />
      </ZaloConnectCard>
    </Flex>
  );
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="min-w-0">
      <dt>
        <Typography.Text strong>{label}</Typography.Text>
      </dt>
      <dd className="m-0 break-words">{children}</dd>
    </div>
  );
}
