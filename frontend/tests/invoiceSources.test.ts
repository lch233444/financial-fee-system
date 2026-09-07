import { expect, test } from "vitest";
import { settlementSourcesFollowOriginal } from "../src/invoiceSources";
import type { Settlement } from "../src/types";

function sources(changes: Partial<Settlement>[] = []) {
  return new Map([
    { id: 1, platform_id: 1, status: "VOID", replaces_settlement_id: null },
    { id: 2, platform_id: 1, status: "FINALIZED", replaces_settlement_id: 1 },
    { id: 3, platform_id: 2, status: "FINALIZED", replaces_settlement_id: null },
  ].map((item, index) => [item.id, { client_id: 1, year: 2026, quarter: 1, fee_plan_id: 1, ...item, ...changes[index] } as Settlement]));
}

test("更正候选保留全部原平台替代链并接纳迟到的新平台", () => {
  expect(settlementSourcesFollowOriginal([1], [2, 3], sources())).toBe(true);
});

test.each([
  { ids: [3], changes: [], description: "遗漏原来源" },
  { ids: [1, 3], changes: [], description: "原来源未替代" },
  { ids: [2, 3], changes: [{}, {}, { quarter: 2 }], description: "跨季度来源" },
  { ids: [2, 3], changes: [{}, {}, { platform_id: 1 }], description: "同平台冒充新增" },
  { ids: [2, 3], changes: [{}, { replaces_settlement_id: 2 }], description: "循环替代链" },
])("拒绝$description", ({ ids, changes }) => {
  expect(settlementSourcesFollowOriginal([1], ids, sources(changes))).toBe(false);
});
