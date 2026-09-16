import { beforeEach, describe, expect, it } from "vitest";
import { DESKTOP_BACKEND_KEY, parseDesktopDeepLink, resolveDesktopApiUrl, resolveDesktopNavigation } from "./runtime";

describe("desktop runtime contracts", () => {
  beforeEach(() => {
    const values = new Map<string, string>();
    Object.defineProperty(window, "localStorage", {
      configurable: true,
      value: {
        clear: () => values.clear(),
        getItem: (key: string) => values.get(key) ?? null,
        removeItem: (key: string) => values.delete(key),
        setItem: (key: string, value: string) => values.set(key, value),
      },
    });
  });

  it("uses only loopback HTTP or HTTPS API bases", () => {
    window.localStorage.setItem(DESKTOP_BACKEND_KEY, "http://127.0.0.1:8765");
    expect(resolveDesktopApiUrl("/health")).toBe("http://127.0.0.1:8765/health");
    window.localStorage.setItem(DESKTOP_BACKEND_KEY, "http://intranet.test");
    expect(resolveDesktopApiUrl("/health")).toBe("/health");
    window.localStorage.setItem(DESKTOP_BACKEND_KEY, "https://rag.example.test/base");
    expect(resolveDesktopApiUrl("/v1/projects")).toBe("https://rag.example.test/base/v1/projects");
  });

  it("maps native menu commands into the current governed project", () => {
    expect(resolveDesktopNavigation("wiki", "/p/project-rag/map")).toBe("/p/project-rag/wiki");
    expect(resolveDesktopNavigation("agents", "/projects", "project-rag")).toBe("/p/project-rag/agents");
    expect(resolveDesktopNavigation("sessions", "/projects", null)).toBe("/projects");
  });

  it("accepts bounded app deep links and rejects external or ambiguous routes", () => {
    expect(parseDesktopDeepLink("evidence-rag://project/project-rag/wiki")).toBe("/p/project-rag/wiki");
    expect(parseDesktopDeepLink("evidence-rag://open?route=%2Fsearch%3Fproject%3Dproject-rag")).toBe("/search?project=project-rag");
    expect(parseDesktopDeepLink("https://example.test/project/project-rag/wiki")).toBeNull();
    expect(parseDesktopDeepLink("evidence-rag://open?route=%2F%2Fevil.test")).toBeNull();
  });
});
