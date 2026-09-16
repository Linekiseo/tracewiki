import {
  type ButtonHTMLAttributes,
  type PropsWithChildren,
  type ReactNode,
  useEffect,
  useId,
  useRef,
  useState,
} from "react";
import { Check, Copy, Maximize2, X } from "lucide-react";

const focusableSelector = [
  "a[href]",
  "button:not([disabled])",
  "input:not([disabled])",
  "select:not([disabled])",
  "textarea:not([disabled])",
  "[contenteditable='true']",
  "[tabindex]:not([tabindex='-1'])",
].join(",");

function focusableElements(container: HTMLElement) {
  return Array.from(
    container.querySelectorAll<HTMLElement>(focusableSelector),
  ).filter((element) => element.getAttribute("aria-hidden") !== "true");
}

export function Button({
  variant = "secondary",
  className = "",
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "secondary" | "quiet" | "danger";
}) {
  return (
    <button className={`button button--${variant} ${className}`} {...props} />
  );
}

export function Status({
  value,
  children,
}: PropsWithChildren<{ value?: string }>) {
  return (
    <span className={`status status--${value || "neutral"}`}>
      <span className="status__dot" />
      {children}
    </span>
  );
}

export function EmptyState({
  icon,
  title,
  description,
  action,
}: {
  icon: ReactNode;
  title: string;
  description: string;
  action?: ReactNode;
}) {
  return (
    <div className="empty-state">
      <div className="empty-state__icon">{icon}</div>
      <h2>{title}</h2>
      <p>{description}</p>
      {action}
    </div>
  );
}

export function Dialog({
  title,
  description,
  open,
  onClose,
  children,
  className = "",
}: PropsWithChildren<{
  title: string;
  description?: string;
  open: boolean;
  onClose: () => void;
  className?: string;
}>) {
  const dialogRef = useRef<HTMLElement>(null);
  const focusRestoreTimerRef = useRef<number | null>(null);
  const lastTriggerRef = useRef<HTMLElement | null>(null);
  const onCloseRef = useRef(onClose);
  const titleId = useId();
  const descriptionId = useId();
  onCloseRef.current = onClose;

  useEffect(() => {
    if (open) return;

    const rememberTrigger = (event: Event) => {
      if (!(event.target instanceof Element)) return;
      const trigger = event.target.closest<HTMLElement>(focusableSelector);
      if (trigger) lastTriggerRef.current = trigger;
    };

    document.addEventListener("pointerdown", rememberTrigger, true);
    document.addEventListener("focusin", rememberTrigger, true);
    return () => {
      document.removeEventListener("pointerdown", rememberTrigger, true);
      document.removeEventListener("focusin", rememberTrigger, true);
    };
  }, [open]);

  useEffect(() => {
    if (!open) return;

    if (focusRestoreTimerRef.current !== null) {
      window.clearTimeout(focusRestoreTimerRef.current);
      focusRestoreTimerRef.current = null;
    }

    const dialog = dialogRef.current;
    if (!dialog) return;

    const activeElement =
      document.activeElement instanceof HTMLElement
        ? document.activeElement
        : null;
    const previouslyFocused =
      activeElement && activeElement !== document.body
        ? activeElement
        : lastTriggerRef.current;

    const isTopmostDialog = () => {
      const dialogs = document.querySelectorAll<HTMLElement>(
        '[role="dialog"][aria-modal="true"]',
      );
      return dialogs[dialogs.length - 1] === dialog;
    };

    const focusFirstElement = () => {
      if (!isTopmostDialog()) return;
      if (dialog.contains(document.activeElement)) return;
      const autoFocusElement = dialog.querySelector<HTMLElement>("[autofocus]");
      const [first] = focusableElements(dialog);
      (autoFocusElement || first || dialog).focus();
    };

    const handleKeyDown = (event: KeyboardEvent) => {
      if (!isTopmostDialog()) return;
      if (event.key === "Escape") {
        event.preventDefault();
        event.stopPropagation();
        onCloseRef.current();
        return;
      }
      if (event.key !== "Tab") return;

      const elements = focusableElements(dialog);
      if (!elements.length) {
        event.preventDefault();
        dialog.focus();
        return;
      }

      const first = elements[0];
      const last = elements[elements.length - 1];
      const active = document.activeElement;
      if (event.shiftKey && (active === first || !dialog.contains(active))) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && active === last) {
        event.preventDefault();
        first.focus();
      }
    };

    const handleFocusIn = (event: FocusEvent) => {
      if (isTopmostDialog() && !dialog.contains(event.target as Node)) {
        focusFirstElement();
      }
    };

    focusFirstElement();
    document.addEventListener("keydown", handleKeyDown, true);
    document.addEventListener("focusin", handleFocusIn, true);

    return () => {
      document.removeEventListener("keydown", handleKeyDown, true);
      document.removeEventListener("focusin", handleFocusIn, true);
      focusRestoreTimerRef.current = window.setTimeout(() => {
        if (previouslyFocused?.isConnected) previouslyFocused.focus();
        focusRestoreTimerRef.current = null;
      });
    };
  }, [open]);

  if (!open) return null;
  return (
    <div className="dialog-layer" role="presentation" onMouseDown={onClose}>
      <section
        ref={dialogRef}
        className={`dialog ${className}`}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={description ? descriptionId : undefined}
        tabIndex={-1}
        onMouseDown={(event) => event.stopPropagation()}
      >
        <header className="dialog__header">
          <div>
            <h2 id={titleId}>{title}</h2>
            {description ? <p id={descriptionId}>{description}</p> : null}
          </div>
          <Button
            variant="quiet"
            className="icon-button"
            onClick={onClose}
            aria-label="关闭"
          >
            <X size={19} />
          </Button>
        </header>
        {children}
      </section>
    </div>
  );
}

export function ExpandableText({
  label,
  title,
  description,
  content,
  emptyText = "暂无可展示内容。",
  previewSize = "comfortable",
  tone = "code",
  className = "",
}: {
  label: string;
  title: string;
  description?: string;
  content?: string | null;
  emptyText?: string;
  previewSize?: "compact" | "comfortable" | "large";
  tone?: "code" | "terminal" | "plain";
  className?: string;
}) {
  const [open, setOpen] = useState(false);
  const [copied, setCopied] = useState(false);
  const text = content?.replace(/\r\n?/g, "\n") || emptyText;
  const lineCount = text.split("\n").length;
  const characterCount = [...text].length;

  const copy = async () => {
    await navigator.clipboard?.writeText(text);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1200);
  };

  return (
    <>
      <section
        className={`expandable-text is-${previewSize} is-${tone} ${className}`}
        aria-label={`${label}预览`}
      >
        <header>
          <span>{label}</span>
          <div>
            <small>{lineCount} 行</small>
            <button onClick={copy} aria-label={`复制：${title}`}>
              {copied ? <Check size={13} /> : <Copy size={13} />}
              {copied ? "已复制" : "复制"}
            </button>
            <button
              className="expandable-text__open"
              onClick={() => setOpen(true)}
              aria-label={`展开详情：${title}`}
            >
              <Maximize2 size={13} />
              展开详情
            </button>
          </div>
        </header>
        <pre>
          <code>{text}</code>
        </pre>
      </section>
      <Dialog
        open={open}
        onClose={() => setOpen(false)}
        title={title}
        description={description || `${label} · 完整内容阅读`}
        className="dialog--reader"
      >
        <div className={`expanded-reader is-${tone}`}>
          <header>
            <span>
              <strong>{lineCount}</strong> 行
              <i />
              <strong>{characterCount.toLocaleString()}</strong> 字符
            </span>
            <button onClick={copy}>
              {copied ? <Check size={14} /> : <Copy size={14} />}
              {copied ? "已复制完整内容" : "复制完整内容"}
            </button>
          </header>
          <pre tabIndex={0}>
            <code>{text}</code>
          </pre>
          <footer>
            <span>可滚动查看全部内容；按 Esc 返回原审阅位置。</span>
            <Button variant="secondary" onClick={() => setOpen(false)}>
              关闭详情
            </Button>
          </footer>
        </div>
      </Dialog>
    </>
  );
}

export function Inspector({
  title,
  eyebrow,
  onClose,
  children,
}: PropsWithChildren<{
  title: string;
  eyebrow?: string;
  onClose: () => void;
}>) {
  return (
    <aside className="inspector" aria-label={`${title}详情`}>
      <header className="inspector__header">
        <div>
          {eyebrow ? <span className="eyebrow">{eyebrow}</span> : null}
          <h2>{title}</h2>
        </div>
        <Button
          variant="quiet"
          className="icon-button"
          onClick={onClose}
          aria-label="关闭详情"
        >
          <X size={19} />
        </Button>
      </header>
      <div className="inspector__body">{children}</div>
    </aside>
  );
}
