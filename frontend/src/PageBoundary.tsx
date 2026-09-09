import { Component, type PropsWithChildren } from "react";
import { AlertCircle, RotateCcw } from "lucide-react";

/** Keep navigation available if a page or its lazy-loaded bundle fails. */
export default class PageBoundary extends Component<PropsWithChildren, { failed: boolean }> {
  state = { failed: false };

  static getDerivedStateFromError() { return { failed: true }; }

  render() {
    if (!this.state.failed) return this.props.children;
    return <section className="panel page-failure" role="alert">
      <AlertCircle size={32} aria-hidden="true" />
      <h1>页面暂时无法显示</h1>
      <p>请刷新页面重试；已提交的记录保存在本机，未提交的表单需要重新填写。</p>
      <button className="primary" onClick={() => window.location.reload()}><RotateCcw size={17} aria-hidden="true" />刷新页面</button>
    </section>;
  }
}
