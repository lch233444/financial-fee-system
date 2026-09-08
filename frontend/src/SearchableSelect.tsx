import { useId, useState } from "react";

type Option = { value: string; label: string };

export function matchesSearch(text: string, query: string) {
  return text.normalize("NFKC").toLocaleLowerCase().includes(query.trim().normalize("NFKC").toLocaleLowerCase());
}

export default function SearchableSelect({
  label, value, onChange, options, name, required = false, disabled = false,
  placeholder = "请选择", searchPlaceholder = "输入客户名称搜索…",
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  options: Option[];
  name?: string;
  required?: boolean;
  disabled?: boolean;
  placeholder?: string;
  searchPlaceholder?: string;
}) {
  const [query, setQuery] = useState("");
  const hintId = useId();
  const normalized = query.trim().normalize("NFKC").toLocaleLowerCase();
  const matches = options.filter((option) => matchesSearch(option.label, normalized));
  const selected = options.find((option) => option.value === value);
  const selectedOutsideResults = selected && !matches.some((option) => option.value === value);

  return <div className="searchable-select">
    <input type="search" aria-label={`${label}搜索`} placeholder={searchPlaceholder}
      value={query} disabled={disabled} autoComplete="off"
      onChange={(event) => setQuery(event.target.value)}
      onKeyDown={(event) => { if (event.key === "Enter") event.preventDefault(); }} />
    <select aria-label={label} aria-describedby={normalized ? hintId : undefined}
      name={name} value={value} required={required} disabled={disabled}
      onChange={(event) => { onChange(event.target.value); setQuery(""); }}>
      <option value="" disabled={required}>{placeholder}</option>
      {selectedOutsideResults ? <optgroup label="当前选择（搜索不会更改已选客户）">
        <option value={selected.value}>{selected.label}</option>
      </optgroup> : null}
      {matches.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
    </select>
    {normalized ? <small id={hintId} role="status">
      {matches.length ? `找到 ${matches.length} 个选项，请选择` : "没有匹配项，请更换关键词"}
      {selectedOutsideResults ? "；当前选择已保留" : ""}
    </small> : null}
  </div>;
}
