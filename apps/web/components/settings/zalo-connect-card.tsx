"use client";

import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import {
  Alert,
  App,
  Button,
  Card,
  Flex,
  Skeleton,
  Tag,
  Tooltip,
  Typography,
} from "antd";
import {
  CheckOutlined,
  CopyOutlined,
  LinkOutlined,
  ReloadOutlined,
} from "@ant-design/icons";
import { ApiError, type ZaloConnect } from "@dw/api-client";
import { formatDateTime } from "../../lib/dates";
import { errorCode, errorMessage } from "../../lib/error-message";
import { useCopyToClipboard } from "../../lib/hooks/use-copy-to-clipboard";
import { useOnline } from "../../lib/hooks/use-online";
import { apiClient } from "../../lib/session";

/** How often a pending code checks whether the bot has redeemed it. */
const PENDING_POLL_MS = 5000;

const OFFLINE_REASON = "Không có kết nối mạng. Kết nối lại rồi thử lại.";

type Status =
  | { kind: "loading" }
  | { kind: "unconfigured" }
  | { kind: "offline" }
  | { kind: "error"; message: string }
  | { kind: "ready"; linked: boolean };

/** A failure the browser raised itself, before any server answered. */
function isNetworkFailure(error: unknown): boolean {
  return !(error instanceof ApiError);
}

function statusFromError(error: unknown): Status {
  if (errorCode(error) === "not_found") return { kind: "unconfigured" };
  if (isNetworkFailure(error)) return { kind: "offline" };
  return { kind: "error", message: errorMessage(error) };
}

/**
 * The signed-in user's own Zalo link.
 *
 * The server decides everything: who "me" is (the bearer token), whether a
 * code is still good (single use, 15 minutes), and whether this deployment has
 * a bot at all (404 from every route when it has none). The card only draws
 * what the server said.
 */
export function ZaloConnectCard({
  now = () => new Date(),
  children,
}: {
  now?: () => Date;
  /** Drawn under the status once the server answered (the workspace choice). */
  children?: ReactNode;
}) {
  const { modal, message } = App.useApp();
  const online = useOnline();
  const { isCopied, copyToClipboard } = useCopyToClipboard();
  const [status, setStatus] = useState<Status>({ kind: "loading" });
  const [offer, setOffer] = useState<ZaloConnect | null>(null);
  const [connecting, setConnecting] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [, setTick] = useState(0);
  // A second press while the first request is in flight sends nothing: the
  // button's `loading` drops clicks once React re-renders, this covers the gap.
  const inFlight = useRef(false);

  const load = useCallback(async () => {
    try {
      const result = await apiClient().getZaloStatus();
      setStatus({ kind: "ready", linked: result.linked });
      return result.linked;
    } catch (error) {
      setStatus(statusFromError(error));
      return null;
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const expired = offer !== null && now() >= new Date(offer.expires_at);
  const waitingForBot =
    offer !== null && !expired && status.kind === "ready" && !status.linked;

  // While a fresh code waits for the bot, check every few seconds; the page
  // flips to "Đã kết nối" on its own once the user has sent it.
  useEffect(() => {
    if (!waitingForBot) return;
    const timer = window.setInterval(() => {
      setTick((n) => n + 1); // re-evaluate expiry
      void load().then((linked) => {
        if (linked) {
          setOffer(null);
          void message.success("Đã kết nối Zalo.");
        }
      });
    }, PENDING_POLL_MS);
    return () => window.clearInterval(timer);
  }, [waitingForBot, load, message]);

  const connect = async () => {
    if (inFlight.current) return;
    inFlight.current = true;
    setConnecting(true);
    setActionError(null);
    try {
      setOffer(await apiClient().connectZalo());
    } catch (error) {
      if (errorCode(error) === "not_found") setStatus({ kind: "unconfigured" });
      else
        setActionError(
          isNetworkFailure(error) ? OFFLINE_REASON : errorMessage(error),
        );
    } finally {
      inFlight.current = false;
      setConnecting(false);
    }
  };

  const confirmDisconnect = () => {
    modal.confirm({
      title: "Ngắt kết nối Zalo?",
      content:
        "Bạn sẽ không nhận thông báo qua Zalo nữa cho tới khi kết nối lại. Hộp thư trong ứng dụng không bị ảnh hưởng.",
      okText: "Ngắt kết nối",
      okButtonProps: { danger: true },
      cancelText: "Giữ kết nối",
      autoFocusButton: "cancel",
      onOk: async () => {
        setActionError(null);
        try {
          await apiClient().disconnectZalo();
          setOffer(null);
          setStatus({ kind: "ready", linked: false });
          void message.success("Đã ngắt kết nối Zalo.");
        } catch (error) {
          setActionError(
            isNetworkFailure(error) ? OFFLINE_REASON : errorMessage(error),
          );
        }
      },
    });
  };

  const linked = status.kind === "ready" && status.linked;

  const offlineTip = (node: ReactNode) =>
    online ? node : <Tooltip title={OFFLINE_REASON}>{node}</Tooltip>;

  const body = (() => {
    switch (status.kind) {
      case "loading":
        return <Skeleton active title={false} paragraph={{ rows: 2 }} />;
      case "unconfigured":
        return (
          <Alert
            type="info"
            showIcon
            message="Hệ thống chưa bật Zalo"
            description="Quản trị viên cần cấu hình bot Zalo trước khi bạn kết nối được."
          />
        );
      case "offline":
        return (
          <Alert
            type="warning"
            showIcon
            message="Không tải được trạng thái Zalo vì mất kết nối mạng."
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
        );
      case "error":
        return (
          <Alert
            type="error"
            showIcon
            message={status.message}
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
        );
      case "ready":
        return null;
    }
  })();

  return (
    <Card
      title="Zalo"
      extra={
        status.kind === "ready" ? (
          linked ? (
            <Tag color="success">Đã kết nối</Tag>
          ) : (
            <Tag>Chưa kết nối</Tag>
          )
        ) : null
      }
    >
      {body}
      {status.kind === "ready" && (
        <Flex vertical gap="middle">
          <Typography.Paragraph className="!mb-0">
            {linked
              ? "Thông báo công việc được gửi tới Zalo của bạn. Muốn dừng, gửi /stop cho bot hoặc bấm “Ngắt kết nối”."
              : "Kết nối Zalo của bạn để nhận thông báo công việc qua Zalo."}
          </Typography.Paragraph>

          {offer !== null && (
            <div role="status">
              {expired ? (
                <Alert
                  type="warning"
                  showIcon
                  message="Mã đã hết hạn. Bấm “Lấy mã mới” để có mã khác."
                />
              ) : (
                <Flex vertical gap="small">
                  <Typography.Text>
                    Gửi tin nhắn sau cho bot Zalo:
                  </Typography.Text>
                  <Flex wrap gap="small" align="center">
                    <Typography.Text code className="break-all">
                      /start {offer.code}
                    </Typography.Text>
                    <Button
                      icon={
                        isCopied ? (
                          <CheckOutlined aria-hidden />
                        ) : (
                          <CopyOutlined aria-hidden />
                        )
                      }
                      onClick={() => copyToClipboard(`/start ${offer.code}`)}
                    >
                      {isCopied ? "Đã chép" : "Chép lệnh"}
                    </Button>
                    {offer.deep_link && (
                      <Button
                        icon={<LinkOutlined aria-hidden />}
                        href={offer.deep_link}
                        target="_blank"
                        rel="noopener noreferrer"
                      >
                        Mở Zalo
                      </Button>
                    )}
                  </Flex>
                  <Typography.Text>
                    Mã chỉ dùng được một lần, hết hạn lúc{" "}
                    {formatDateTime(offer.expires_at)} (15 phút).
                    {linked
                      ? " Khi bot xác nhận, Zalo mới thay cho Zalo cũ."
                      : " Trang tự cập nhật khi kết nối xong."}
                  </Typography.Text>
                </Flex>
              )}
            </div>
          )}

          {children}

          {!online && (
            <Alert type="warning" showIcon message={OFFLINE_REASON} />
          )}
          {actionError && <Alert type="error" showIcon message={actionError} />}

          <Flex wrap gap="small">
            {offlineTip(
              <Button
                type={linked ? "default" : "primary"}
                loading={connecting}
                disabled={!online}
                onClick={() => void connect()}
              >
                {offer !== null
                  ? "Lấy mã mới"
                  : linked
                    ? "Đổi Zalo khác"
                    : "Kết nối Zalo"}
              </Button>,
            )}
            {offer !== null && !linked && !expired && (
              <Button
                icon={<ReloadOutlined aria-hidden />}
                onClick={() => void load()}
              >
                Kiểm tra lại
              </Button>
            )}
            {linked &&
              offlineTip(
                <Button danger disabled={!online} onClick={confirmDisconnect}>
                  Ngắt kết nối
                </Button>,
              )}
          </Flex>
        </Flex>
      )}
    </Card>
  );
}
