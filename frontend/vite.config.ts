import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import { loadEnv, type Plugin } from "vite";

const localBackendOrigin =
  /https?:\/\/(?:127\.0\.0\.1|localhost|\[::1\]):8000/g;

function productionSameOriginFallbacks(): Plugin {
  return {
    name: "production-same-origin-fallbacks",
    apply: "build",
    enforce: "pre",
    transform(code, id) {
      const normalizedId = id.replace(/\\/g, "/");
      if (normalizedId.indexOf("/src/") === -1 || !localBackendOrigin.test(code)) {
        localBackendOrigin.lastIndex = 0;
        return null;
      }
      localBackendOrigin.lastIndex = 0;
      return {
        code: code.replace(localBackendOrigin, ""),
        map: null,
      };
    },
    generateBundle(_options, bundle) {
      for (const fileName in bundle) {
        const output = bundle[fileName];
        const content =
          output.type === "chunk"
            ? output.code
            : typeof output.source === "string"
              ? output.source
              : "";
        localBackendOrigin.lastIndex = 0;
        if (localBackendOrigin.test(content)) {
          throw new Error(`Local backend fallback leaked into ${fileName}`);
        }
      }
    },
  };
}

export default defineConfig(({ mode }) => {
  const environment = loadEnv(mode, ".", "RAG_");
  return {
    plugins: [react(), productionSameOriginFallbacks()],
    resolve: {
      alias: {
        "react-router-dom": new URL("./src/lib/router.tsx", import.meta.url)
          .pathname,
      },
    },
    test: {
      environment: "jsdom",
      setupFiles: "./src/test/setup.ts",
    },
    base: "/",
    build: {
      outDir: environment.RAG_FRONTEND_OUT_DIR || "../web",
      assetsDir: "app",
      emptyOutDir: false,
      sourcemap: false,
      rollupOptions: {
        output: {
          manualChunks: {
            "vendor-react": [
              "@tanstack/react-query",
              "react",
              "react-dom",
            ],
            "vendor-markdown": ["react-markdown", "remark-gfm"],
          },
        },
      },
    },
    server: {
      proxy: {
        "/v1": "http://127.0.0.1:8000",
        "/health": "http://127.0.0.1:8000",
      },
    },
  };
});
