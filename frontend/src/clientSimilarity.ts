export type ClientSearchOption = { value: string; label: string; name: string; searchText: string };

// Text similarity only: never infer aliases, transliterations or client identity.
function nameText(value: string) {
  return value.normalize("NFKC").toLowerCase().replace(/\s+/gu, "");
}

export function nameSimilarity(left: string, right: string): number {
  const a = Array.from(nameText(left));
  const b = Array.from(nameText(right));
  if (!a.length || !b.length) return 0;
  let row = b.map((_, index) => index + 1);
  row.unshift(0);
  for (let i = 0; i < a.length; i += 1) {
    const next = [i + 1];
    for (let j = 0; j < b.length; j += 1) {
      next.push(Math.min(next[j] + 1, row[j + 1] + 1, row[j] + (a[i] === b[j] ? 0 : 1)));
    }
    row = next;
  }
  return 1 - row[b.length] / Math.max(a.length, b.length);
}

export function searchClientOptions(options: ClientSearchOption[], query: string, reviewName: string): ClientSearchOption[] {
  const search = query.trim();
  const normalizedQuery = nameText(search);
  return options.map((option, index) => ({ option, index,
    score: nameSimilarity(option.name, search || reviewName),
    direct: Boolean(normalizedQuery && nameText(option.searchText).includes(normalizedQuery)),
  }))
    .filter((item) => !normalizedQuery || item.direct || item.score > 0)
    .sort((a, b) => Number(b.direct) - Number(a.direct) || b.score - a.score || a.index - b.index)
    .map((item) => item.option);
}
