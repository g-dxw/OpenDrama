import { useEffect, useRef, useState } from "react";
import {
  Alert,
  Button,
  Card,
  Descriptions,
  Form,
  Input,
  Modal,
  Space,
  Typography,
  message,
} from "antd";
import { ApiError, api, errorText, getToken } from "../api";
import { CURRENT_ENTRY } from "../entry";
import { useMe, usePermission } from "../me";

export type MembershipRefundResolution = {
  required: boolean;
  kind: "one_time_refund_window";
  provider_status: "failed" | "canceled" | null;
  remaining_seconds: number | null;
  can_resolve: boolean;
  blocked_reason: string | null;
  revision: string;
  disposition: "restore_remaining" | "release_revoked";
  resolved?: boolean;
  result?: Result | null;
};
type Intent = { key: string; reason: string; revision: string };
type Result = {
  order_no: string;
  resolved: true;
  restored: boolean;
  released_coins: number;
  starts_at: string | null;
  ends_at: string | null;
  replayed: boolean;
};
const secondsText = (seconds: number | null) =>
  seconds === null
    ? "尚未核验"
    : seconds.toLocaleString("zh-CN", { maximumFractionDigits: 6 }) + " 秒";

export function MembershipRefundResolutionCard({
  orderNo,
  ownerToken,
  resolution,
  refresh,
}: {
  orderNo: string;
  ownerToken: string;
  resolution: MembershipRefundResolution;
  refresh: () => Promise<boolean>;
}) {
  const me = useMe(),
    canRefund = usePermission("finance.refund");
  const storageKey =
    "jjtv_membership_refund_resolution:" +
    [
      CURRENT_ENTRY.kind,
      CURRENT_ENTRY.code || "platform",
      me?.id,
      orderNo,
      resolution.disposition,
    ]
      .map((value) => encodeURIComponent(String(value)))
      .join(":");
  const [confirmed, setConfirmed] = useState<Result | null>(null);
  const [open, setOpen] = useState(false),
    [reason, setReason] = useState(""),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false);
  const [intent, setIntent] = useState<Intent | null>(null),
    [reviewReady, setReviewReady] = useState(false),
    [reviewConflict, setReviewConflict] = useState(false);
  const alive = useRef(false),
    operation = useRef(0),
    busyRef = useRef(false),
    intentRef = useRef<Intent | null>(null),
    openedKey = useRef<string | null>(null);
  const current = useRef({
    orderNo,
    ownerToken,
    canRefund,
    resolution,
    storageKey,
  });
  current.current = { orderNo, ownerToken, canRefund, resolution, storageKey };
  const owns = () =>
    alive.current &&
    getToken() === ownerToken &&
    current.current.orderNo === orderNo &&
    current.current.ownerToken === ownerToken &&
    current.current.canRefund;
  const isCurrent = (id: number) => owns() && operation.current === id;
  const updateIntent = (value: Intent | null) => {
    intentRef.current = value;
    setIntent(value);
  };
  const readIntent = (): Intent | null => {
    const raw = sessionStorage.getItem(storageKey);
    if (!raw) return null;
    const saved = JSON.parse(raw);
    if (
      !saved ||
      typeof saved.key !== "string" ||
      saved.key.length < 8 ||
      saved.key.length > 100 ||
      typeof saved.reason !== "string" ||
      !saved.reason.trim() ||
      saved.reason.length > 500 ||
      typeof saved.revision !== "string" ||
      !/^[a-f0-9]{64}$/.test(saved.revision)
    )
      throw new Error("结案重试记录无法核验，请先人工核对原请求。");
    return saved as Intent;
  };
  const removeIntent = (expected: Intent) => {
    const saved = readIntent();
    if (saved?.key !== expected.key) return false;
    sessionStorage.removeItem(storageKey);
    return true;
  };
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
      operation.current++;
      busyRef.current = false;
    };
  }, []);
  useEffect(() => {
    if (
      resolution.resolved === true &&
      resolution.result?.resolved === true &&
      resolution.result.order_no === orderNo &&
      !resolution.required &&
      getToken() === ownerToken
    ) {
      try {
        const saved = readIntent();
        if (saved && removeIntent(saved))
          message.success("原权益结案已由服务端确认完成");
      } catch {
        /* Keep an unverified retry record rather than replacing it. */
      }
    }
    if (reviewConflict && intent && resolution.revision !== intent.revision)
      setReviewReady(true);
  }, [resolution, reviewConflict, intent]);
  const blocked = !resolution.can_resolve
    ? resolution.blocked_reason || "当前条件不满足，请刷新订单详情后核验。"
    : !/^[a-f0-9]{64}$/.test(resolution.revision)
      ? "结案版本尚未核验，请刷新订单详情。"
      : resolution.disposition === "restore_remaining" &&
          resolution.remaining_seconds === null
        ? "剩余会员时长尚未核验。"
        : "";
  const close = () => {
    operation.current++;
    busyRef.current = false;
    setBusy(false);
    setOpen(false);
  };
  const show = () => {
    if (!owns() || blocked) return;
    setError("");
    setReviewConflict(false);
    setReviewReady(false);
    try {
      const saved = readIntent();
      openedKey.current = saved?.key || null;
      updateIntent(saved);
      setReason(saved?.reason || "");
    } catch (e) {
      setError(errorText(e));
      updateIntent(null);
    }
    setOpen(true);
  };
  const submit = async () => {
    if (
      !owns() ||
      busyRef.current ||
      blocked ||
      !current.current.resolution.required
    )
      return;
    if (!reason.trim()) {
      setError("请填写处理原因");
      return;
    }
    let attempt: Intent;
    try {
      const saved = readIntent();
      if ((saved?.key || null) !== openedKey.current)
        throw new Error("原结案请求已改变，请关闭后重新核对订单。");
      attempt = saved || {
        key: "membership-resolution-" + crypto.randomUUID(),
        reason: reason.trim(),
        revision: current.current.resolution.revision,
      };
      if (!saved) sessionStorage.setItem(storageKey, JSON.stringify(attempt));
      openedKey.current = attempt.key;
      updateIntent(attempt);
      setReason(attempt.reason);
    } catch (e) {
      setError(errorText(e));
      return;
    }
    const id = ++operation.current;
    busyRef.current = true;
    setBusy(true);
    setError("");
    setReviewConflict(false);
    setReviewReady(false);
    try {
      const result = await api<Result>(
        "/admin/v1/finance/orders/" +
          encodeURIComponent(orderNo) +
          "/membership-refund-resolution",
        {
          method: "POST",
          body: JSON.stringify({
            reason: attempt.reason,
            idempotency_key: attempt.key,
            expected_revision: attempt.revision,
          }),
        },
      );
      if (!isCurrent(id)) return;
      if (result.order_no !== orderNo || result.resolved !== true)
        throw new Error("结案结果尚未确认，请保留原请求重试。");
      setConfirmed(result);
      removeIntent(attempt);
      updateIntent(null);
      setOpen(false);
      message.success(
        result.replayed
          ? "原权益结案请求已确认完成"
          : result.restored
            ? "权益结案已完成，冻结剩余会员时长已从本次确认时起恢复"
            : "权益结案已完成，未恢复会员时长；释放金币 " +
              result.released_coins,
      );
      await refresh();
    } catch (e) {
      if (!isCurrent(id)) return;
      setError(errorText(e));
      const detail = e instanceof ApiError ? e.detail : null;
      const changed =
        e instanceof ApiError &&
        e.status === 409 &&
        detail?.code === "membership_resolution_changed" &&
        detail?.action === "review_again" &&
        detail?.committed === false;
      setReviewConflict(changed);
      const refreshed = await refresh();
      if (isCurrent(id) && changed) setReviewReady(refreshed);
    } finally {
      if (isCurrent(id)) {
        busyRef.current = false;
        setBusy(false);
      }
    }
  };
  const reviewAgain = () => {
    if (
      !owns() ||
      busyRef.current ||
      !reviewConflict ||
      !reviewReady ||
      !intentRef.current
    )
      return;
    try {
      if (!removeIntent(intentRef.current))
        throw new Error("原结案请求已改变，请重新打开订单。");
      openedKey.current = null;
      updateIntent(null);
      setReviewConflict(false);
      setReviewReady(false);
      setError("");
    } catch (e) {
      setError(errorText(e));
    }
  };
  if (getToken() !== ownerToken) return null;
  if (confirmed)
    return (
      <Alert
        showIcon
        type="success"
        data-testid="membership-refund-resolution-confirmed"
        message="权益结案已确认完成"
        description={
          (confirmed.restored
            ? "已恢复冻结剩余会员时长；"
            : "未恢复会员时长；") +
          "释放金币 " +
          confirmed.released_coins +
          "。可刷新订单详情核对实际权益区间。"
        }
      />
    );
  if (!resolution.required) return null;
  const restoring = resolution.disposition === "restore_remaining";
  return (
    <Card
      size="small"
      title="会员退款窗口结案"
      data-testid="membership-refund-resolution-card"
    >
      <Descriptions
        size="small"
        column={1}
        items={[
          {
            key: "remaining",
            label: "冻结剩余会员时长",
            children: secondsText(resolution.remaining_seconds),
          },
          {
            key: "provider",
            label: "支付渠道退款终态",
            children:
              resolution.provider_status === "failed"
                ? "已确认失败"
                : resolution.provider_status === "canceled"
                  ? "已确认取消"
                  : "尚未确认",
          },
          {
            key: "action",
            label: "本次权益处理",
            children: restoring
              ? "从确认时起恢复冻结剩余时长"
              : "仅释放金币；会员权益保持收回",
          },
        ]}
      />
      {blocked && (
        <Alert
          showIcon
          type="warning"
          message={blocked}
          style={{ marginTop: 12 }}
        />
      )}
      {!canRefund ? (
        <Typography.Paragraph
          type="secondary"
          style={{ marginBottom: 0, marginTop: 12 }}
        >
          当前角色仅可查看结案依据，无退款处理权限。
        </Typography.Paragraph>
      ) : (
        <Button
          style={{ marginTop: 12 }}
          data-testid="membership-refund-resolution-open"
          disabled={!!blocked}
          onClick={show}
        >
          核对并结案
        </Button>
      )}
      <Modal
        title="确认会员退款窗口结案"
        open={open && canRefund}
        onCancel={close}
        okButtonProps={{
          loading: busy,
          disabled: busy || !!blocked || reviewConflict,
        }}
        onOk={() => void submit()}
        okText={intent ? "重试原结案请求" : "确认权益结案"}
        cancelText={busy ? "关闭（保留原请求）" : "关闭"}
        wrapClassName="membership-refund-resolution-modal"
        destroyOnClose
      >
        <Typography.Paragraph strong>{orderNo}</Typography.Paragraph>
        <Alert
          showIcon
          type="warning"
          message={
            restoring
              ? "确认后从本次确认时起恢复 " +
                secondsText(resolution.remaining_seconds) +
                " 会员权益"
              : "仅释放本订单冻结金币，会员权益保持收回"
          }
          description="此操作只结案本订单权益，不会向 Stripe 发起退款或新的支付。"
        />
        {intent && (
          <Alert
            showIcon
            type="info"
            style={{ marginTop: 12 }}
            message="原请求已保留。重试沿用原处理原因与核验版本，只核对同一次结案。"
          />
        )}
        {blocked && (
          <Alert
            showIcon
            type="warning"
            style={{ marginTop: 12 }}
            message={blocked}
          />
        )}
        {error && (
          <Alert
            showIcon
            type="error"
            style={{ marginTop: 12 }}
            message={error}
          />
        )}
        {reviewConflict && (
          <Space direction="vertical" style={{ marginTop: 12 }}>
            <Typography.Text>
              服务端确认原请求未执行，请先核对刷新后的权益依据。
            </Typography.Text>
            <Button
              data-testid="membership-refund-resolution-review"
              disabled={!reviewReady || busy}
              onClick={reviewAgain}
            >
              重新核对
            </Button>
          </Space>
        )}
        <Form layout="vertical" style={{ marginTop: 16 }}>
          <Form.Item label="处理原因" required>
            <Input.TextArea
              data-testid="membership-refund-resolution-reason"
              aria-label="权益结案处理原因"
              value={reason}
              readOnly={!!intent}
              onChange={(e) => setReason(e.target.value)}
              maxLength={500}
              rows={3}
            />
          </Form.Item>
        </Form>
      </Modal>
    </Card>
  );
}
