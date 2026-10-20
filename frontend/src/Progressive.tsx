import { type PropsWithChildren, useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { ArrowLeft, CircleHelp } from "lucide-react";

/** Shared help remains available to mouse, touch and keyboard users. */
export function HelpTip({ title, children }: PropsWithChildren<{ title: string }>) {
  const id = useId();
  const trigger = useRef<HTMLButtonElement>(null);
  const popup = useRef<HTMLDivElement>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const [open, setOpen] = useState(false);
  const [pinned, setPinned] = useState(false);
  const [position, setPosition] = useState({ top: 0, left: 0 });
  const cancelClose = () => clearTimeout(timer.current);
  const close = () => { cancelClose(); setOpen(false); setPinned(false); };
  const scheduleClose = () => { cancelClose(); if (!pinned) timer.current = setTimeout(() => setOpen(false), 120); };
  useEffect(() => () => clearTimeout(timer.current), []);
  useLayoutEffect(() => {
    if (!open) return;
    const place = () => {
      const rect = trigger.current?.getBoundingClientRect();
      if (!rect) return;
      const height = popup.current?.offsetHeight || 0;
      const width = popup.current?.offsetWidth || 320;
      setPosition({
        top: Math.max(8, rect.bottom + height + 8 < window.innerHeight ? rect.bottom + 6 : rect.top - height - 6),
        left: Math.max(8, Math.min(rect.left, window.innerWidth - width - 8)),
      });
    };
    place();
    const dismiss = (event: PointerEvent) => {
      if (!trigger.current?.contains(event.target as Node) && !popup.current?.contains(event.target as Node)) close();
    };
    const escape = (event: KeyboardEvent) => { if (event.key === "Escape") close(); };
    window.addEventListener("resize", place);
    window.addEventListener("scroll", place, true);
    document.addEventListener("pointerdown", dismiss);
    document.addEventListener("keydown", escape);
    return () => {
      window.removeEventListener("resize", place);
      window.removeEventListener("scroll", place, true);
      document.removeEventListener("pointerdown", dismiss);
      document.removeEventListener("keydown", escape);
    };
  }, [open]);
  return <span className="help-tip">
    <button ref={trigger} type="button" className="help-trigger" aria-label={`${title}：帮助`} aria-expanded={open} aria-describedby={open ? id : undefined}
      onPointerEnter={() => { cancelClose(); setOpen(true); }} onPointerLeave={scheduleClose}
      onFocus={() => { cancelClose(); setOpen(true); }} onBlur={scheduleClose}
      onClick={(event) => { event.preventDefault(); if (pinned) close(); else { cancelClose(); setPinned(true); setOpen(true); } }}>
      <CircleHelp size={17} aria-hidden="true" />
    </button>
    {open ? createPortal(<div ref={popup} id={id} role="tooltip" className="help-popover" style={position}
      onPointerEnter={cancelClose} onPointerLeave={scheduleClose}><strong>{title}</strong><div>{children}</div></div>, document.body) : null}
  </span>;
}

/** Navigation only. The parent keeps the form mounted to retain unsaved values. */
export function DetailBack({ onBack, label = "返回列表", title, disabled = false }: {
  onBack: () => void; label?: string; title: string; disabled?: boolean;
}) {
  const heading = useRef<HTMLHeadingElement>(null);
  useEffect(() => { heading.current?.focus({ preventScroll: true }); heading.current?.scrollIntoView?.({ block: "start" }); }, []);
  return <div className="detail-back"><button type="button" className="ghost" onClick={onBack} disabled={disabled}><ArrowLeft size={17} aria-hidden="true" />{label}</button><h2 ref={heading} tabIndex={-1}>{title}</h2></div>;
}
