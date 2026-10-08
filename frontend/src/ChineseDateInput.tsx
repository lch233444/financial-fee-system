import { type InputHTMLAttributes, useEffect, useRef, useState } from "react";
import { CalendarDays } from "lucide-react";

type Props = Omit<InputHTMLAttributes<HTMLInputElement>, "type" | "value" | "defaultValue" | "onChange"> & {
  value?: string;
  defaultValue?: string;
  onValueChange?: (value: string) => void;
};

function parseDate(text: string): string | null {
  if (!text.trim()) return "";
  const match = text.trim().match(/^(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})日?$/);
  if (!match) return null;
  const [, y, m, d] = match;
  const date = new Date(0);
  date.setUTCFullYear(Number(y), Number(m) - 1, Number(d));
  if (Number(y) < 1 || date.getUTCFullYear() !== Number(y) || date.getUTCMonth() + 1 !== Number(m) || date.getUTCDate() !== Number(d)) return null;
  return `${y}-${m.padStart(2, "0")}-${d.padStart(2, "0")}`;
}

const displayDate = (value: string) => value ? value.replace(/^(\d{4})-(\d{2})-(\d{2})$/, "$1年$2月$3日") : "";

/** Chinese display and prompts; the submitted value remains an ISO calendar date. */
export default function ChineseDateInput({ value, defaultValue = "", onValueChange, name, min, max, disabled, readOnly, ...props }: Props) {
  const [iso, setIso] = useState(value ?? defaultValue);
  const [text, setText] = useState(displayDate(value ?? defaultValue));
  const input = useRef<HTMLInputElement>(null);
  const lastEmitted = useRef(value ?? defaultValue);
  useEffect(() => {
    if (value !== undefined && value !== lastEmitted.current) {
      setIso(value); setText(displayDate(value)); lastEmitted.current = value;
    }
  }, [value]);
  useEffect(() => {
    const parsed = parseDate(text);
    const error = parsed === null ? "请填写有效日期，例如2026年10月08日" : parsed && min && parsed < String(min) ? `日期不得早于${displayDate(String(min))}` : parsed && max && parsed > String(max) ? `日期不得晚于${displayDate(String(max))}` : "";
    input.current?.setCustomValidity(error);
  }, [text, min, max]);
  useEffect(() => {
    const form = input.current?.form;
    const reset = () => { const next = value ?? defaultValue; setIso(next); setText(displayDate(next)); lastEmitted.current = next; };
    form?.addEventListener("reset", reset);
    return () => form?.removeEventListener("reset", reset);
  }, [value, defaultValue]);
  function change(next: string) {
    setText(next);
    const parsed = parseDate(next);
    if (parsed !== null) { setIso(parsed); lastEmitted.current = parsed; onValueChange?.(parsed); }
  }
  return <span className="chinese-date-input">
    <input {...props} ref={input} type="text" inputMode="numeric" placeholder="年/月/日" value={text} disabled={disabled} readOnly={readOnly} onChange={(event) => change(event.target.value)} onBlur={(event) => { const parsed = parseDate(text); if (parsed !== null) setText(displayDate(parsed)); props.onBlur?.(event); }} />
    <input type="hidden" name={name} value={iso} disabled={disabled} />
    <span className="date-picker-control"><CalendarDays size={18} aria-hidden="true" /><input type="date" lang="zh-CN" aria-label={`打开日历：${props["aria-label"] || "日期"}`} value={iso} min={min} max={max} disabled={disabled || readOnly} onChange={(event) => change(displayDate(event.target.value))} /></span>
  </span>;
}
