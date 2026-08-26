import { FormEvent, useCallback, useEffect, useState } from "react";
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
import { ErrorBanner, Loading, PageHeader, Panel, StatusBadge } from "../components";
import type { AiAssistantStatus } from "../types";
import { LUNA_MODEL_ID } from "../types";

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
        model: LUNA_MODEL_ID,
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
        model: LUNA_MODEL_ID,
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
    const confirmed = window.confirm("退出只会结束金融收费系统专用的Luna登录，不影响Codex桌面应用。之后如需Luna辅助识别，需要重新登录。确定继续吗？");
    if (!confirmed) return;
    setAssistantWorking(true);
    setError("");
    try {
      const result = await logoutAiAssistant();
      setLoginPolling(false);
      setAssistant((current) => ({
        available: current?.available ?? true,
        model: LUNA_MODEL_ID,
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
    try {
      const response = await fetch("/api/backups", withFinancialSystemRequestHeader({ method: "POST" }));
      if (!response.ok) throw new Error("备份失败");
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `financial_system_backup_${new Date().toISOString().slice(0, 10)}.zip`;
      anchor.click();
      URL.revokeObjectURL(url);
      notify("备份已生成并通过哈希校验");
    } catch (err) { setError(err instanceof Error ? err.message : "备份失败"); }
  }

  async function restore(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    try {
      const result = await api<{ staged: boolean; restart_required: boolean }>("/api/backups/restore", { method: "POST", body: data });
      if (result.staged) notify("备份已校验并排队恢复，请关闭并重新启动系统");
      form.reset();
    } catch (err) { setError(err instanceof Error ? err.message : "恢复失败"); }
  }

  if (!info && !error) return <Loading />;
  const assistantReady = assistant?.status === "ready";
  return (
    <>
      <PageHeader title="数据与系统" subtitle="本地数据、ChatGPT Pro辅助识别及完整备份恢复" />
      {error ? <ErrorBanner message={error} /> : null}
      {info ? <div className="system-grid"><article><FolderLock /><span><small>数据根目录 Data Root</small><strong>{info.data_root}</strong></span></article><article><HardDrive /><span><small>SQLite Database</small><strong>{info.database_path}</strong></span></article><article><ArchiveRestore /><span><small>Excel Template</small><strong>{info.template_exists ? info.template_path : "模板未找到"}</strong></span></article><article><DatabaseBackup /><span><small>访问范围</small><strong>{info.local_only ? "仅本机 127.0.0.1" : "请检查网络配置"}</strong></span></article></div> : null}

      <Panel title="ChatGPT Pro 辅助识别" subtitle="使用与桌面Codex隔离的专用登录；不需要OpenAI API Key">
        <div className="assistant-settings">
          <div className="assistant-settings-main">
            <div className="assistant-settings-icon"><BrainCircuit /></div>
            <div>
              <div className="assistant-settings-title"><strong>Luna 单模型模式</strong><StatusBadge value={assistantLoading ? "CHECKING" : assistantReady ? "READY" : assistant?.status || "UNAVAILABLE"} /></div>
              <p>{assistantLoading ? "正在检查系统专用登录状态..." : assistant?.message || (assistantReady ? "系统专用ChatGPT登录已就绪，可以在账单导入页运行Luna识别。" : "当前未连接，请为本系统单独登录ChatGPT。")}</p>
            </div>
          </div>
          <div className="assistant-facts">
            <span><small>固定模型</small><strong>{LUNA_MODEL_ID}</strong></span>
            <span><small>登录方式</small><strong>{assistant?.authenticated ? "ChatGPT订阅已登录" : "尚未登录"}</strong>{assistant?.plan_type ? <em>内部标识：{assistant.plan_type}</em> : null}</span>
            <span><small>登录状态</small><strong>{assistant?.authenticated ? "已登录" : "未登录"}</strong></span>
            <span><small>Luna可用性</small><strong>{assistant?.model_available === false ? "不可用 · 转人工" : assistantReady ? "可用" : "等待连接"}</strong></span>
          </div>
          {assistant?.user_code ? <div className="device-code"><span>浏览器验证代码</span><strong>{assistant.user_code}</strong></div> : null}
          <div className="assistant-actions">
            <button className="ghost" type="button" disabled={assistantWorking} onClick={() => void refreshAssistant(true)}><RefreshCw size={15} />刷新状态</button>
            {assistant?.authenticated ? <button className="danger" type="button" disabled={assistantWorking} onClick={() => void logout()}><LogOut size={15} />退出ChatGPT Pro</button> : <button className="primary" type="button" disabled={assistantWorking || assistantLoading || assistant?.available === false} onClick={() => void login()}><LogIn size={15} />{assistantWorking ? "正在启动登录..." : "登录ChatGPT Pro"}</button>}
          </div>
        </div>
      </Panel>

      <div className="split-layout">
        <Panel title="建立完整备份" subtitle="包含数据库、账单原件、附件及导出文件"><div className="backup-action"><DatabaseBackup size={38} /><p>备份采用SQLite安全快照，并为每个文件写入SHA-256校验值。</p><button className="primary" onClick={() => void backup()}>生成并下载ZIP备份</button></div></Panel>
        <Panel title="恢复备份" subtitle="先校验所有文件，重启后才正式替换数据"><form className="backup-action" onSubmit={(event) => void restore(event)}><ArchiveRestore size={38} /><input name="file" type="file" accept=".zip" required /><button className="danger" type="submit">校验并安排恢复</button></form></Panel>
      </div>
    </>
  );
}
