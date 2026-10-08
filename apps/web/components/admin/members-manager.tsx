"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Alert,
  App,
  Button,
  Card,
  Checkbox,
  Flex,
  Select,
  Tag,
  Typography,
  theme,
} from "antd";
import {
  DeleteOutlined,
  SafetyCertificateOutlined,
  SaveOutlined,
  UserAddOutlined,
} from "@ant-design/icons";
import type { PlatformUserRef, WorkspaceMember } from "@dw/api-client";
import type { AdminPermissionSet } from "@dw/contracts";
import { RegionState } from "@dw/ui";
import { apiClient } from "../../lib/session";
import { useAuth } from "../../lib/auth/auth-context";
import { errorMessage } from "../../lib/error-message";
import { roleLabel } from "../../lib/nav/roles";
import { EmailPicker } from "../email-picker";

// Business roles anyone who manages members can hand out. PLUG-IN POINT: a
// bounded context's own tiers join this list once they are in the tenant role
// catalog — the API refuses a key the catalog does not carry.
const BASE_GRANTABLE_ROLES = ["member", "approver"];
// Administrative roles: the API refuses them from a non-platform-admin, so they
// are offered only to a platform admin (see `grantableRoles`). Listing them also
// lets an existing admin member's row show its real role instead of falling
// back to the first business role.
const ADMIN_GRANTABLE_ROLES = ["org_admin", "platform_admin"];

export function MembersManager() {
  const { active, roles } = useAuth();
  const { message, modal } = App.useApp();
  const { token } = theme.useToken();
  const workspaceId = active?.workspaceId ?? null;
  // Only the platform_admin ROLE may mint an admin role — the grant handler's
  // _forbid_escalation lets exactly that role through and refuses everyone else,
  // so the UI offers the admin options to the same set and no one it would 403.
  const canGrantAdmin = roles.includes("platform_admin");
  const grantableRoles = useMemo(
    () =>
      [
        ...BASE_GRANTABLE_ROLES,
        ...(canGrantAdmin ? ADMIN_GRANTABLE_ROLES : []),
      ].map((key) => ({ value: key, label: roleLabel(key) })),
    [canGrantAdmin],
  );

  const [members, setMembers] = useState<WorkspaceMember[] | null>(null);
  const [candidates, setCandidates] = useState<PlatformUserRef[]>([]);
  // The picker is a convenience (the email box works without it), so a load
  // failure shows a small note here rather than being swallowed silently.
  const [candidatesError, setCandidatesError] = useState<string | null>(null);
  const [permissionSets, setPermissionSets] = useState<AdminPermissionSet[]>(
    [],
  );
  const [email, setEmail] = useState("");
  const [role, setRole] = useState(BASE_GRANTABLE_ROLES[0]!);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Per-row role edits, keyed by user id; a row falls back to its stored role
  // until it is touched.
  const [roleEdits, setRoleEdits] = useState<Record<string, string>>({});
  const [savingId, setSavingId] = useState<string | null>(null);
  // Per-row permission-set edits, keyed by user id.
  const [setEdits, setSetEdits] = useState<Record<string, string[]>>({});
  const [savingSetsId, setSavingSetsId] = useState<string | null>(null);
  // Permission sets are the exception, not the rule, so the editor stays folded
  // per member and opens only when an admin asks for it. One open at a time.
  const [expandedSetsId, setExpandedSetsId] = useState<string | null>(null);

  // Re-read the pick list on its own so it can refresh when the picker opens:
  // somebody who signs in after this page loaded must appear without a reload.
  const loadCandidates = useCallback(async () => {
    try {
      setCandidates(await apiClient().listGrantCandidates());
      setCandidatesError(null);
    } catch (e) {
      setCandidates([]);
      setCandidatesError(`${errorMessage(e)} Gõ email để cấp trực tiếp.`);
    }
  }, []);

  const refresh = useCallback(async () => {
    try {
      setMembers(await apiClient().listWorkspaceMembers());
      // Clear pending per-row edits so freshly loaded members show stored state.
      setSetEdits({});
      setRoleEdits({});
      await loadCandidates();
      // The permission-set catalog is optional decoration; a failure here must
      // not blank the roster.
      try {
        setPermissionSets(await apiClient().listPermissionSets());
      } catch {
        setPermissionSets([]);
      }
    } catch (e) {
      setMembers([]);
      setError(errorMessage(e));
    }
  }, [loadCandidates]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  async function grant() {
    if (!workspaceId || !email.trim()) return;
    setBusy(true);
    setError(null);
    try {
      await apiClient().grantMember({
        email: email.trim(),
        workspaceId,
        roleKeys: [role],
      });
      message.success(`Đã cấp quyền cho ${email.trim()}.`);
      setEmail("");
      await refresh();
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  function currentRole(m: WorkspaceMember): string {
    const edited = roleEdits[m.user_id];
    if (edited !== undefined) return edited;
    const known = m.role_keys.find((k) =>
      grantableRoles.some((r) => r.value === k),
    );
    return known ?? grantableRoles[0]!.value;
  }

  // grant is an upsert, so re-granting with a single role replaces the member's
  // role. Department is passed back so a role change does not reset it.
  async function saveRole(m: WorkspaceMember) {
    if (!workspaceId || !m.email) return;
    setSavingId(m.user_id);
    setError(null);
    try {
      await apiClient().grantMember({
        email: m.email,
        workspaceId,
        roleKeys: [currentRole(m)],
        department: m.department,
      });
      await refresh();
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setSavingId(null);
    }
  }

  function checkedSets(m: WorkspaceMember): string[] {
    return setEdits[m.user_id] ?? m.permission_set_keys;
  }

  async function savePermissionSets(m: WorkspaceMember) {
    setSavingSetsId(m.user_id);
    setError(null);
    try {
      await apiClient().setMemberPermissionSets(m.user_id, checkedSets(m));
      await refresh();
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setSavingSetsId(null);
    }
  }

  function revoke(m: WorkspaceMember) {
    if (!workspaceId) return;
    modal.confirm({
      title: `Gỡ ${m.display_name} khỏi workspace?`,
      content: "Người này sẽ mất mọi vai trong workspace hiện tại.",
      okText: "Gỡ",
      okButtonProps: { danger: true },
      cancelText: "Hủy",
      autoFocusButton: "cancel",
      onOk: async () => {
        setError(null);
        try {
          await apiClient().revokeMember(m.user_id, workspaceId);
          await refresh();
        } catch (e) {
          setError(errorMessage(e));
        }
      },
    });
  }

  return (
    <Card title="Thành viên">
      <Flex vertical gap="middle">
        <Typography.Text type="secondary">
          Cấp quyền cho người đã đăng nhập ít nhất một lần. Người mới phải tự
          đăng nhập trước khi được giao vai.
        </Typography.Text>
        <Flex wrap align="end" gap="small">
          <Flex vertical gap={4} className="min-w-[12rem] flex-1">
            <label htmlFor="grant-email">Email</label>
            <EmailPicker
              id="grant-email"
              value={email}
              onChange={setEmail}
              onOpen={() => void loadCandidates()}
              options={candidates.map((c) => ({
                email: c.email ?? "",
                display_name: c.display_name,
              }))}
              placeholder="Chọn hoặc gõ email…"
            />
            {candidatesError && (
              <Typography.Text type="secondary" className="text-xs">
                {candidatesError}
              </Typography.Text>
            )}
          </Flex>
          <Flex vertical gap={4}>
            <label htmlFor="grant-role">Vai</label>
            <Select
              id="grant-role"
              className="w-44"
              value={role}
              onChange={setRole}
              options={grantableRoles}
            />
          </Flex>
          <Button
            type="primary"
            icon={<UserAddOutlined aria-hidden />}
            loading={busy}
            disabled={!email.trim()}
            onClick={() => void grant()}
          >
            Cấp quyền
          </Button>
        </Flex>

        {error && <Alert type="error" showIcon title={error} />}

        {members === null ? (
          <RegionState kind="loading" compact />
        ) : members.length === 0 ? (
          <RegionState kind="empty" compact title="Chưa có thành viên" />
        ) : (
          <ul className="m-0 list-none p-0">
            {members.map((m) => {
              const roleId = `role-${m.user_id}`;
              return (
                <li
                  key={m.user_id}
                  className="py-2.5"
                  style={{
                    borderBottom: `1px solid ${token.colorBorderSecondary}`,
                  }}
                >
                  <Flex wrap justify="space-between" align="center" gap="small">
                    <div className="min-w-0">
                      <Typography.Text strong ellipsis className="block">
                        {m.display_name}
                      </Typography.Text>
                      <Typography.Text type="secondary" className="text-xs">
                        {m.email ?? "—"} · {m.department}
                      </Typography.Text>
                    </div>
                    <Flex
                      align="center"
                      gap="small"
                      wrap
                      className="max-w-full"
                    >
                      <Select
                        id={roleId}
                        aria-label={`Vai của ${m.display_name}`}
                        className="w-40"
                        value={currentRole(m)}
                        onChange={(value: string) =>
                          setRoleEdits((prev) => ({
                            ...prev,
                            [m.user_id]: value,
                          }))
                        }
                        options={grantableRoles}
                      />
                      <Button
                        icon={<SaveOutlined aria-hidden />}
                        loading={savingId === m.user_id}
                        disabled={!m.email}
                        onClick={() => void saveRole(m)}
                      >
                        Lưu
                      </Button>
                      <Button
                        type="text"
                        danger
                        icon={<DeleteOutlined aria-hidden />}
                        aria-label={`Gỡ ${m.display_name}`}
                        onClick={() => revoke(m)}
                      />
                    </Flex>
                  </Flex>

                  {permissionSets.length > 0 && (
                    <div className="mt-1.5">
                      <Button
                        type="link"
                        size="small"
                        className="!px-0"
                        icon={<SafetyCertificateOutlined aria-hidden />}
                        aria-expanded={expandedSetsId === m.user_id}
                        onClick={() =>
                          setExpandedSetsId(
                            expandedSetsId === m.user_id ? null : m.user_id,
                          )
                        }
                      >
                        Bộ quyền bổ sung
                        {checkedSets(m).length > 0 && (
                          <Tag className="!ms-1">{checkedSets(m).length}</Tag>
                        )}
                      </Button>
                      {expandedSetsId === m.user_id && (
                        <Flex vertical gap="small" className="mt-2">
                          <Checkbox.Group
                            value={checkedSets(m)}
                            onChange={(next) =>
                              setSetEdits((prev) => ({
                                ...prev,
                                [m.user_id]: next as string[],
                              }))
                            }
                            options={permissionSets.map((ps) => ({
                              value: ps.key,
                              label: ps.name,
                              title: ps.scopes.join(", "),
                            }))}
                          />
                          <div>
                            <Button
                              size="small"
                              icon={<SaveOutlined aria-hidden />}
                              loading={savingSetsId === m.user_id}
                              onClick={() => void savePermissionSets(m)}
                            >
                              Lưu bộ quyền
                            </Button>
                          </div>
                        </Flex>
                      )}
                    </div>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </Flex>
    </Card>
  );
}
