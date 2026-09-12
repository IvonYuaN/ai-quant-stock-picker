// AQSP UI 原语层（aq-* 前缀）。
//
// 改造前的问题：样式表里同时存在两套组件类（历史 vr-* 与后来的 aqsp-*），
// 同一个"徽标"有 aqsp-badge / vr-chip / vr-status 三种写法，彼此定义还有重复；
// 页面于是各写各的空态、加载态、状态条，同一件事在不同页长得不一样。
//
// 这里收敛成一套最小原语：所有页面只用这几个组件表达"卡片 / 标签 / 状态 / 空态 / 分区标题"，
// 语气（ok / warn / neutral）只有一处映射，展示因此天然一致。
//
// 注意：语气类名一律写成**显式字面量映射**，不要用 `aq-badge-${tone}` 这种模板拼接 ——
// Tailwind 是按源码文本匹配类名的，拼接出来的类名扫不到，会被当成未使用而裁掉，
// 结果是"组件写了 tone、样式却没生效"。
import type { KeyboardEvent as ReactKeyboardEvent, ReactNode } from "react";
import { AlertCircle, CheckCircle2, CircleAlert, Info, RefreshCw, type LucideIcon } from "lucide-react";
import { cn } from "@/lib/utils";
import type { Tone } from "@/lib/daily-view";

const TONE_ICON: Record<Tone, LucideIcon> = {
  ok: CheckCircle2,
  warn: CircleAlert,
  neutral: Info,
};

const CALLOUT_TONE: Readonly<Record<Tone, string>> = {
  ok: "aq-callout-ok",
  warn: "aq-callout-warn",
  neutral: "aq-callout-neutral",
};

const BADGE_TONE: Readonly<Record<Tone, string>> = {
  ok: "aq-badge-ok",
  warn: "aq-badge-warn",
  neutral: "aq-badge-neutral",
};

const TAG_TONE: Readonly<Record<Tone | "primary", string>> = {
  primary: "aq-tag-primary",
  ok: "aq-tag-ok",
  warn: "aq-tag-warn",
  neutral: "aq-tag-neutral",
};

const CARD_ACCENT: Readonly<Record<"primary" | "success" | "warning" | "info", string>> = {
  primary: "aq-card-primary",
  success: "aq-card-success",
  warning: "aq-card-warning",
  info: "aq-card-info",
};

const STATE_TONE: Readonly<Record<Tone, string>> = {
  ok: "aq-state-ok",
  warn: "aq-state-warn",
  neutral: "aq-state-neutral",
};

/* ---------------------------------------------------------------- 语气提示条 */

export interface ToneCalloutProps {
  tone: Tone;
  title?: string;
  detail?: ReactNode;
  icon?: LucideIcon;
  action?: ReactNode;
  className?: string;
}

/** 唯一的「状态条」表达：门禁、链路、数据新鲜度、空态提示都走它。 */
export function ToneCallout({ tone, title, detail, icon, action, className }: ToneCalloutProps) {
  const Icon = icon ?? TONE_ICON[tone];
  return (
    <div className={cn("aq-callout", CALLOUT_TONE[tone], className)} role="status">
      <Icon className="aq-callout-icon" aria-hidden="true" />
      <div className="aq-callout-body">
        {title ? <strong>{title}</strong> : null}
        {detail ? <div className="aq-callout-detail">{detail}</div> : null}
      </div>
      {action ? <div className="aq-callout-action">{action}</div> : null}
    </div>
  );
}

/* ------------------------------------------------------------------ 标签 */

export interface BadgeProps {
  tone?: Tone;
  children: ReactNode;
  className?: string;
}

/** 药丸标签：数据状态、生命周期、证据等级。 */
export function Badge({ tone = "neutral", children, className }: BadgeProps) {
  return <span className={cn("aq-badge", BADGE_TONE[tone], className)}>{children}</span>;
}

export interface TagProps {
  tone?: Tone | "primary";
  children: ReactNode;
  className?: string;
}

/** 轻量标签：策略名、板块名、角色名。 */
export function Tag({ tone = "neutral", children, className }: TagProps) {
  return <span className={cn("aq-tag", TAG_TONE[tone], className)}>{children}</span>;
}

/* -------------------------------------------------------------- 分区标题 */

export interface SectionHeaderProps {
  number?: string;
  title: string;
  description?: string;
  /** 右侧的数量/汇总信息。别往这里塞图标 —— 图标用 `icon`。 */
  count?: ReactNode;
  icon?: LucideIcon;
  children?: ReactNode;
}

export function SectionHeader({ number, title, description, count, icon, children }: SectionHeaderProps) {
  const Icon = icon;
  return (
    <header className="aq-section-head">
      <div className="aq-section-head-main">
        {number ? <p className="aq-eyebrow">{number}</p> : null}
        <h2>
          {Icon ? <Icon className="aq-section-title-icon" aria-hidden="true" /> : null}
          {title}
        </h2>
        {description ? <p className="aq-section-desc">{description}</p> : null}
      </div>
      <div className="aq-section-head-side">
        {count != null ? <span className="aq-section-count">{count}</span> : null}
        {children}
      </div>
    </header>
  );
}

/* ---------------------------------------------------------------- 空态 */

export interface EmptyStateProps {
  title: string;
  detail?: ReactNode;
  icon?: LucideIcon;
  action?: ReactNode;
}

/** 空态统一说明"为什么空"，而不是留一片空白让人猜。 */
export function EmptyState({ title, detail, icon: Icon = CircleAlert, action }: EmptyStateProps) {
  return (
    <div className="aq-empty">
      <Icon className="aq-empty-icon" aria-hidden="true" />
      <div className="aq-empty-body">
        <strong>{title}</strong>
        {detail ? <p>{detail}</p> : null}
        {action ? <div className="aq-empty-action">{action}</div> : null}
      </div>
    </div>
  );
}

/* ------------------------------------------------------- 加载 / 错误 / 信息 */

export function LoadingState({ label = "正在读取研究数据" }: { label?: string }) {
  return (
    <div className="aq-state aq-state-neutral">
      <RefreshCw className="aq-spin" aria-hidden="true" />
      <span className="aq-state-text">{label}</span>
    </div>
  );
}

export function ErrorState({ error, onRefresh }: { error: string; onRefresh: () => void }) {
  return (
    <div className="aq-state aq-state-warn">
      <AlertCircle aria-hidden="true" />
      <span className="aq-state-text">读取失败：{error}</span>
      <button type="button" className="aq-btn aq-btn-ghost" onClick={onRefresh} title="重新读取">
        <RefreshCw aria-hidden="true" />
        重试
      </button>
    </div>
  );
}

/** 纯信息面板：用于"正在读取""暂无数据"这类不需要语气的说明。 */
export function StatePanel({
  tone = "neutral",
  children,
  action,
}: {
  tone?: Tone;
  children: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className={cn("aq-state", STATE_TONE[tone])}>
      <span className="aq-state-text">{children}</span>
      {action}
    </div>
  );
}

/* ---------------------------------------------------------------- 容器 */

/**
 * 可点击表格行的通用属性：鼠标与键盘都能触发。
 *
 * 只写 `onClick` 是常见的可访问性缺口 —— `<tr>` 天然不可聚焦、也不响应回车，
 * 结果键盘用户**完全打不开**（抽屉/详情这类核心交互尤其致命）。
 */
export function clickableRowProps(onActivate: () => void, title?: string) {
  return {
    className: "aq-clickable",
    tabIndex: 0,
    role: "button" as const,
    title,
    onClick: onActivate,
    onKeyDown: (event: ReactKeyboardEvent<HTMLElement>) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        onActivate();
      }
    },
  };
}

export function Card({
  accent,
  className,
  children,
}: {
  accent?: "primary" | "success" | "warning" | "info";
  className?: string;
  children: ReactNode;
}) {
  return <article className={cn("aq-card", accent && CARD_ACCENT[accent], className)}>{children}</article>;
}
