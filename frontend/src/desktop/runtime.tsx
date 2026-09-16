import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { useLocation, useNavigate } from "react-router-dom";

export const DESKTOP_BACKEND_KEY = "rag_backend_url";

export type DesktopConnectionMode = "managed_local" | "remote";

export type DesktopConnectionProfile = {
  schema: "desktop-connection-profile-v1";
  mode: DesktopConnectionMode;
  base_url: string;
  last_project_id: string | null;
};

export type DesktopBootstrap = {
  desktop: true;
  os: string;
  arch: string;
  app_version: string;
  profile: DesktopConnectionProfile;
  native_menu: boolean;
  deep_link_scheme: string;
  local_service_command_available: boolean;
};

export type DesktopLLMProfile = {
  schema: "desktop-llm-profile-v1";
  project_id: string;
  provider: "openai_compatible";
  base_url: string;
  model: string;
  key_stored: boolean;
};

export type DesktopLLMProfileInput = Omit<DesktopLLMProfile, "key_stored"> & {
  api_key?: string;
};

export type LLMRuntimeStatus = "unconfigured" | "registering" | "ready" | "error";

type DesktopHealth = "checking" | "ready" | "offline";

type DesktopRuntimeValue = {
  isDesktop: boolean;
  bootstrap: DesktopBootstrap | null;
  health: DesktopHealth;
  healthDetail: string;
  llmProfile: DesktopLLMProfile | null;
  llmStatus: LLMRuntimeStatus;
  llmDetail: string;
  refreshHealth: () => Promise<void>;
  saveProfile: (profile: DesktopConnectionProfile) => Promise<void>;
  saveLLMProfile: (input: DesktopLLMProfileInput) => Promise<DesktopLLMProfile>;
  deleteLLMProfile: (projectId: string) => Promise<void>;
  registerLLMProvider: (projectId: string) => Promise<void>;
};

const DesktopRuntimeContext = createContext<DesktopRuntimeValue>({
  isDesktop: false,
  bootstrap: null,
  health: "ready",
  healthDetail: "网页同源服务",
  llmProfile: null,
  llmStatus: "unconfigured",
  llmDetail: "仅桌面 App 支持模型配置",
  refreshHealth: async () => undefined,
  saveProfile: async () => undefined,
  saveLLMProfile: async () => { throw new Error("desktop_runtime_required"); },
  deleteLLMProfile: async () => undefined,
  registerLLMProvider: async () => undefined,
});

declare global {
  interface Window {
    __TAURI_INTERNALS__?: unknown;
  }
}

export function isDesktopRuntime() {
  return typeof window !== "undefined" && Boolean(window.__TAURI_INTERNALS__);
}

export function resolveDesktopApiUrl(path: string): string {
  if (!path.startsWith("/")) return path;
  if (typeof window === "undefined") return path;
  let configured: string | undefined;
  try {
    configured = window.localStorage?.getItem(DESKTOP_BACKEND_KEY)?.trim();
  } catch {
    return path;
  }
  if (!configured) return path;
  try {
    const base = new URL(configured);
    const loopback = ["127.0.0.1", "localhost", "::1", "[::1]"].includes(base.hostname);
    if (!((base.protocol === "http:" && loopback) || base.protocol === "https:")) return path;
    if (base.username || base.password || base.search || base.hash) return path;
    return new URL(path.replace(/^\//, ""), `${base.toString().replace(/\/$/, "")}/`).toString();
  } catch {
    return path;
  }
}

function currentProjectId(pathname: string, fallback?: string | null) {
  const match = pathname.match(/^\/p\/([^/]+)/);
  return match ? decodeURIComponent(match[1]) : fallback || null;
}

export function resolveDesktopNavigation(command: string, pathname: string, lastProjectId?: string | null) {
  if (command.startsWith("/p/") || command === "/projects" || command === "/search" || command === "/ops") {
    return command;
  }
  const projectId = currentProjectId(pathname, lastProjectId);
  if (!projectId) return command === "search" ? "/search" : "/projects";
  const modules: Record<string, string> = {
    wiki: "wiki",
    sessions: "sessions",
    agents: "agents",
    map: "map",
    code: "code",
    overview: "overview",
  };
  return modules[command]
    ? `/p/${encodeURIComponent(projectId)}/${modules[command]}`
    : command === "search"
      ? `/search?project=${encodeURIComponent(projectId)}`
      : "/projects";
}

export function parseDesktopDeepLink(value: string): string | null {
  try {
    const url = new URL(value);
    if (url.protocol !== "evidence-rag:") return null;
    if (url.hostname === "open") {
      const route = url.searchParams.get("route");
      return route?.startsWith("/") && !route.startsWith("//") ? route : null;
    }
    if (url.hostname === "project") {
      const [projectId, module = "overview"] = url.pathname.split("/").filter(Boolean);
      if (!projectId || !/^[A-Za-z0-9_.-]{1,160}$/.test(projectId)) return null;
      if (!/^(overview|wiki|map|code|sessions|agents|relations)$/.test(module)) return null;
      return `/p/${encodeURIComponent(projectId)}/${module}`;
    }
  } catch {
    return null;
  }
  return null;
}

export function DesktopRuntimeProvider({ children }: { children: ReactNode }) {
  const desktop = isDesktopRuntime();
  const [bootstrap, setBootstrap] = useState<DesktopBootstrap | null>(null);
  const [health, setHealth] = useState<DesktopHealth>(desktop ? "checking" : "ready");
  const [healthDetail, setHealthDetail] = useState(desktop ? "正在连接 RAG 服务" : "网页同源服务");
  const [llmProfile, setLLMProfile] = useState<DesktopLLMProfile | null>(null);
  const [llmStatus, setLLMStatus] = useState<LLMRuntimeStatus>("unconfigured");
  const [llmDetail, setLLMDetail] = useState("尚未配置智能模型");
  const navigate = useNavigate();
  const location = useLocation();

  const refreshHealth = async () => {
    if (!desktop) return;
    setHealth("checking");
    setHealthDetail("正在验证服务身份");
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), 3_500);
    try {
      const response = await fetch(resolveDesktopApiUrl("/health"), {
        signal: controller.signal,
        headers: { Accept: "application/json" },
      });
      const payload = (await response.json()) as { status?: string; version?: string };
      if (!response.ok || payload.status !== "ok") throw new Error("health_contract_mismatch");
      setHealth("ready");
      setHealthDetail(`RAG ${payload.version || "服务"} 已连接`);
    } catch {
      setHealth("offline");
      setHealthDetail("RAG 服务不可用；查询与写入已停用");
    } finally {
      window.clearTimeout(timeout);
    }
  };

  const saveProfile = async (profile: DesktopConnectionProfile) => {
    if (!desktop) return;
    const { invoke } = await import("@tauri-apps/api/core");
    const saved = await invoke<DesktopConnectionProfile>("set_connection_profile", { profile });
    window.localStorage.setItem(DESKTOP_BACKEND_KEY, saved.base_url);
    setBootstrap((current) => current ? { ...current, profile: saved } : current);
    await refreshHealth();
  };

  const registerLLMProvider = async (projectId: string) => {
    if (!desktop || !bootstrap || !projectId) return;
    setLLMStatus("registering");
    setLLMDetail("正在将钥匙串凭据注入本机 RAG 进程");
    try {
      const { invoke } = await import("@tauri-apps/api/core");
      await invoke("desktop_register_llm_provider", {
        input: {
          project_id: projectId,
          backend_url: bootstrap.profile.base_url,
          bearer_token: window.localStorage.getItem("rag_api_token"),
          acl_refs: window.localStorage.getItem("rag_acl_refs"),
        },
      });
      setLLMStatus("ready");
      setLLMDetail(`${llmProfile?.model || "智能模型"} 已连接；查询理解与受约束生成可用`);
    } catch {
      setLLMStatus("error");
      setLLMDetail("模型凭据未能注册到 RAG 服务；请检查服务连接与配置");
    }
  };

  const saveLLMProfile = async (input: DesktopLLMProfileInput) => {
    if (!desktop) throw new Error("desktop_runtime_required");
    const { invoke } = await import("@tauri-apps/api/core");
    const saved = await invoke<DesktopLLMProfile>("desktop_save_llm_profile", { input });
    setLLMProfile(saved);
    setLLMStatus("registering");
    setLLMDetail("模型密钥已保存到系统钥匙串，正在连接");
    if (!bootstrap) return saved;
    try {
      await invoke("desktop_register_llm_provider", {
        input: {
          project_id: saved.project_id,
          backend_url: bootstrap.profile.base_url,
          bearer_token: window.localStorage.getItem("rag_api_token"),
          acl_refs: window.localStorage.getItem("rag_acl_refs"),
        },
      });
      setLLMStatus("ready");
      setLLMDetail(`${saved.model} 已连接；查询理解与受约束生成可用`);
    } catch (error) {
      setLLMStatus("error");
      setLLMDetail("密钥已安全保存，但当前 RAG 服务无法注册模型");
      throw error;
    }
    return saved;
  };

  const deleteLLMProfile = async (projectId: string) => {
    if (!desktop) return;
    const { invoke } = await import("@tauri-apps/api/core");
    await invoke("desktop_delete_llm_profile", { projectId });
    const headers = new Headers({ "Content-Type": "application/json" });
    const token = window.localStorage.getItem("rag_api_token");
    const aclRefs = window.localStorage.getItem("rag_acl_refs");
    if (token) headers.set("Authorization", `Bearer ${token}`);
    if (aclRefs) headers.set("X-RAG-ACL-Refs", aclRefs);
    await fetch(resolveDesktopApiUrl("/v1/ai/provider"), {
      method: "DELETE",
      headers,
      body: JSON.stringify({ project_id: projectId }),
    });
    setLLMProfile(null);
    setLLMStatus("unconfigured");
    setLLMDetail("尚未配置智能模型；查询将使用本地统计理解与检索式回答");
  };

  useEffect(() => {
    if (!desktop) return;
    let active = true;
    const cleanups: Array<() => void> = [];
    void (async () => {
      const [{ invoke }, { listen }] = await Promise.all([
        import("@tauri-apps/api/core"),
        import("@tauri-apps/api/event"),
      ]);
      const value = await invoke<DesktopBootstrap>("desktop_bootstrap");
      if (!active) return;
      window.localStorage.setItem(DESKTOP_BACKEND_KEY, value.profile.base_url);
      setBootstrap(value);
      const navigateFromMenu = (command: string) => {
        const normalized = command.replace(/^\/desktop\/current\//, "");
        navigate(resolveDesktopNavigation(normalized, location.pathname, value.profile.last_project_id));
      };
      cleanups.push(await listen<string>("desktop:navigate", (event) => navigateFromMenu(event.payload)));
      cleanups.push(await listen<string[]>("desktop:deep-link", (event) => {
        const route = event.payload.map(parseDesktopDeepLink).find(Boolean);
        if (route) navigate(route);
      }));
      await refreshHealth();
    })().catch(() => {
      setHealth("offline");
      setHealthDetail("桌面运行时初始化失败");
    });
    return () => {
      active = false;
      cleanups.forEach((cleanup) => cleanup());
    };
    // Native listeners are installed once; current route is resolved at bootstrap time.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [desktop]);

  const activeProjectId = currentProjectId(location.pathname, bootstrap?.profile.last_project_id);
  useEffect(() => {
    if (!desktop || !bootstrap || !activeProjectId) {
      if (!activeProjectId) setLLMProfile(null);
      return;
    }
    let active = true;
    void (async () => {
      const { invoke } = await import("@tauri-apps/api/core");
      const profile = await invoke<DesktopLLMProfile | null>("desktop_llm_profile", {
        projectId: activeProjectId,
      });
      if (!active) return;
      setLLMProfile(profile);
      if (!profile?.key_stored) {
        setLLMStatus("unconfigured");
        setLLMDetail("尚未配置智能模型；查询将使用本地统计理解与检索式回答");
        return;
      }
      setLLMStatus("registering");
      setLLMDetail("正在从系统钥匙串恢复智能模型连接");
      try {
        await invoke("desktop_register_llm_provider", {
          input: {
            project_id: activeProjectId,
            backend_url: bootstrap.profile.base_url,
            bearer_token: window.localStorage.getItem("rag_api_token"),
            acl_refs: window.localStorage.getItem("rag_acl_refs"),
          },
        });
        if (!active) return;
        setLLMStatus("ready");
        setLLMDetail(`${profile.model} 已连接；查询理解与受约束生成可用`);
      } catch {
        if (!active) return;
        setLLMStatus("error");
        setLLMDetail("系统钥匙串中已有配置，但 RAG 服务注册失败");
      }
    })().catch(() => {
      if (!active) return;
      setLLMStatus("error");
      setLLMDetail("无法读取系统钥匙串中的模型配置");
    });
    return () => { active = false; };
  }, [activeProjectId, bootstrap, desktop]);

  const value = useMemo<DesktopRuntimeValue>(() => ({
    isDesktop: desktop,
    bootstrap,
    health,
    healthDetail,
    llmProfile,
    llmStatus,
    llmDetail,
    refreshHealth,
    saveProfile,
    saveLLMProfile,
    deleteLLMProfile,
    registerLLMProvider,
  }), [desktop, bootstrap, health, healthDetail, llmProfile, llmStatus, llmDetail]);

  return <DesktopRuntimeContext.Provider value={value}>{children}</DesktopRuntimeContext.Provider>;
}

export function useDesktopRuntime() {
  return useContext(DesktopRuntimeContext);
}
