import type { BalanceSnapshot, Settlement } from "./types";
import { quarterDates } from "./hooks";

export type HwmInput = {
  startDate: string;
  beginningSnapshotId: string;
  originalHwm: string;
  overrideReason: string;
  confirmedBasis: string;
};

export function hwmBasis(accountId: number, input: HwmInput, snapshots: BalanceSnapshot[], settlements: Settlement[], year: number, quarter: number) {
  const previous = settlements.filter((item) => item.status === "FINALIZED" && item.year * 4 + item.quarter < year * 4 + quarter)
    .sort((a, b) => b.year * 4 + b.quarter - (a.year * 4 + a.quarter))
    .flatMap((item) => item.account_lines.filter((line) => line.account_id === accountId))[0];
  const [quarterStart] = quarterDates(year, quarter);
  const previousDay = new Date(`${quarterStart}T00:00:00Z`);
  previousDay.setUTCDate(previousDay.getUTCDate() - 1);
  const options = snapshots.filter((snapshot) => snapshot.account_id === accountId && (snapshot.as_of_date === input.startDate
    || (input.startDate === quarterStart && snapshot.as_of_date === previousDay.toISOString().slice(0, 10))));
  const snapshot = previous ? snapshots.find((item) => item.id === previous.closing_snapshot_id)
    : input.beginningSnapshotId ? options.find((item) => String(item.id) === input.beginningSnapshotId) : options.length === 1 ? options[0] : undefined;
  const amount = previous ? previous.next_hwm ?? "" : snapshot?.total_balance ?? "";
  const value = previous ? amount : input.originalHwm || amount;
  const modified = !previous && Boolean(amount && value) && Number(value) !== Number(amount);
  const signature = JSON.stringify([accountId, input.startDate, snapshot?.id, snapshot?.as_of_date, amount, value, input.overrideReason.trim()]);
  return { previous, options, snapshot, amount, value, modified, signature, confirmed: input.confirmedBasis === signature };
}
