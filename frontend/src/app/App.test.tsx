import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  NotFoundState,
  RouteErrorBoundary,
  RouteLoadingState,
} from "./App";

function ThrowingRoute({ broken }: { broken: boolean }) {
  if (broken) throw new Error("route failed");
  return <p>route recovered</p>;
}

afterEach(cleanup);

describe("route reliability states", () => {
  it("announces loading state without exposing an empty page", () => {
    render(<RouteLoadingState />);

    expect(screen.getByRole("status")).toHaveAttribute("aria-busy", "true");
    expect(screen.getByText("正在加载工作区…")).toBeVisible();
  });

  it("catches a route render failure and can retry the route", async () => {
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => undefined);
    const user = userEvent.setup();
    let broken = true;
    function ControlledRoute() {
      return <ThrowingRoute broken={broken} />;
    }
    render(
      <RouteErrorBoundary resetKey="route-a">
        <ControlledRoute />
      </RouteErrorBoundary>,
    );

    expect(screen.getByRole("alert")).toHaveTextContent("这个页面暂时无法显示");
    broken = false;
    await user.click(screen.getByRole("button", { name: "重试当前页面" }));
    expect(screen.getByText("route recovered")).toBeVisible();

    consoleError.mockRestore();
  });

  it("renders an explicit not-found action", () => {
    render(
      <MemoryRouter>
        <NotFoundState />
      </MemoryRouter>,
    );

    expect(screen.getByRole("heading", { name: "没有找到这个页面" })).toBeVisible();
    expect(screen.getByRole("link", { name: "返回项目中心" })).toHaveAttribute(
      "href",
      "/projects",
    );
  });
});
