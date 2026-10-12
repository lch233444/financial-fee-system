import { type PropsWithChildren } from "react";

/** Switch tasks without discarding the forms mounted inside TaskView. */
export function TaskTabs<T extends string>({ label, value, onChange, items, disabled = false }: {
  label: string; value: T; onChange: (value: T) => void;
  items: { id: T; label: string; detail?: string }[]; disabled?: boolean;
}) {
  return <div className="task-tabs" role="tablist" aria-label={label}>{items.map((item, index) =>
    <button type="button" role="tab" id={`${item.id}-tab`} aria-controls={`${item.id}-panel`}
      key={item.id} aria-selected={value === item.id} tabIndex={value === item.id ? 0 : -1}
      disabled={disabled} onClick={() => onChange(item.id)} onKeyDown={(event) => {
        const next = event.key === "ArrowRight" ? (index + 1) % items.length
          : event.key === "ArrowLeft" ? (index + items.length - 1) % items.length
          : event.key === "Home" ? 0 : event.key === "End" ? items.length - 1 : -1;
        if (next < 0 || disabled) return;
        event.preventDefault(); onChange(items[next].id);
        document.getElementById(`${items[next].id}-tab`)?.focus();
      }}><strong>{item.label}</strong>{item.detail ? <span>{item.detail}</span> : null}</button>
  )}</div>;
}

export function TaskView({ id, active, children }: PropsWithChildren<{ id: string; active: boolean }>) {
  return <div id={`${id}-panel`} className="task-view" role="tabpanel" aria-labelledby={`${id}-tab`} hidden={!active}>{children}</div>;
}

export function TaskHelp({ title, children }: PropsWithChildren<{ title: string }>) {
  return <details className="task-help"><summary>{title}</summary><div>{children}</div></details>;
}
