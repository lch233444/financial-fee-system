import { datasetState } from "./datasetSession";

export type SettlementCycle = "QUARTERLY" | "ANNUAL";
export function isAnnualDataset() { return datasetState()?.settlement_cycle === "ANNUAL"; }
export function cycleLabel(cycle?: SettlementCycle) { return cycle === "ANNUAL" ? "自然年度结算" : "季度结算"; }
export function periodLabel(year: number, quarter: number) {
  return quarter === 0 ? `${year}年度` : `${year}年第${quarter}季度`;
}
// Only pass fixed interface copy here; customer names and saved records stay intact.
export function cycleText(text: string) {
  return isAnnualDataset() ? text.replaceAll("季度", "年度").replaceAll("季末", "年末").replaceAll("本季", "本年") : text;
}
