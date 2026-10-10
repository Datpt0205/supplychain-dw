"use client";

import { useCallback, useEffect, useState } from "react";
import { Alert, App, Button, Flex, Select, Skeleton, Typography } from "antd";
import { ReloadOutlined } from "@ant-design/icons";
import type { Membership } from "../../lib/auth/auth-context";
import { errorCode, errorMessage } from "../../lib/error-message";
import { useOnline } from "../../lib/hooks/use-online";
import { apiClient } from "../../lib/session";

const LABEL = "Workspace dùng cho Zalo";
const LABEL_ID = "zalo-workspace-label";

type State =
  | { kind: "loading" }
  | { kind: "hidden" }
  | { kind: "error"; message: string }
  | { kind: "ready"; value: string | null };

function key(tenantId: string, workspaceId: string): string {
  return `${tenantId}:${workspaceId}`;
}

/**
 * Which workspace the viewer's Zalo commands act in, when they have several.
 *
 * The options are the viewer's own memberships (the auth context's list, the
 * same one "Workspace của bạn" shows). The server decides: it saves a choice
 * only when the pair is one of the caller's own memberships, and answers 404
 * otherwise — and 404 from GET means Zalo is not configured, so the field
 * hides. With one membership there is nothing to choose and nothing renders:
 * the bot uses that workspace.
 */
export function ZaloWorkspaceSelect({
  memberships,
}: {
  memberships: Membership[];
}) {
  const { message } = App.useApp();
  const online = useOnline();
  const several = memberships.length > 1;
  const [state, setState] = useState<State>({ kind: "loading" });
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setState({ kind: "loading" });
    try {
      const chosen = await apiClient().getZaloWorkspace();
      setState({
        kind: "ready",
        value:
          chosen.tenant_id && chosen.workspace_id
            ? key(chosen.tenant_id, chosen.workspace_id)
            : null,
      });
    } catch (error) {
      if (errorCode(error) === "not_found") setState({ kind: "hidden" });
      else setState({ kind: "error", message: errorMessage(error) });
    }
  }, []);

  useEffect(() => {
    if (several) void load();
  }, [several, load]);

  if (!several || state.kind === "hidden") return null;

  const choose = async (value: string) => {
    const picked = memberships.find(
      (m) => key(m.tenantId, m.workspaceId) === value,
    );
    if (!picked) return;
    setSaving(true);
    setSaveError(null);
    try {
      // Shown as chosen only once the server has kept it.
      await apiClient().setZaloWorkspace(picked.tenantId, picked.workspaceId);
      setState({ kind: "ready", value });
      void message.success("Đã lưu workspace dùng cho Zalo.");
    } catch (error) {
      setSaveError(errorMessage(error));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Flex vertical gap="small">
      <Typography.Text strong id={LABEL_ID}>
        {LABEL}
      </Typography.Text>
      {state.kind === "loading" && <Skeleton.Input active block />}
      {state.kind === "error" && (
        <Alert
          type="error"
          showIcon
          message={state.message}
          action={
            <Button
              size="small"
              icon={<ReloadOutlined aria-hidden />}
              onClick={() => void load()}
            >
              Thử lại
            </Button>
          }
        />
      )}
      {state.kind === "ready" && (
        <>
          <Select
            aria-labelledby={LABEL_ID}
            className="w-full"
            value={state.value ?? undefined}
            placeholder="Chưa chọn"
            loading={saving}
            disabled={saving || !online}
            onChange={(value: string) => void choose(value)}
            options={memberships.map((m) => ({
              value: key(m.tenantId, m.workspaceId),
              label: `${m.workspaceName} · ${m.tenantName}`,
            }))}
          />
          <Typography.Text>
            {state.value === null
              ? "Bạn thuộc nhiều workspace: chọn một để gửi lệnh qua Zalo. Chưa chọn thì bot sẽ gửi lại đường dẫn tới trang này."
              : "Lệnh gửi qua Zalo làm việc trong workspace này."}
          </Typography.Text>
          {saveError && <Alert type="error" showIcon message={saveError} />}
        </>
      )}
    </Flex>
  );
}
