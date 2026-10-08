"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Alert, Button, Card, Flex, Tag, Typography } from "antd";
import { LoginOutlined } from "@ant-design/icons";
import type { DemoUser } from "@dw/contracts";
import { AUTH_MODE } from "../../lib/auth/config";
import { errorMessage } from "../../lib/error-message";
import { roleLabel } from "../../lib/nav/roles";
import { apiClient, loginAsDev } from "../../lib/session";

/** Dev-only one-click login (host `make dev` without Keycloak). */
export default function DevLoginPage() {
  const router = useRouter();
  const [users, setUsers] = useState<DemoUser[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  // Demo persona login exists only in dev-auth builds. In OIDC mode (dev and
  // prod both run OIDC) this page must not render or call the demo API — real
  // sign-in is Keycloak. Bounce to the app, which sends the user to Keycloak.
  useEffect(() => {
    if (AUTH_MODE !== "dev") {
      router.replace("/");
      return;
    }
    apiClient()
      .listDemoUsers()
      .then(setUsers)
      .catch((e: unknown) => setError(errorMessage(e)));
  }, [router]);

  if (AUTH_MODE !== "dev") return null;

  async function pick(subject: string) {
    setBusy(subject);
    try {
      await loginAsDev(subject);
      window.location.href = "/";
    } catch (e) {
      setError(errorMessage(e));
      setBusy(null);
    }
  }

  return (
    <Flex
      vertical
      justify="center"
      gap="large"
      className="mx-auto min-h-screen max-w-2xl p-6"
    >
      <div>
        <Typography.Title level={3} className="!mb-1">
          Đăng nhập (chế độ dev)
        </Typography.Title>
        <Typography.Text type="secondary">
          Chọn một tài khoản demo để vào nhanh.
        </Typography.Text>
      </div>

      {error && <Alert type="error" showIcon title={error} />}

      <div className="grid gap-3 sm:grid-cols-2">
        {users?.map((u) => (
          <Card key={u.subject} size="small">
            <Flex vertical gap="small">
              <div>
                <Typography.Text strong className="block">
                  {u.display_name}
                </Typography.Text>
                <Typography.Text type="secondary" className="text-xs">
                  {u.tenant_name}
                </Typography.Text>
              </div>
              <Flex wrap gap={4}>
                {u.roles.map((r) => (
                  <Tag key={r}>{roleLabel(r)}</Tag>
                ))}
              </Flex>
              <Button
                type="primary"
                icon={<LoginOutlined aria-hidden />}
                loading={busy === u.subject}
                onClick={() => void pick(u.subject)}
              >
                Đăng nhập
              </Button>
            </Flex>
          </Card>
        ))}
      </div>
    </Flex>
  );
}
