import type { Settlement } from "./types";

export function settlementSourcesFollowOriginal(originalIds: number[], replacementIds: number[], settlementById: Map<number, Settlement>) {
  if (!originalIds.length) return false;
  const originals = originalIds.map((id) => settlementById.get(id));
  if (originals.some((item) => !item)) return false;
  const original = originals[0]!;
  const originalPlatforms = new Set(originals.map((item) => item!.platform_id));
  const originalIdSet = new Set(originalIds);
  const matched = new Set<number>();
  const platforms = new Set<number>();
  for (const id of replacementIds) {
    let current = settlementById.get(id);
    if (!current || current.status !== "FINALIZED"
      || current.client_id !== original.client_id || current.year !== original.year
      || current.quarter !== original.quarter || current.fee_plan_id !== original.fee_plan_id
      || platforms.has(current.platform_id)) return false;
    platforms.add(current.platform_id);
    if (!originalPlatforms.has(current.platform_id)) continue;
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
