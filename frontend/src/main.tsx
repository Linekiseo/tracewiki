import React from "react";
import ReactDOM from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { HashRouter } from "react-router-dom";
import { App } from "./app/App";
import { DesktopRuntimeProvider } from "./desktop/runtime";
import "./styles/app.css";
import "./styles/workbench-polish.css";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 15_000,
      retry: 1,
      refetchOnWindowFocus: false,
    },
  },
});

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <HashRouter>
        <DesktopRuntimeProvider>
          <App />
        </DesktopRuntimeProvider>
      </HashRouter>
    </QueryClientProvider>
  </React.StrictMode>,
);
