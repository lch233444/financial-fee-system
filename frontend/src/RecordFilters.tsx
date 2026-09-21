import type { ReactNode } from "react";
import SearchableSelect from "./SearchableSelect";
import { Field } from "./components";
import { clientIdentityLabel } from "./types";
import type { Account, Client, FC, FeePlan } from "./types";
import type { RecordFilterValue } from "./periodFilters";

export type { RecordFilterValue } from "./periodFilters";

const years = Array.from({ length: 201 }, (_, index) => String(2000 + index));

export default function RecordFilters({ value, onChange, clients, accounts = [], fcs, plans, children, disabled = false, clientLabel = "Client" }: {
  value: RecordFilterValue;
  onChange: (next: RecordFilterValue) => void;
  clients: Client[];
  accounts?: Account[];
  fcs: FC[];
  plans: FeePlan[];
  children?: ReactNode;
  disabled?: boolean;
  clientLabel?: string;
}) {
  const change = (key: keyof RecordFilterValue, next: string) => onChange({ ...value, [key]: next });
  return <div className="record-filters">
    <Field group label="Client"><SearchableSelect label={clientLabel} value={value.clientId} onChange={(next) => change("clientId", next)} disabled={disabled} placeholder="全部客户" options={clients.map((client) => ({ value: String(client.id), label: clientIdentityLabel(client, clients, accounts) }))} /></Field>
    <Field group label="年份"><SearchableSelect label="年份" value={value.year} onChange={(next) => change("year", next)} disabled={disabled} required options={years.map((year) => ({ value: year, label: `${year}年` }))} optionLimit={years.length} centerSelectedOnOpen searchPlaceholder="输入年份搜索" /></Field>
    <Field label="季度"><select value={value.quarter} disabled={disabled} onChange={(event) => change("quarter", event.target.value)}><option value="">全年 · 全部季度</option><option value="1">Q1 · 第一季度</option><option value="2">Q2 · 第二季度</option><option value="3">Q3 · 第三季度</option><option value="4">Q4 · 第四季度</option></select></Field>
    <Field group label="FC"><SearchableSelect label="FC筛选" value={value.fcId} onChange={(next) => change("fcId", next)} disabled={disabled} placeholder="全部FC" searchPlaceholder="输入FC名称搜索" options={fcs.map((fc) => ({ value: String(fc.id), label: fc.name }))} /></Field>
    <Field group label="Fee Plan"><SearchableSelect label="Fee Plan筛选" value={value.feePlanId} onChange={(next) => change("feePlanId", next)} disabled={disabled} placeholder="全部收费计划" searchPlaceholder="输入收费计划名称搜索" options={plans.map((plan) => ({ value: String(plan.id), label: plan.name }))} /></Field>
    {children}
  </div>;
}
