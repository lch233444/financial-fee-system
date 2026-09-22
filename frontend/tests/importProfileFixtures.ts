import { fireEvent, screen } from "@testing-library/react";

export const profileRecords: Record<string, unknown[]> = {
  "/api/fcs": [{ id: 1, name: "示例FC" }],
  "/api/platforms": [{ id: 1, name: "示例平台" }],
  "/api/fee-plans": [{ id: 1, name: "示例收费计划" }],
};

export function completeProfile() {
  for (const [label, value] of [["客户FC", "1"], ["客户开始管理日期", "2026-01-01"],
    ["账户Platform", "1"], ["账户收费计划", "1"], ["账户开始管理日期", "2026-01-01"]]) {
    const field = screen.queryByLabelText(label);
    if (field) fireEvent.change(field, { target: { value } });
  }
}
