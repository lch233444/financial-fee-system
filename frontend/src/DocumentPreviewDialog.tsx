import { useEffect, useId, useRef, useState } from "react";
import { Download, X } from "lucide-react";
import { fetchBlob } from "./api";
import { ErrorBanner, Loading } from "./components";

export type PreviewDocument = { path: string; title: string; filename?: string };

export default function DocumentPreviewDialog({ document: source, onClose }: {
  document: PreviewDocument; onClose: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  const [preview, setPreview] = useState<{ url: string; type: string } | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    const element = dialog.current!;
    const previousFocus = document.activeElement as HTMLElement | null;
    element.showModal();
    return () => { element.close(); if (previousFocus?.isConnected) previousFocus.focus(); };
  }, []);
  useEffect(() => {
    const abort = new AbortController();
    let url: string | null = null;
    setPreview(null);
    setError("");
    fetchBlob(source.path, { signal: abort.signal }).then((blob) => {
      if (abort.signal.aborted) return;
      url = URL.createObjectURL(blob);
      setPreview({ url, type: blob.type });
    }).catch((err: Error) => { if (!abort.signal.aborted) setError(err.message); });
    return () => { abort.abort(); if (url) URL.revokeObjectURL(url); };
  }, [source.path]);
  return <dialog className="payment-evidence-dialog document-preview-dialog" ref={dialog} aria-labelledby={titleId}
    onCancel={(event) => { event.preventDefault(); onClose(); }}>
    <header><div><h2 id={titleId}>{source.title}</h2>{source.filename ? <p>{source.filename}</p> : null}</div>
      <div className="document-preview-actions">
        {preview && source.filename ? <a className="secondary" href={preview.url} download={source.filename}><Download size={17} aria-hidden="true" />下载</a> : null}
        <button className="ghost icon-button" type="button" aria-label="关闭预览" onClick={onClose}><X size={20} /></button>
      </div>
    </header>
    {error ? <ErrorBanner message={error} /> : !preview ? <Loading /> : <div className="payment-proof-preview">
      {/^image\/(png|jpeg)$/.test(preview.type) ? <img src={preview.url} alt={source.title} />
        : preview.type === "application/pdf" ? <object data={`${preview.url}#toolbar=0`} type="application/pdf" aria-label={`${source.title} PDF`}><p>浏览器暂不能显示此PDF。{source.filename ? "请使用下载按钮查看。" : "请使用支持PDF预览的浏览器查看。"}</p></object>
        : <p>此文件格式暂不支持弹窗预览。{source.filename ? "请使用下载按钮查看。" : ""}</p>}
    </div>}
  </dialog>;
}
