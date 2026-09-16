import {
  Component,
  lazy,
  Suspense,
  type ErrorInfo,
  type ReactNode,
} from "react";
import { CircleAlert, LoaderCircle, SearchX } from "lucide-react";
import { Link, Navigate, Route, Routes, useLocation } from "react-router-dom";
import { AppShell } from "../components/AppShell";
import { Button, EmptyState } from "../components/ui";

const ProjectCenter = lazy(() =>
  import("../features/projects/ProjectCenter").then((module) => ({
    default: module.ProjectCenter,
  })),
);
const ProjectOverview = lazy(() =>
  import("../features/workspace/ProjectOverview").then((module) => ({
    default: module.ProjectOverview,
  })),
);
const ResearchMap = lazy(() =>
  import("../features/workspace/ResearchMap").then((module) => ({
    default: module.ResearchMap,
  })),
);
const WikiWorkbench = lazy(() =>
  import("../features/wiki/WikiWorkbench").then((module) => ({
    default: module.WikiWorkbench,
  })),
);
const SearchPage = lazy(() =>
  import("../features/search/SearchPage").then((module) => ({
    default: module.SearchPage,
  })),
);
const LegacyGateway = lazy(() =>
  import("../features/workspace/LegacyGateway").then((module) => ({
    default: module.LegacyGateway,
  })),
);
const CodeWorkspace = lazy(() =>
  import("../features/code/CodeWorkspace").then((module) => ({
    default: module.CodeWorkspace,
  })),
);
const ResearchAssetsWorkspace = lazy(() =>
  import("../features/assets/ResearchAssetsWorkspace").then((module) => ({
    default: module.ResearchAssetsWorkspace,
  })),
);
const RelationReviewWorkspace = lazy(() =>
  import("../features/relations/RelationReviewWorkspace").then((module) => ({
    default: module.RelationReviewWorkspace,
  })),
);
const SessionOverview = lazy(() =>
  import("../features/sessions/SessionWorkspace").then((module) => ({
    default: module.SessionOverview,
  })),
);
const SessionDetail = lazy(() =>
  import("../features/sessions/SessionWorkspace").then((module) => ({
    default: module.SessionDetail,
  })),
);
const SessionGraphWorkspace = lazy(() =>
  import("../features/sessions/SessionGraphWorkspace").then((module) => ({
    default: module.SessionGraphWorkspace,
  })),
);
const CodexBridgeWorkspace = lazy(() =>
  import("../features/codex/CodexBridgeWorkspace").then((module) => ({
    default: module.CodexBridgeWorkspace,
  })),
);
const AgentsWorkspace = lazy(() =>
  import("../features/agents/AgentsWorkspace").then((module) => ({
    default: module.AgentsWorkspace,
  })),
);
const RagOpsPage = lazy(() =>
  import("../features/ops/RagOpsPage").then((module) => ({
    default: module.RagOpsPage,
  })),
);

export function RouteLoadingState() {
  return (
    <div
      className="page-loading"
      role="status"
      aria-live="polite"
      aria-busy="true"
    >
      <LoaderCircle className="is-spinning" aria-hidden="true" size={20} />
      <span>正在加载工作区…</span>
    </div>
  );
}

export function RouteErrorState({ onRetry }: { onRetry: () => void }) {
  return (
    <div role="alert">
      <EmptyState
        icon={<CircleAlert aria-hidden="true" size={24} />}
        title="这个页面暂时无法显示"
        description="页面模块加载或渲染失败。你可以重试当前页面；如果问题持续，重新加载应用会保留当前地址。"
        action={
          <div>
            <Button type="button" variant="primary" onClick={onRetry}>
              重试当前页面
            </Button>
            <Button
              type="button"
              variant="quiet"
              onClick={() => window.location.reload()}
            >
              重新加载应用
            </Button>
          </div>
        }
      />
    </div>
  );
}

type RouteErrorBoundaryProps = {
  children: ReactNode;
  resetKey: string;
};

type RouteErrorBoundaryState = {
  error: Error | null;
  resetKey: string;
};

export class RouteErrorBoundary extends Component<
  RouteErrorBoundaryProps,
  RouteErrorBoundaryState
> {
  state: RouteErrorBoundaryState = {
    error: null,
    resetKey: this.props.resetKey,
  };

  static getDerivedStateFromError(
    error: Error,
  ): Partial<RouteErrorBoundaryState> {
    return { error };
  }

  static getDerivedStateFromProps(
    props: RouteErrorBoundaryProps,
    state: RouteErrorBoundaryState,
  ): Partial<RouteErrorBoundaryState> | null {
    if (props.resetKey !== state.resetKey) {
      return { error: null, resetKey: props.resetKey };
    }
    return null;
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("Route rendering failed", error, info);
  }

  render() {
    if (this.state.error) {
      return <RouteErrorState onRetry={() => this.setState({ error: null })} />;
    }
    return this.props.children;
  }
}

function RouteFrame({ children }: { children: ReactNode }) {
  const location = useLocation();
  const resetKey = `${location.pathname}${location.search}`;
  return (
    <RouteErrorBoundary resetKey={resetKey}>
      <Suspense fallback={<RouteLoadingState />}>{children}</Suspense>
    </RouteErrorBoundary>
  );
}

export function NotFoundState() {
  return (
    <EmptyState
      icon={<SearchX aria-hidden="true" size={24} />}
      title="没有找到这个页面"
      description="地址可能已失效，或对应工作区还没有开放。"
      action={
        <Link className="button button--primary" to="/projects">
          返回项目中心
        </Link>
      }
    />
  );
}

function route(element: ReactNode) {
  return <RouteFrame>{element}</RouteFrame>;
}

export function App() {
  return (
    <Routes>
      <Route element={route(<AppShell />)}>
        <Route index element={<Navigate to="/projects" replace />} />
        <Route path="/projects" element={route(<ProjectCenter />)} />
        <Route path="/search" element={route(<SearchPage />)} />
        <Route path="/ops" element={route(<RagOpsPage />)} />
        <Route
          path="/p/:projectId/overview"
          element={route(<ProjectOverview />)}
        />
        <Route path="/p/:projectId/map" element={route(<ResearchMap />)} />
        <Route path="/p/:projectId/wiki" element={route(<WikiWorkbench />)} />
        <Route path="/p/:projectId/code" element={route(<CodeWorkspace />)} />
        <Route
          path="/p/:projectId/sessions"
          element={route(<SessionOverview />)}
        />
        <Route
          path="/p/:projectId/sessions/:threadId/graph"
          element={route(<SessionGraphWorkspace />)}
        />
        <Route
          path="/p/:projectId/sessions/:threadId"
          element={route(<SessionDetail />)}
        />
        <Route
          path="/p/:projectId/codex"
          element={route(<CodexBridgeWorkspace />)}
        />
        <Route
          path="/p/:projectId/agents"
          element={route(<AgentsWorkspace />)}
        />
        <Route
          path="/p/:projectId/documents"
          element={route(<ResearchAssetsWorkspace mode="documents" />)}
        />
        <Route
          path="/p/:projectId/experiments"
          element={route(<ResearchAssetsWorkspace mode="experiments" />)}
        />
        <Route
          path="/p/:projectId/evidence"
          element={route(<ResearchAssetsWorkspace mode="evidence" />)}
        />
        <Route
          path="/p/:projectId/relations"
          element={route(<RelationReviewWorkspace />)}
        />
        <Route path="/p/:projectId/ops" element={route(<RagOpsPage />)} />
        <Route
          path="/p/:projectId/:module"
          element={route(<LegacyGateway />)}
        />
        <Route path="*" element={<NotFoundState />} />
      </Route>
    </Routes>
  );
}
