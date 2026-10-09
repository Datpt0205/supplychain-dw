import {
  ControlOutlined,
  ExperimentOutlined,
  OrderedListOutlined,
  SettingOutlined,
  FileSearchOutlined,
  ImportOutlined,
  ReadOutlined,
  ScheduleOutlined,
  WarningOutlined,
} from "@ant-design/icons";
import type { NavContext, NavItem } from "../../../lib/nav/types";

/** Supply Chain as the navbar names it, when it is someone's whole bar. */
const SUPPLY_CHAIN: NavContext = {
  key: "supply-chain",
  product: "Supply Chain",
};

/**
 * Supply Chain's own nav manifest — the plug-in point `lib/nav/registry.ts`
 * documents. Edited here as the context grows; the registry itself is
 * touched once, to add the import and the spread. In process order: the
 * day's work first, then stage 1 (steps 1–9), then stage 2 (steps 10–17).
 */
export const supplyChainNav: NavItem[] = [
  {
    href: "/supply-chain/daily-brief",
    label: "Bản tin hôm nay",
    hint: "Việc cần xử lý, gom theo tín hiệu và xếp theo ưu tiên",
    icon: ReadOutlined,
    scope: "supply_chain.po_case.read",
    context: SUPPLY_CHAIN,
  },
  {
    href: "/supply-chain/follow-ups",
    label: "Việc cần làm",
    hint: "Nhắc NCC, leo thang và trễ SLA đã giao cho bạn",
    icon: ScheduleOutlined,
    scope: "supply_chain.po_case.read",
    context: SUPPLY_CHAIN,
  },
  {
    href: "/supply-chain/product-cases",
    label: "Phát triển SP",
    hint: "Hồ sơ phát triển sản phẩm, bước 1–9: đề xuất, lấy mẫu, test mẫu, BGĐ duyệt",
    icon: ExperimentOutlined,
    scope: "supply_chain.product_case.read",
    context: SUPPLY_CHAIN,
  },
  {
    href: "/supply-chain/proposal-lists",
    label: "Danh sách đề xuất",
    hint: "Bước 1 từ một file: AI tách danh sách SP đề xuất thành từng dòng, bạn đề xuất từng dòng",
    icon: OrderedListOutlined,
    scope: "supply_chain.product_case.read",
    context: SUPPLY_CHAIN,
  },
  {
    href: "/supply-chain/po-cases",
    label: "Hồ sơ PO",
    hint: "Hồ sơ PO, bước 10–17: trạng thái, SLA và cập nhật của NCC",
    icon: FileSearchOutlined,
    scope: "supply_chain.po_case.read",
    context: SUPPLY_CHAIN,
  },
  {
    href: "/supply-chain/attention-queue",
    label: "Cần chú ý",
    hint: "Hồ sơ PO trễ SLA hoặc NCC im lặng quá lâu",
    icon: WarningOutlined,
    scope: "supply_chain.po_case.read",
    context: SUPPLY_CHAIN,
  },
  {
    href: "/supply-chain/control-tower",
    label: "Control Tower",
    hint: "Mọi Hồ sơ PO đang chạy theo trạng thái và NCC",
    icon: ControlOutlined,
    scope: "supply_chain.po_case.read",
    context: SUPPLY_CHAIN,
  },
  {
    href: "/supply-chain/settings",
    label: "Cấu hình",
    hint: "SLA theo Category và quy tắc test tiền sản xuất của công ty",
    icon: SettingOutlined,
    // Either policy's reader finds the page; each card checks its own scope.
    anyScope: [
      "supply_chain.sla_policy.read",
      "supply_chain.action_duties.read",
    ],
    context: SUPPLY_CHAIN,
  },
  {
    href: "/supply-chain/import",
    label: "Nạp dữ liệu",
    hint: "Nạp một lần NCC, danh mục mã hàng và SKU, người dùng từ mẫu Excel",
    icon: ImportOutlined,
    scope: "supply_chain.import",
    context: SUPPLY_CHAIN,
  },
];
