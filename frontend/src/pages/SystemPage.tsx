import { FormEvent, useCallback, useEffect, useRef, useState } from "react";
import {
  ArchiveRestore,
  BrainCircuit,
  DatabaseBackup,
  FolderLock,
  HardDrive,
  LogIn,
  LogOut,
  RefreshCw,
} from "lucide-react";
import {
  api,
  getAiAssistantStatus,
  logoutAiAssistant,
  startAiAssistantLogin,
  withFinancialSystemRequestHeader,
} from "../api";
import { ErrorBanner, Field, Loading, PageHeader, Panel, StatusBadge, SectionNav } from "../components";
import type { AiAssistantStatus } from "../types";
import { SOL_MODEL_ID } from "../types";

type SystemInfo = {
  app_name: string;
  data_root: string;
  database_path: string;
  template_path: string;
  template_exists: boolean;
  local_only: boolean;
};

export default function SystemPage({ notify }: { notify: (message: string) => void }) {
  const [info, setInfo] = useState<SystemInfo | null>(null);
  const [assistant, setAssistant] = useState<AiAssistantStatus | null>(null);
  const [assistantLoading, setAssistantLoading] = useState(true);
  const [assistantWorking, setAssistantWorking] = useState(false);
  const [loginPolling, setLoginPolling] = useState(false);
  const [backupBusy, setBackupBusy] = useState(false);
  const backupBusyRef = useRef(false);
  const [packageYear, setPackageYear] = useState(() => new Date().getFullYear());
  const [packageQuarter, setPackageQuarter] = useState(
    () => Math.floor(new Date().getMonth() / 3) + 1,
  );
  const [restoreBusy, setRestoreBusy] = useState(false);
  const restoreBusyRef = useRef(false);
  const [error, setError] = useState("");

  const refreshAssistant = useCallback(async (showError = false) => {
    try {
      const result = await getAiAssistantStatus();
      setAssistant(result);
      if (result.authenticated) setLoginPolling(false);
      return result;
    } catch (err) {
      if (showError) setError(err instanceof Error ? err.message : "无法读取ChatGPT Pro状态");
      setAssistant({
        available: false,
        authenticated: false,
        status: "unavailable",
        model: SOL_MODEL_ID,
        message: err instanceof Error ? err.message : "辅助识别服务不可用",
      });
      return null;
    } finally {
      setAssistantLoading(false);
    }
  }, []);

  useEffect(() => {
    api<SystemInfo>("/api/system-info").then(setInfo).catch((err: Error) => setError(err.message));
    void refreshAssistant();
  }, [refreshAssistant]);

  useEffect(() => {
    if (assistant?.authenticated || !loginPolling) return;
    const timer = window.setInterval(() => void refreshAssistant(), 2500);
    const timeout = window.setTimeout(() => setLoginPolling(false), 120000);
    return () => { window.clearInterval(timer); window.clearTimeout(timeout); };
  }, [assistant?.authenticated, loginPolling, refreshAssistant]);

  async function login() {
    setAssistantWorking(true);
    setError("");
    const authWindow = window.open("", "_blank");
    if (authWindow) authWindow.opener = null;
    try {
      const result = await startAiAssistantLogin();
      setLoginPolling(Boolean(result.login_started && !result.already_authenticated));
      setAssistant((current) => ({
        available: current?.available ?? true,
        authenticated: current?.authenticated ?? false,
        model: SOL_MODEL_ID,
        ...current,
        ...result,
        ...(result.already_authenticated ? { authenticated: true } : {}),
      }));
      const authUrl = result.auth_url || result.login_url || result.verification_url;
      if (authUrl && authWindow) {
        authWindow.location.replace(authUrl);
      } else if (authUrl) {
        const opened = window.open(authUrl, "_blank", "noopener,noreferrer");
        if (!opened) setError("浏览器阻止了登录窗口，请允许弹出窗口后重试。");
      } else {
        authWindow?.close();
      }
      notify(result.authenticated ? "ChatGPT Pro已连接" : "登录流程已启动，完成浏览器验证后本页会自动更新");
    } catch (err) {
      authWindow?.close();
      setError(err instanceof Error ? err.message : "无法启动ChatGPT Pro登录");
    } finally {
      setAssistantWorking(false);
    }
  }

  async function logout() {
    const confirmed = window.confirm("退出只会结束金融收费系统专用的Sol登录，不影响Codex桌面应用。之后如需Sol辅助识别，需要重新登录。确定继续吗？");
    if (!confirmed) return;
    setAssistantWorking(true);
    setError("");
    try {
      const result = await logoutAiAssistant();
      setLoginPolling(false);
      setAssistant((current) => ({
        available: current?.available ?? true,
        model: SOL_MODEL_ID,
        ...current,
        ...result,
        authenticated: false,
        model_available: false,
      }));
      notify("已退出本机ChatGPT Pro辅助识别");
    } catch (err) {
      setError(err instanceof Error ? err.message : "退出失败");
    } finally {
      setAssistantWorking(false);
    }
  }

  async function backup() {
    if (backupBusyRef.current) return;
    backupBusyRef.current = true;
    setBackupBusy(true);
    setError("");
    try {
      const response = await fetch(
        `/api/backups?year=${packageYear}&quarter=${packageQuarter}`,
        withFinancialSystemRequestHeader({ method: "POST" }),
      );
      if (!response.ok) throw new Error("数据包导出失败");
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      const disposition = response.headers.get("content-disposition") || "";
      const serverName = /filename="?([^";]+)"?/i.exec(disposition)?.[1];
      anchor.download = serverName || `financial_system_data_package_${packageYear}_Q${packageQuarter}.zip`;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      URL.revokeObjectURL(url);
      notify(`${packageYear} Q${packageQuarter}完整数据包已生成并通过校验`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "数据包导出失败");
    } finally {
      backupBusyRef.current = false;
      setBackupBusy(false);
    }
  }

  async function restore(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (restoreBusyRef.current) return;
    const confirmed = window.confirm(
      "导入数据包会完整覆盖这台电脑现有的数据库、账单原件、附件和导出文件，不能与现有资料合并。\n\n请确认这台电脑不需要保留当前资料，并且数据包来自相同版本的系统。确定继续吗？",
    );
    if (!confirmed) return;
    restoreBusyRef.current = true;
    setRestoreBusy(true);
    const form = event.currentTarget;
    const data = new FormData(form);
    setError("");
    try {
      const result = await api<{ staged: boolean; restart_required: boolean; shutdown_scheduled: boolean; cleanup_warning: string | null }>("/api/backups/restore", { method: "POST", body: data });
      if (result.staged) {
        notify(`数据包已校验并排队导入，系统正在安全退出；退出后请重新启动以完成导入${result.cleanup_warning ? `；${result.cleanup_warning}` : ""}`);
      }
      form.reset();
    } catch (err) {
      setError(err instanceof Error ? err.message : "数据包导入失败");
    } finally {
      restoreBusyRef.current = false;
      setRestoreBusy(false);
    }
  }

  if (!info && !error) return <Loading />;
  const assistantReady = assistant?.status === "ready";
  return (
    <>
      <PageHeader title="数据与系统" subtitle="本地数据、ChatGPT Pro辅助识别及完整数据包交接" />
      <SectionNav items={[{ id: "system-backup", label: "导出数据包" }, { id: "system-restore", label: "导入数据包" }, { id: "system-assistant", label: "Sol登录" }]} />
      {error ? <ErrorBanner message={error} /> : null}
      {info ? <><div className="system-grid"><article><FolderLock aria-hidden="true" /><span><small>业务数据保存位置</small><strong>{info.data_root}</strong></span></article><article><DatabaseBackup aria-hidden="true" /><span><small>运行范围与导出模板</small><strong>{info.local_only ? "仅本机运行" : "请检查网络配置"} · {info.template_exists ? "Excel模板已就绪" : "Excel模板未找到"}</strong></span></article></div><details className="technical-details"><summary>查看数据库与模板位置</summary><div><p><HardDrive size={16} aria-hidden="true" /><span><strong>数据库</strong>{info.database_path}</span></p><p><ArchiveRestore size={16} aria-hidden="true" /><span><strong>Excel模板</strong>{info.template_path}</span></p></div></details></> : null}

      <Panel id="system-assistant" title="ChatGPT Pro 辅助识别" subtitle="使用与桌面Codex隔离的专用登录；不需要OpenAI API Key">
        <div className="assistant-settings">
          <div className="assistant-settings-main">
            <div className="assistant-settings-icon"><BrainCircuit /></div>
            <div>
              <div className="assistant-settings-title"><strong>Sol 单模型模式</strong><StatusBadge value={assistantLoading ? "CHECKING" : assistantReady ? "READY" : assistant?.status || "UNAVAILABLE"} /></div>
              <p>{assistantLoading ? "正在检查系统专用登录状态..." : assistant?.message || (assistantReady ? "系统专用ChatGPT登录已就绪，可以在账单导入页运行Sol识别。" : "当前未连接，请为本系统单独登录ChatGPT。")}</p>
            </div>
          </div>
          <div className="assistant-facts">
            <span><small>固定模型</small><strong>{SOL_MODEL_ID}</strong></span>
            <span><small>登录方式</small><strong>{assistant?.authenticated ? "ChatGPT订阅已登录" : "尚未登录"}</strong>{assistant?.plan_type ? <em>内部标识：{assistant.plan_type}</em> : null}</span>
            <span><small>登录状态</small><strong>{assistant?.authenticated ? "已登录" : "未登录"}</strong></span>
            <span><small>Sol可用性</small><strong>{assistant?.model_available === false ? "不可用 · 转人工" : assistantReady ? "可用" : "等待连接"}</strong></span>
          </div>
          {assistant?.user_code ? <div className="device-code"><span>浏览器验证代码</span><strong>{assistant.user_code}</strong></div> : null}
          <div className="assistant-actions">
            <button className="ghost" type="button" disabled={assistantWorking} onClick={() => void refreshAssistant(true)}><RefreshCw size={15} />刷新状态</button>
            {assistant?.authenticated ? <button className="danger" type="button" disabled={assistantWorking} onClick={() => void logout()}><LogOut size={15} />退出ChatGPT Pro</button> : <button className="primary" type="button" disabled={assistantWorking || assistantLoading || assistant?.available === false} onClick={() => void login()}><LogIn size={15} />{assistantWorking ? "正在启动登录..." : "登录ChatGPT Pro"}</button>}
          </div>
        </div>
      </Panel>

      <div className="split-layout">
        <Panel id="system-backup" title="导出完整数据包" subtitle="供另一台同版本系统复核；包含导出时的全部资料"><div className="backup-action"><DatabaseBackup size={38} /><p>年度和季度只是检查批次标签。数据包始终包含完整数据库、账单原件、附件和导出文件，并逐项校验。</p><div className="data-package-period"><Field label="检查年度"><input type="number" min="2000" max="2100" value={packageYear} disabled={backupBusy} onChange={(event) => setPackageYear(Number(event.target.value))} /></Field><Field label="检查季度"><select value={packageQuarter} disabled={backupBusy} onChange={(event) => setPackageQuarter(Number(event.target.value))}><option value={1}>Q1</option><option value={2}>Q2</option><option value={3}>Q3</option><option value={4}>Q4</option></select></Field></div><button className="primary" disabled={backupBusy} onClick={() => void backup()}>{backupBusy ? "正在生成数据包..." : "生成并下载ZIP数据包"}</button></div></Panel>
        <Panel id="system-restore" title="导入完整数据包" subtitle="只接受同版本数据包；导入后完整覆盖本机资料"><form className="backup-action" onSubmit={(event) => void restore(event)}><ArchiveRestore size={38} /><p>导入后，本机将显示数据包导出时的完整记录和文件。系统不会把两边资料合并。</p><input aria-label="选择完整数据包ZIP" name="file" type="file" accept=".zip" required disabled={restoreBusy} /><button className="danger" type="submit" disabled={restoreBusy}>{restoreBusy ? "正在校验数据包..." : "校验并安排导入"}</button></form></Panel>
      </div>
    </>
  );
}
