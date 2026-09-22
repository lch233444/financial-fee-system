import { Field } from "./components";
import type { Account, Client, FC, FeePlan, Platform } from "./types";

export function statementProfile(data: FormData, client?: Client) {
  return {
    platform_id: data.get("profile_platform_id") === "statement" ? null : Number(data.get("profile_platform_id")),
    fee_plan_id: Number(data.get("profile_fee_plan_id")),
    start_date: data.get("profile_start_date"),
    end_date: data.get("profile_end_date") || null,
    account_remark: data.get("profile_account_remark") || null,
    ...(!client || client.status === "DRAFT" ? {
      fc_id: Number(data.get("profile_fc_id")),
      management_start_date: data.get("profile_management_start_date"),
      contact: data.get("profile_contact") || null,
      client_remark: data.get("profile_client_remark") || null,
    } : {}),
  };
}

export default function StatementProfileFields({ client, account, fcs, plans, platforms, scheme, disabled }: {
  client?: Client; account?: Account; fcs: FC[]; plans: FeePlan[]; platforms: Platform[];
  scheme?: string; disabled: boolean;
}) {
  return <fieldset className="statement-profile-fields form-grid" disabled={disabled}>
    <legend>确认客户与账户资料</legend>
    <p className="form-wide">请核对归属、收费计划和管理日期；提交后直接完成建档，无需另行激活。</p>
    {!client || client.status === "DRAFT" ? <>
      <Field label="客户FC"><select name="profile_fc_id" required defaultValue={client?.fc_id ?? ""}><option value="" disabled>请选择FC</option>{fcs.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></Field>
      <Field label="客户开始管理日期"><input name="profile_management_start_date" type="date" required defaultValue={client?.management_start_date ?? ""} /></Field>
      <Field label="客户联系方式"><input name="profile_contact" defaultValue={client?.contact ?? ""} /></Field>
      <Field label="客户备注"><input name="profile_client_remark" defaultValue={client?.remark ?? ""} /></Field>
    </> : null}
    <Field label="账户Platform"><select name="profile_platform_id" required defaultValue={account?.platform_id ?? ""}>
      <option value="" disabled>请选择Platform</option>
      {platforms.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
      {!account && scheme?.trim() ? <option value="statement">按已核对的Scheme建立／匹配：{scheme}</option> : null}
    </select></Field>
    <Field label="账户收费计划"><select name="profile_fee_plan_id" required defaultValue={account?.fee_plan_id ?? ""}><option value="" disabled>请选择收费计划</option>{plans.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></Field>
    <Field label="账户开始管理日期"><input name="profile_start_date" type="date" required defaultValue={account?.start_date ?? ""} /></Field>
    <Field label="账户实际结束日期"><input name="profile_end_date" type="date" defaultValue={account?.end_date ?? ""} /></Field>
    <Field label="账户备注"><input name="profile_account_remark" defaultValue={account?.remark ?? ""} /></Field>
  </fieldset>;
}
