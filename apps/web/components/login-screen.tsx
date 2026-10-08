"use client";

import { Alert, Button, Card, Flex, Typography, theme } from "antd";
import { LoginOutlined, RobotOutlined } from "@ant-design/icons";
import { useAuth } from "../lib/auth/auth-context";

/**
 * Shown while an unauthenticated visit is being redirected to the Keycloak
 * sign-in page (which carries every configured identity provider). Normally it
 * flashes for a moment; the button is the manual way through if the automatic
 * redirect was blocked.
 */
export function LoginScreen() {
  const { login, error } = useAuth();
  const { token } = theme.useToken();

  return (
    <Flex align="center" justify="center" className="min-h-screen p-6">
      <Card className="w-full max-w-md">
        <Flex vertical align="center" gap="small" className="mb-6 text-center">
          <span
            className="flex size-12 items-center justify-center text-2xl"
            style={{
              background: token.colorPrimary,
              color: token.colorTextLightSolid,
              borderRadius: token.borderRadiusLG,
            }}
          >
            <RobotOutlined aria-hidden />
          </span>
          <Typography.Title level={4} className="!mb-0">
            Digital Worker Platform
          </Typography.Title>
          <Typography.Text type="secondary">
            Đang chuyển tới trang đăng nhập…
          </Typography.Text>
        </Flex>
        {error && (
          <Alert type="error" showIcon title={error} className="mb-4" />
        )}
        <Button
          type="primary"
          size="large"
          block
          icon={<LoginOutlined aria-hidden />}
          onClick={login}
        >
          Đăng nhập
        </Button>
      </Card>
    </Flex>
  );
}
