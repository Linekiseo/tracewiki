import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Dialog, ExpandableText } from "./ui";

afterEach(cleanup);

function DialogHarness({
  onClose = () => undefined,
}: {
  onClose?: () => void;
}) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button type="button" onClick={() => setOpen(true)}>
        打开对话框
      </button>
      <button type="button">外部操作</button>
      <Dialog
        open={open}
        title="编辑项目"
        description="修改项目设置"
        onClose={() => {
          onClose();
          setOpen(false);
        }}
      >
        <label>
          项目名称
          <input />
        </label>
        <button type="button">保存</button>
      </Dialog>
    </>
  );
}

describe("Dialog", () => {
  it("labels the dialog and moves focus inside when opened", async () => {
    const user = userEvent.setup();
    render(<DialogHarness />);

    await user.click(screen.getByRole("button", { name: "打开对话框" }));

    const dialog = screen.getByRole("dialog", { name: "编辑项目" });
    expect(dialog).toHaveAttribute("aria-modal", "true");
    expect(dialog).toHaveAccessibleDescription("修改项目设置");
    expect(screen.getByRole("button", { name: "关闭" })).toHaveFocus();
  });

  it("preserves an explicitly requested initial focus target", () => {
    render(
      <Dialog open title="新建项目" onClose={() => undefined}>
        <label>
          项目名称
          <input autoFocus />
        </label>
      </Dialog>,
    );

    expect(screen.getByRole("textbox", { name: "项目名称" })).toHaveFocus();
  });

  it("traps tab focus in both directions", async () => {
    const user = userEvent.setup();
    render(<DialogHarness />);
    await user.click(screen.getByRole("button", { name: "打开对话框" }));

    const close = screen.getByRole("button", { name: "关闭" });
    const save = screen.getByRole("button", { name: "保存" });
    save.focus();
    await user.tab();
    expect(close).toHaveFocus();

    await user.tab({ shift: true });
    expect(save).toHaveFocus();
  });

  it("closes with Escape and restores focus to the opener", async () => {
    const onClose = vi.fn();
    const user = userEvent.setup();
    render(<DialogHarness onClose={onClose} />);

    const opener = screen.getByRole("button", { name: "打开对话框" });
    await user.click(opener);
    await user.keyboard("{Escape}");

    expect(onClose).toHaveBeenCalledOnce();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await waitFor(() => expect(opener).toHaveFocus());
  });

  it("remembers a pointer opener even when the browser does not focus it", async () => {
    const user = userEvent.setup();
    render(<DialogHarness />);

    const opener = screen.getByRole("button", { name: "打开对话框" });
    fireEvent.pointerDown(opener);
    fireEvent.click(opener);
    expect(screen.getByRole("dialog")).toBeVisible();

    await user.keyboard("{Escape}");
    await waitFor(() => expect(opener).toHaveFocus());
  });

  it("keeps independently rendered dialogs associated with unique labels", () => {
    render(
      <>
        <Dialog open title="第一个" onClose={() => undefined}>
          one
        </Dialog>
        <Dialog open title="第二个" onClose={() => undefined}>
          two
        </Dialog>
      </>,
    );

    const [first, second] = screen.getAllByRole("dialog");
    expect(first.getAttribute("aria-labelledby")).not.toBe(
      second.getAttribute("aria-labelledby"),
    );
  });
});

describe("ExpandableText", () => {
  it("opens the complete content in a readable dialog and returns focus", async () => {
    const user = userEvent.setup();
    const content = [
      "第一行：摘要",
      "第二行：完整证据不会被截断",
      "第三行：可在弹窗中滚动审阅",
    ].join("\n");

    render(
      <ExpandableText
        label="命中内容"
        title="来源文件 · 完整命中内容"
        description="代码实现 · src/example.ts"
        content={content}
      />,
    );

    expect(screen.getByText("3 行")).toBeInTheDocument();
    const opener = screen.getByRole("button", {
      name: "展开详情：来源文件 · 完整命中内容",
    });
    await user.click(opener);

    const dialog = screen.getByRole("dialog", {
      name: "来源文件 · 完整命中内容",
    });
    expect(dialog).toHaveAccessibleDescription("代码实现 · src/example.ts");
    expect(dialog).toHaveTextContent("第三行：可在弹窗中滚动审阅");
    expect(
      screen.getByRole("button", { name: "复制完整内容" }),
    ).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "关闭详情" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await waitFor(() => expect(opener).toHaveFocus());
  });
});
