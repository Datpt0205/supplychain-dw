import {
  AlertTriangle,
  FlaskConical,
  ListTodo,
  Newspaper,
  PackageSearch,
  TowerControl,
} from "lucide-react";
import type { NavItem } from "../../../lib/nav/types";

/**
 * Supply Chain's own nav manifest — the plug-in point `lib/nav/registry.ts`
 * documents. Edited here as the context grows; the registry itself is
 * touched once, to add the import and the spread.
 */
export const supplyChainNav: NavItem[] = [
  {
    href: "/supply-chain/daily-brief",
    label: "Bản tin hôm nay",
    hint: "Việc cần xử lý, gom theo tín hiệu và xếp theo ưu tiên",
    icon: Newspaper,
    scope: "supply_chain.po_case.read",
  },
  {
    href: "/supply-chain/follow-ups",
    label: "Việc cần làm",
    hint: "Nhắc NCC, leo thang và trễ SLA đã giao cho bạn",
    icon: ListTodo,
    scope: "supply_chain.po_case.read",
  },
  {
    href: "/supply-chain/attention-queue",
    label: "Cần chú ý",
    hint: "Case trễ SLA hoặc nhà cung cấp im lặng quá lâu",
    icon: AlertTriangle,
    scope: "supply_chain.po_case.read",
  },
  {
    href: "/supply-chain/control-tower",
    label: "Control Tower",
    hint: "Toàn bộ case đang chạy theo trạng thái và nhà cung cấp",
    icon: TowerControl,
    scope: "supply_chain.po_case.read",
  },
  {
    href: "/supply-chain/product-cases",
    label: "Hồ sơ phát triển SP",
    hint: "Sản phẩm đề xuất, lấy mẫu, test mẫu tới khi chờ BGĐ duyệt",
    icon: FlaskConical,
    scope: "supply_chain.product_case.read",
  },
  {
    href: "/supply-chain/po-cases",
    label: "PO cases",
    hint: "Purchase orders, their state, SLA health and supplier updates",
    icon: PackageSearch,
    scope: "supply_chain.po_case.read",
  },
];
