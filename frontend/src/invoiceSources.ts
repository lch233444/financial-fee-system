import type { Settlement } from "./types";

export function settlementSourcesFollowOriginal(originalIds: number[], replacementIds: number[], settlementById: Map<number, Settlement>, sameSources = false) {
  if (!originalIds.length) return false;
  const originals = originalIds.map((id) => settlementById.get(id));
  if (originals.some((item) => !item)) return false;
  if (sameSources) return originalIds.length === replacementIds.length
    && new Set(replacementIds).size === originalIds.length
    && replacementIds.every((id) => originalIds.includes(id) && settlementById.get(id)?.status === "FINALIZED");
  const original = originals[0]!;
  const sourceGroup = (item: Settlement) => `${item.platform_id}:${item.fee_plan_id}`;
  const originalGroups = new Set(originals.map((item) => sourceGroup(item!)));
  const originalIdSet = new Set(originalIds);
  const matched = new Set<number>();
  const groups = new Set<string>();
  for (const id of replacementIds) {
    let current = settlementById.get(id);
    if (!current || current.status !== "FINALIZED"
      || current.client_id !== original.client_id || current.year !== original.year
      || current.quarter !== original.quarter
      || groups.has(sourceGroup(current))) return false;
    groups.add(sourceGroup(current));
    if (!originalGroups.has(sourceGroup(current))) continue;
    const visited = new Set<number>([current.id]);
    let found = false;
    while (current?.replaces_settlement_id != null) {
      const previousId = current.replaces_settlement_id;
      if (visited.has(previousId)) return false;
      visited.add(previousId);
      if (originalIdSet.has(previousId)) {
        if (matched.has(previousId)) return false;
        matched.add(previousId);
        found = true;
        break;
      }
      current = settlementById.get(previousId);
    }
    if (!found) return false;
  }
  return matched.size === originalIdSet.size;
}
