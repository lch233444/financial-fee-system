import { fireEvent, render, screen, within } from "@testing-library/react";
import { expect, test, vi } from "vitest";
import RecordFilters from "../src/RecordFilters";
import { defaultRecordFilters } from "../src/periodFilters";
import { clientIdentityLabel, clientIdentityLabels, type Account, type Client, type ClientIdentity } from "../src/types";

const sameName: ClientIdentity[] = [{ id: 1, name: "合成同名客户", fc_name: "共同FC", contact: null }, { id: 2, name: "合成同名客户", fc_name: "共同FC", contact: null }];

test("唯一姓名保持原名，同名优先用FC及联系方式区分", () => {
  expect(clientIdentityLabel(sameName[0], [sameName[0]])).toBe("合成同名客户");
  const differentFc = [sameName[0], { ...sameName[1], fc_name: "另一FC" }];
  expect(clientIdentityLabel(differentFc[1], differentFc)).toBe("合成同名客户 · FC 另一FC");
  const differentContact = [{ ...sameName[0], contact: "contact-a" }, { ...sameName[1], contact: "contact-b" }];
  expect(clientIdentityLabel(differentContact[1], differentContact)).toBe("合成同名客户 · FC 共同FC · contact-b");
});

test("同名同FC时优先显示实际账户，同名平台和Scheme不会重复", () => {
  const accounts = [1, 2].map((id) => ({ id, client_id: id, platform_name: "合成平台", scheme_name: "合成平台", account_number: `ACCOUNT-${id}`, fee_plan_name: "合成计划" })) as Account[];
  expect(clientIdentityLabel(sameName[0], sameName, accounts)).toBe("合成同名客户 · FC 共同FC · 合成平台 · ACCOUNT-1 · 收费计划 合成计划");
  expect(clientIdentityLabel(sameName[1], sameName, accounts)).toContain("ACCOUNT-2");
  expect(clientIdentityLabel(sameName[1], sameName, accounts)).not.toContain("档案 #");
});

test("全部业务身份仍相同时仅在候选标签保留档案编号，明确选择对应内部ID", () => {
  const changed = vi.fn();
  render(<RecordFilters value={defaultRecordFilters} onChange={changed} clients={sameName as Client[]} fcs={[]} plans={[]} />);
  fireEvent.focus(screen.getByRole("combobox", { name: "Client" }));
  const list = within(screen.getByRole("listbox", { name: "Client候选" }));
  expect(list.getAllByRole("option").map((item) => item.textContent)).toEqual(["合成同名客户 · FC 共同FC · 档案 #1", "合成同名客户 · FC 共同FC · 档案 #2"]);
  fireEvent.click(list.getByRole("option", { name: /档案 #2$/ }));
  expect(changed).toHaveBeenCalledWith({ ...defaultRecordFilters, clientId: "2" });
});

test("同名客户的多账户按账户ID选首户并计数，候选顺序和账户归属保持", () => {
  const clients = [sameName[1], sameName[0]] as Client[];
  const accounts = [
    { id: 30, client_id: 1, platform_name: "合成平台", scheme_name: "另一方案", account_number: "A-LAST", fee_plan_name: "合成计划" },
    { id: 20, client_id: 2, platform_name: "合成平台", scheme_name: "合成平台", account_number: "B-ONLY", fee_plan_name: "合成计划" },
    { id: 10, client_id: 1, platform_name: "合成平台", scheme_name: "合成平台", account_number: "A-FIRST", fee_plan_name: "合成计划" },
    { id: 1, client_id: 99, platform_name: "其他平台", account_number: "UNRELATED", fee_plan_name: null },
  ] as Account[];
  const changed = vi.fn();
  render(<RecordFilters value={defaultRecordFilters} onChange={changed} clients={clients} accounts={accounts} fcs={[]} plans={[]} />);
  fireEvent.focus(screen.getByRole("combobox", { name: "Client" }));
  const list = within(screen.getByRole("listbox", { name: "Client候选" }));
  expect(list.getAllByRole("option").map((item) => item.textContent)).toEqual([
    "合成同名客户 · FC 共同FC · 合成平台 · B-ONLY · 收费计划 合成计划",
    "合成同名客户 · FC 共同FC · 合成平台 · A-FIRST · 收费计划 合成计划 等2个账户",
  ]);
  fireEvent.click(list.getByRole("option", { name: /A-FIRST/ }));
  expect(changed).toHaveBeenCalledWith({ ...defaultRecordFilters, clientId: "1" });
  expect(accounts.map((account) => account.id)).toEqual([30, 20, 10, 1]);
});

test("批量标签保持规范化重名、业务资料及完整账户仍相同的编号判断", () => {
  const clients: ClientIdentity[] = [
    { id: 7, name: "唯一客户", fc_name: "独立FC" },
    { id: 3, name: "Ａｌｉｃｅ", fc_name: " 共同FC ", contact: "contact" },
    { id: 2, name: "alice", fc_name: "共同fc", contact: "CONTACT" },
    { id: 1, name: "ALICE", fc_name: "另一FC" },
  ];
  const accounts = [
    { id: 30, client_id: 3, platform_name: "平台", scheme_name: "平台", account_number: "SAME", fee_plan_name: null },
    { id: 20, client_id: 2, platform_name: "平台", scheme_name: "平台", account_number: "SAME", fee_plan_name: null },
  ] as Account[];
  expect(clientIdentityLabels(clients, accounts)).toEqual([
    "唯一客户",
    "Ａｌｉｃｅ · FC 共同FC · contact · 平台 · SAME · 未分配收费计划 · 档案 #3",
    "alice · FC 共同fc · CONTACT · 平台 · SAME · 未分配收费计划 · 档案 #2",
    "ALICE · FC 另一FC",
  ]);
  expect(clientIdentityLabels([], accounts)).toEqual([]);
});
