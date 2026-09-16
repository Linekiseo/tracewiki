import "@testing-library/jest-dom/vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { RelatedRailTrigger } from "./ResearchAssetsWorkspace";

describe("RelatedRailTrigger", () => {
  it("keeps related sources discoverable and opens them from the collapsed rail", async () => {
    const user = userEvent.setup();
    const onOpen = vi.fn();

    render(<RelatedRailTrigger count={7} onOpen={onOpen} />);

    const trigger = screen.getByRole("button", { name: "展开相关来源，共 7 项" });
    expect(trigger).toHaveAttribute("aria-expanded", "false");
    expect(trigger).toHaveAttribute("aria-controls", "related-sources-panel");
    expect(screen.getByText("相关来源")).toBeVisible();
    expect(screen.getByText("7")).toBeVisible();

    await user.click(trigger);

    expect(onOpen).toHaveBeenCalledTimes(1);
  });
});
