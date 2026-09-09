import { useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Check, ChevronDown, Search, X } from "lucide-react";

type Option = { value: string; label: string };

export function matchesSearch(text: string, query: string) {
  return text.normalize("NFKC").toLocaleLowerCase().includes(query.trim().normalize("NFKC").toLocaleLowerCase());
}

export default function SearchableSelect({
  label, value, onChange, options, name, required = false, disabled = false,
  placeholder = "请选择", searchPlaceholder = "输入客户名称搜索…",
}: {
  label: string; value: string; onChange: (value: string) => void; options: Option[];
  name?: string; required?: boolean; disabled?: boolean; placeholder?: string; searchPlaceholder?: string;
}) {
  const id = useId();
  const input = useRef<HTMLInputElement>(null);
  const root = useRef<HTMLDivElement>(null);
  const popup = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(-1);
  const [position, setPosition] = useState({ top: 0, left: 0, width: 0, maxHeight: 280 });
  const selected = options.find((option) => option.value === value);
  const matches = useMemo(() => options.filter((option) => matchesSearch(option.label, query)), [options, query]);
  // Limit rendered options while allowing a focused name search over the complete list.
  const visible = matches.slice(0, 100);

  useEffect(() => {
    input.current?.setCustomValidity(required && !selected ? `请从列表选择${label}` : "");
  }, [required, selected, label, query]);

  useEffect(() => { if (disabled) setOpen(false); }, [disabled]);

  useLayoutEffect(() => {
    if (!open) return;
    const place = () => {
      const box = input.current?.getBoundingClientRect();
      if (!box) return;
      const available = window.innerHeight - box.bottom - 16;
      const above = available < 220 && box.top > available;
      const height = Math.min(320, Math.max(120, above ? box.top - 16 : available));
      setPosition({ left: Math.max(8, Math.min(box.left, window.innerWidth - box.width - 8)), width: Math.min(box.width, window.innerWidth - 16), top: above ? Math.max(8, box.top - height - 6) : box.bottom + 6, maxHeight: height });
    };
    place();
    const outside = (event: PointerEvent) => {
      if (!root.current?.contains(event.target as Node) && !popup.current?.contains(event.target as Node)) setOpen(false);
    };
    // Follow the anchor on page scroll, including nested financial tables.
    window.addEventListener("resize", place);
    window.addEventListener("scroll", place, true);
    document.addEventListener("pointerdown", outside);
    return () => {
      window.removeEventListener("resize", place);
      window.removeEventListener("scroll", place, true);
      document.removeEventListener("pointerdown", outside);
    };
  }, [open]);

  useEffect(() => {
    if (open && active >= 0) document.getElementById(`${id}-option-${active}`)?.scrollIntoView?.({ block: "nearest" });
  }, [active, id, open]);

  function show() { if (!disabled) { setQuery(""); setActive(-1); setOpen(true); } }
  function choose(option: Option) {
    onChange(option.value);
    setQuery("");
    setOpen(false);
    setActive(-1);
  }

  return <div className="searchable-select" ref={root}>
    <div className="combobox-control">
      <Search size={17} aria-hidden="true" />
      <input ref={input} type="text" role="combobox" aria-label={label} aria-expanded={open}
        aria-autocomplete="list" aria-controls={open ? `${id}-list` : undefined}
        aria-activedescendant={open && active >= 0 && visible[active] ? `${id}-option-${active}` : undefined}
        aria-describedby={open ? `${id}-hint` : undefined}
        autoComplete="off" value={open ? query : selected?.label || ""} disabled={disabled}
        required={required} placeholder={open ? searchPlaceholder : `${placeholder} · 可搜索`}
        onFocus={show} onClick={() => { if (!open) show(); }}
        onChange={(event) => { setQuery(event.target.value); setActive(-1); setOpen(true); }}
        onBlur={() => setOpen(false)}
        onKeyDown={(event) => {
          if (event.nativeEvent.isComposing) return;
          if (event.key === "ArrowDown" || event.key === "ArrowUp") {
            event.preventDefault();
            if (!open) { show(); return; }
            setActive((index) => Math.max(0, Math.min(visible.length - 1, index + (event.key === "ArrowDown" ? 1 : -1))));
          } else if (event.key === "Enter") {
            event.preventDefault();
            if (open && visible[active]) choose(visible[active]);
            else if (!open) show();
          } else if (event.key === "Escape") { event.preventDefault(); setOpen(false); setQuery(""); }
        }} />
      {name ? <input type="hidden" name={name} value={value} disabled={disabled} /> : null}
      {!required && value ? <button className="combobox-clear icon-button" type="button" disabled={disabled} aria-label={`清空${label}`} onMouseDown={(event) => event.preventDefault()} onClick={() => { onChange(""); setOpen(false); }}><X size={15} /></button> : <ChevronDown size={16} aria-hidden="true" />}
    </div>
    {open && !disabled ? createPortal(<div className="combobox-popup" ref={popup} style={position} onMouseDown={(event) => event.preventDefault()}>
      <div id={`${id}-list`} role="listbox" aria-label={`${label}候选`} className="combobox-options">
        {visible.map((option, index) => <div key={option.value} id={`${id}-option-${index}`} role="option" aria-selected={option.value === value}
          className={`combobox-option ${index === active ? "highlighted" : ""}`}
          onMouseMove={() => setActive(index)} onClick={() => choose(option)}>
          <span>{option.label}</span>{option.value === value ? <Check size={17} aria-hidden="true" /> : null}
        </div>)}
        {!visible.length ? <div className="combobox-empty">没有匹配项，请更换关键词</div> : null}
      </div>
      <div id={`${id}-hint`} className="combobox-hint" role="status">
        {matches.length > 100 ? `找到 ${matches.length} 项，显示前100项；输入更多关键词缩小范围。` : `${matches.length} 个选项 · ↑↓ 移动，Enter 选择`}
        {selected ? <span>当前选择已保留：{selected.label}</span> : null}
      </div>
    </div>, document.body) : null}
  </div>;
}
