import { useId, useLayoutEffect, useRef, useState } from "react";
import { RotateCcw, ZoomIn, ZoomOut } from "lucide-react";

// Each source is mounted separately by the preview dialogs, so zoom and scroll
// never carry over to another financial document.
export default function ZoomableImage({ src, alt }: { src: string; alt: string }) {
  const viewport = useRef<HTMLDivElement>(null);
  const hintId = useId();
  const [zoom, setZoom] = useState(100);
  const [size, setSize] = useState({ width: 0, height: 0 });
  const [natural, setNatural] = useState({ width: 0, height: 0 });
  const [failed, setFailed] = useState(false);

  useLayoutEffect(() => {
    const element = viewport.current!;
    const measure = () => setSize({ width: element.clientWidth, height: element.clientHeight });
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  const ready = !failed && natural.width > 0 && natural.height > 0 && size.width > 0 && size.height > 0;
  const fit = ready ? Math.min(1, size.width / natural.width, size.height / natural.height) : 1;
  const width = ready ? natural.width * fit * zoom / 100 : undefined;
  const height = ready ? natural.height * fit * zoom / 100 : undefined;
  function reset() {
    setZoom(100);
    if (viewport.current) {
      viewport.current.scrollLeft = 0;
      viewport.current.scrollTop = 0;
    }
  }

  return <div className="zoomable-image">
    <div className="image-zoom-controls" role="group" aria-label="图片缩放">
      <button type="button" className="secondary" aria-label="缩小图片" disabled={!ready || zoom <= 50} onClick={() => setZoom((current) => Math.max(50, current - 25))}><ZoomOut size={17} aria-hidden="true" />缩小</button>
      <span role="status" aria-label="图片缩放比例" aria-live="polite" aria-atomic="true">{failed ? "图片不可用" : ready ? `${zoom}%` : "图片加载中…"}</span>
      <button type="button" className="secondary" aria-label="放大图片" disabled={!ready || zoom >= 400} onClick={() => setZoom((current) => Math.min(400, current + 25))}><ZoomIn size={17} aria-hidden="true" />放大</button>
      <button type="button" className="ghost" aria-label="复位图片（适应窗口）" disabled={!ready} onClick={reset}><RotateCcw size={17} aria-hidden="true" />复位</button>
    </div>
    <p className="image-zoom-hint" id={hintId}>100%为适应窗口；放大后可滚动查看。复位可恢复完整图片。</p>
    <div className="image-zoom-viewport" ref={viewport} role="region" aria-label="可滚动的凭证图片" aria-describedby={hintId} tabIndex={0}>
      {failed ? <p role="alert">图片无法显示，请核对原件后重新打开预览。</p> : <div className="image-zoom-stage" style={{ width, height }}>
        <img src={src} alt={alt} style={{ width, height }} onLoad={(event) => setNatural({ width: event.currentTarget.naturalWidth, height: event.currentTarget.naturalHeight })} onError={() => setFailed(true)} />
      </div>}
    </div>
  </div>;
}
