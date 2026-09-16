import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  HashRouter,
  Link,
  MemoryRouter,
  Navigate,
  NavLink,
  Outlet,
  Route,
  Routes,
  useLocation,
  useNavigate,
  useParams,
  useSearchParams,
} from "./router";

function RouteProbe() {
  const location = useLocation();
  const params = useParams<{ projectId: string }>();
  const [searchParams, setSearchParams] = useSearchParams();
  const navigate = useNavigate();
  return (
    <section>
      <output data-testid="route-value">
        {`${params.projectId}:${location.pathname}${location.search}`}
      </output>
      <output data-testid="filter-value">{searchParams.get("filter")}</output>
      <button
        onClick={() =>
          setSearchParams((current) => {
            const next = new URLSearchParams(current);
            next.set("filter", "verified");
            return next;
          }, { replace: true })
        }
      >
        update filter
      </button>
      <button onClick={() => navigate(-1)}>back</button>
    </section>
  );
}

function NestedWorkspace() {
  return (
    <>
      <nav>
        <NavLink className={({ isActive }) => isActive ? "active" : ""} to="/p/project-rag/wiki">
          Wiki
        </NavLink>
        <Link to="/p/next-project/wiki?filter=new">Next</Link>
      </nav>
      <Outlet />
    </>
  );
}

function RouterContract() {
  return (
    <Routes>
      <Route element={<NestedWorkspace />}>
        <Route path="/p/:projectId/wiki" element={<RouteProbe />} />
        <Route path="*" element={<p>not found</p>} />
      </Route>
    </Routes>
  );
}

describe("local router contract", () => {
  beforeEach(() => {
    window.history.replaceState(null, "", "/");
  });

  afterEach(cleanup);

  it("matches nested routes, decodes params, and updates search with replace semantics", async () => {
    const user = userEvent.setup();
    render(
      <MemoryRouter
        initialEntries={["/previous", "/p/project%20rag/wiki?filter=raw"]}
        initialIndex={1}
      >
        <RouterContract />
      </MemoryRouter>,
    );

    expect(screen.getByTestId("route-value")).toHaveTextContent(
      "project rag:/p/project%20rag/wiki?filter=raw",
    );
    expect(screen.getByTestId("filter-value")).toHaveTextContent("raw");

    await user.click(screen.getByRole("button", { name: "update filter" }));
    expect(screen.getByTestId("route-value")).toHaveTextContent(
      "?filter=verified",
    );

    await user.click(screen.getByRole("button", { name: "back" }));
    expect(screen.getByText("not found")).toBeVisible();
  });

  it("preserves normal anchor behavior while handling in-app link navigation", async () => {
    const user = userEvent.setup();
    render(
      <MemoryRouter initialEntries={["/p/project-rag/wiki"]}>
        <RouterContract />
      </MemoryRouter>,
    );

    const wikiLink = screen.getByRole("link", { name: "Wiki" });
    expect(wikiLink).toHaveAttribute("href", "/p/project-rag/wiki");
    expect(wikiLink).toHaveAttribute("aria-current", "page");
    expect(wikiLink).toHaveClass("active");

    await user.click(screen.getByRole("link", { name: "Next" }));
    expect(screen.getByTestId("route-value")).toHaveTextContent(
      "next-project:/p/next-project/wiki?filter=new",
    );
  });

  it("uses the URL hash as history and produces hash-safe link hrefs", async () => {
    window.history.replaceState(
      { source: "test" },
      "",
      "/#/p/project-rag/wiki?filter=hash",
    );
    const user = userEvent.setup();
    render(
      <HashRouter>
        <RouterContract />
      </HashRouter>,
    );

    expect(screen.getByTestId("filter-value")).toHaveTextContent("hash");
    expect(screen.getByRole("link", { name: "Next" })).toHaveAttribute(
      "href",
      "#/p/next-project/wiki?filter=new",
    );

    await user.click(screen.getByRole("link", { name: "Next" }));
    expect(window.location.hash).toBe("#/p/next-project/wiki?filter=new");
    expect(screen.getByTestId("route-value")).toHaveTextContent(
      "next-project:/p/next-project/wiki?filter=new",
    );
  });

  it("redirects an index route without adding an extra history entry", async () => {
    render(
      <MemoryRouter initialEntries={["/"]}>
        <Routes>
          <Route index element={<Navigate to="/projects" replace />} />
          <Route path="/projects" element={<p>project center</p>} />
        </Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByText("project center")).toBeVisible();
  });

  it("rejects protocol and scheme-relative navigation targets", () => {
    const consoleError = vi
      .spyOn(console, "error")
      .mockImplementation(() => undefined);

    expect(() =>
      render(
        <MemoryRouter>
          <Link to="https://example.test/collect">external</Link>
        </MemoryRouter>,
      ),
    ).toThrow("Only absolute in-app routes are supported");
    expect(() =>
      render(
        <MemoryRouter>
          <Link to="//example.test/collect">scheme relative</Link>
        </MemoryRouter>,
      ),
    ).toThrow("Only absolute in-app routes are supported");

    consoleError.mockRestore();
  });
});
