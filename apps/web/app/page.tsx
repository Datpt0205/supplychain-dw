"use client";

import Link from "next/link";
import { Card, Flex, Typography } from "antd";
import { HomeOutlined } from "@ant-design/icons";
import { PageHeader, RegionState } from "@dw/ui";
import { useAuth } from "../lib/auth/auth-context";
import { NAV_ITEMS } from "../lib/nav/registry";
import { isNavItemVisible } from "../lib/nav/visibility";

/**
 * The platform landing page.
 *
 * A bounded context owns its own home screen; this one only points at the
 * platform areas the signed-in person can actually reach, read from the same
 * nav registry the menu renders — so a page added to the registry appears
 * here too, and nothing here can offer a link the menu would hide.
 */
export default function HomePage() {
  const { displayName, active, isPlatformOperator, hasScope, roles } =
    useAuth();

  const destinations = NAV_ITEMS.filter(
    (item) =>
      item.href !== "/" &&
      isNavItemVisible(item, { isPlatformOperator, hasScope, roles }),
  );

  return (
    <div className="mx-auto max-w-5xl">
      <PageHeader
        icon={<HomeOutlined />}
        title={displayName ? `Xin chào, ${displayName}` : "Xin chào"}
        subtitle={
          active
            ? `Bạn đang làm việc trong ${active.workspaceName}. Mọi lượt chạy, quyết định và thao tác ở đây đều thuộc workspace này.`
            : "Hãy chọn một workspace để bắt đầu."
        }
      />
      {destinations.length === 0 && (
        <RegionState
          kind="empty"
          title="Chưa có mục nào cho bạn"
          description="Vai của bạn chưa có quyền ở khu vực nào của workspace này. Hãy nhờ quản trị viên cấp quyền."
        />
      )}
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {destinations.map((item) => {
          const Icon = item.icon;
          return (
            <Link key={item.href} href={item.href} className="block">
              <Card hoverable size="small" className="h-full">
                <Flex vertical gap={6}>
                  <Typography.Text strong>
                    <Icon aria-hidden /> {item.label}
                  </Typography.Text>
                  <Typography.Text type="secondary">
                    {item.hint}
                  </Typography.Text>
                </Flex>
              </Card>
            </Link>
          );
        })}
      </div>
    </div>
  );
}
