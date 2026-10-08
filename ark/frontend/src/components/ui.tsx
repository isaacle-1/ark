/* ui.tsx — ARK field-manual component set (Panel, Button, Table chrome,
   Badge, StatusLine, Field, Stat, IndexRow). Classes defined in styles.css. */

import type { AnchorHTMLAttributes, ButtonHTMLAttributes, ReactNode } from "react";

/* -- Panel ------------------------------------------------------------------ */
export function Panel({
  no,
  title,
  meta,
  actions,
  flush,
  className = "",
  testid,
  children,
}: {
  no?: string;
  title?: string;
  meta?: ReactNode;
  actions?: ReactNode;
  flush?: boolean;
  className?: string;
  testid?: string;
  children: ReactNode;
}) {
  return (
    <section className={"panel " + className} data-testid={testid}>
      {(no || title || meta || actions) && (
        <header className="panel-head">
          <div className="flex min-w-0 items-baseline gap-2">
            {no && <span className="label">{no}</span>}
            {title && <span className="panel-head-title">{title}</span>}
          </div>
          {(meta || actions) && (
            <div className="flex items-center gap-3">
              {meta && <span className="panel-head-meta">{meta}</span>}
              {actions}
            </div>
          )}
        </header>
      )}
      <div className={flush ? "panel-body-flush" : "panel-body"}>{children}</div>
    </section>
  );
}

/* -- Page title ------------------------------------------------------------- */
export function PageTitle({
  kicker,
  title,
  meta,
}: {
  kicker?: ReactNode;
  title: ReactNode;
  meta?: ReactNode;
}) {
  return (
    <header className="mb-4 flex flex-wrap items-end justify-between gap-x-4 gap-y-2">
      <div>
        {kicker && <div className="label mb-1">{kicker}</div>}
        <h1 className="display-lg">{title}</h1>
      </div>
      {meta && <div className="mb-1">{meta}</div>}
    </header>
  );
}

/* -- Button ----------------------------------------------------------------- */
type BtnProps = {
  variant?: "ghost" | "primary" | "danger" | "plain";
  size?: "md" | "sm";
  testid?: string;
  children: ReactNode;
} & (
  | ({ href: string } & AnchorHTMLAttributes<HTMLAnchorElement>)
  | ({ href?: undefined } & ButtonHTMLAttributes<HTMLButtonElement>)
);

export function Button({
  variant = "ghost",
  size = "md",
  testid,
  className = "",
  children,
  ...rest
}: BtnProps) {
  const cls =
    "btn " +
    (variant === "primary" ? "btn-primary" : variant === "danger" ? "btn-danger" : variant === "plain" ? "btn-plain" : "") +
    " " +
    (size === "sm" ? "btn-sm " : "") +
    className;
  const tid = testid ? { "data-testid": testid } : {};
  if (rest.href !== undefined) {
    const { href, ...anchor } = rest as { href: string } & AnchorHTMLAttributes<HTMLAnchorElement>;
    return (
      <a href={href} className={cls} {...tid} {...anchor}>
        {children}
      </a>
    );
  }
  return (
    <button className={cls} {...tid} {...rest}>
      {children}
    </button>
  );
}

/* -- Badge ------------------------------------------------------------------ */
export function Badge({
  tone = "muted",
  className = "",
  testid,
  children,
}: {
  tone?: "muted" | "ok" | "warn" | "err" | "active" | "solid";
  className?: string;
  testid?: string;
  children: ReactNode;
}) {
  const toneClass =
    tone === "ok"
      ? "badge-ok"
      : tone === "warn"
        ? "badge-warn"
        : tone === "err"
          ? "badge-err"
          : tone === "active"
            ? "badge-active"
            : tone === "solid"
              ? "badge-solid"
              : "";
  return (
    <span className={"badge " + toneClass + " " + className} data-testid={testid}>
      {children}
    </span>
  );
}

/* -- StatusLine ------------------------------------------------------------- */
export function StatusLine({
  tone = "muted",
  className = "",
  children,
}: {
  tone?: "muted" | "ok" | "warn" | "err" | "active";
  className?: string;
  children: ReactNode;
}) {
  return (
    <span className={"statusline " + (tone === "muted" ? "" : tone) + " " + className}>
      <span className="dot" aria-hidden="true" />
      {children}
    </span>
  );
}

/* -- Field ------------------------------------------------------------------ */
export function Field({
  label,
  hint,
  className = "",
  children,
}: {
  label?: ReactNode;
  hint?: ReactNode;
  className?: string;
  children: ReactNode;
}) {
  return (
    <div className={"field " + className}>
      {label && <label>{label}</label>}
      {children}
      {hint && <div className="hint fine">{hint}</div>}
    </div>
  );
}

/* -- Stat cell (dashboard status board) ------------------------------------- */
export function Stat({
  label,
  value,
  foot,
}: {
  label: ReactNode;
  value: ReactNode;
  foot?: ReactNode;
}) {
  return (
    <div className="stat">
      <div className="stat-label">{label}</div>
      <div className="stat-value">{value}</div>
      {foot && <div className="stat-foot">{foot}</div>}
    </div>
  );
}

/* -- Index row (numbered module/catalog index) ------------------------------ */
export function IndexRow({
  no,
  title,
  desc,
  meta,
  action,
  onClick,
  onKeyDown,
  href,
}: {
  no?: string;
  title: ReactNode;
  desc?: ReactNode;
  meta?: ReactNode;
  action?: ReactNode;
  onClick?: () => void;
  onKeyDown?: (e: KeyboardEvent) => void;
  href?: string;
}) {
  const cls = "index-row" + (onClick || href ? " cursor-pointer" : "");
  const body = (
    <>
      {no && <span className="index-no">{no}</span>}
      <span className="index-main">
        <span className="index-title">{title}</span>
        {desc && <span className="index-desc"> — {desc}</span>}
      </span>
      {meta && <span className="index-meta">{meta}</span>}
      {action}
    </>
  );
  if (href) {
    return (
      <a className={cls + " no-underline"} href={href}>
        {body}
      </a>
    );
  }
  return (
    <div
      className={cls}
      onClick={onClick}
      onKeyDown={(e) => onKeyDown?.(e as unknown as KeyboardEvent)}
      role={onClick ? "button" : undefined}
      tabIndex={onClick ? 0 : undefined}
    >
      {body}
    </div>
  );
}

/* -- Num / Mono ------------------------------------------------------------- */
export function Num({ className = "", children }: { className?: string; children: ReactNode }) {
  return <span className={"num " + className}>{children}</span>;
}