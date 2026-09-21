import { fireEvent, render, screen, within } from "@testing-library/react";
import { expect, test, vi } from "vitest";
import RecordFilters from "../src/RecordFilters";
import { defaultRecordFilters } from "../src/periodFilters";
import { clientIdentityLabel, type Account, type Client, type ClientIdentity } from "../src/types";

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
