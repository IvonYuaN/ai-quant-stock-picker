// 收评日报 6 段卡片流（纯白极简）——决策时刻的市场环境证据面。
//
// 数据：`GET /api/aqsp/closing-review`（市场级只读聚合，复用 closing_review 的
// 6 个 build_*_section，fail-soft）。派生纯函数在 lib/closing-view，本组件只渲染。
//
// 视觉基调（按老大审美）：白底卡片纵向堆叠、标题层级清晰、留白充足、每段可折叠；
// 缺数据段置灰默认折叠，绝不用空段冒充有数据。所有段只读，绝不写回打分/排序/下单。
import { useCallback, useEffect, useMemo, useState } from "react";
import { Activity, ChevronDown, Grip, Landmark, Megaphone, Newspaper, Trophy } from "lucide-react";
import { Badge } from "@/components/ui/primitives";
import { MarkdownView } from "@/components/ui/Markdown";
import { cn } from "@/lib/utils";
import { api, type ClosingReviewPayload } from "@/lib/api";
import { buildClosingSections, type ClosingSectionView } from "@/lib/closing-view";

const SECTION_ICON: Record<string, typeof Activity> = {
  factor_ic: Activity,
  board_fund: Landmark,
  longhubang: Trophy,
  news: Newspaper,
  announcements: Megaphone,
  holder_concentration: Grip,
};

/** 段序（与后端返回顺序一致）：决策相关的放前，市场环境证据放后。 */
const ORDER: readonly string[] = [
  "factor_ic",
  "board_fund",
  "longhubang",
  "news",
  "announcements",
  "holder_concentration",
];

function SectionCard({
  section,
  index,
  collapsed,
  onToggle,
}: {
  section: ClosingSectionView;
  index: number;
  collapsed: boolean;
  onToggle: () => void;
}) {
  const Icon = SECTION_ICON[section.key] ?? Activity;
  const empty = !section.available;
  return (
    <article className={cn("aq-closing-card", empty && "aq-closing-card-empty")}>
      <button
        type="button"
        className="aq-closing-head"
        onClick={onToggle}
        aria-expanded={!collapsed}
      >
        <span className="aq-closing-eyebrow">
          <Icon className="aq-closing-icon" aria-hidden="true" />
          0{index + 1}
        </span>
        <span className="aq-closing-title">{section.title}</span>
        {empty ? (
          <Badge tone="neutral">暂无数据</Badge>
        ) : (
          <Badge tone="ok">有数据</Badge>
        )}
        <ChevronDown
          className={cn("aq-closing-chevron", !collapsed && "aq-closing-chevron-open")}
          aria-hidden="true"
        />
      </button>
      {!collapsed && section.body ? (
        <div className="aq-closing-body">
          <MarkdownView content={section.body} />
        </div>
      ) : null}
      {!collapsed && empty ? (
        <p className="aq-closing-empty-hint">该段当前无可用数据（盘后预载未落盘或数据源缺缓存）。</p>
      ) : null}
    </article>
  );
}

/** 收评日报 6 段聚合卡片流。挂在「今日研究」常驻块内，不随页签变化。 */
export function ClosingReviewPanel() {
  const [payload, setPayload] = useState<ClosingReviewPayload | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  // 默认折叠的段：缺数据段置灰折叠；有数据段展开。
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());

  const load = useCallback(() => {
    let live = true;
    setLoading(true);
    api
      .closingReview()
      .then((data) => {
        if (!live) return;
        setPayload(data);
        setError("");
        // 缺数据段进默认折叠集合
        setCollapsed(new Set(buildClosingSections(data).filter((s) => !s.available).map((s) => s.key)));
      })
      .catch((reason: unknown) => {
        if (!live) return;
        setError(reason instanceof Error ? reason.message : String(reason));
        setPayload(null);
      })
      .finally(() => {
        if (live) setLoading(false);
      });
    return () => {
      live = false;
    };
  }, []);

  useEffect(() => {
    const cancel = load();
    return cancel;
  }, [load]);

  const sections = useMemo(() => buildClosingSections(payload), [payload]);
  const ordered = useMemo(
    () => ORDER.map((key) => sections.find((s) => s.key === key)).filter(
      (s): s is ClosingSectionView => Boolean(s),
    ),
    [sections],
  );
  const anyAvailable = ordered.some((s) => s.available);

  return (
    <section className="aq-closing" aria-label="收评日报" data-closing-review>
      <header className="aq-closing-headbar">
        <h3>收评日报 · 市场环境</h3>
        <span className="aq-closing-sub">
          {payload?.as_of ? `数据截至 ${payload.as_of.slice(0, 10)}` : "只读 · 不改打分/排序/下单"}
        </span>
      </header>

      {loading ? (
        <p className="aq-closing-loading">正在读取收评 6 段…</p>
      ) : error ? (
        <div className="aq-closing-error">
          <p>{error}</p>
          <button type="button" className="aq-btn" onClick={load}>
            重试
          </button>
        </div>
      ) : anyAvailable ? (
        <div className="aq-closing-flow">
          {ordered.map((section, index) => {
            const isCollapsed = collapsed.has(section.key);
            return (
              <SectionCard
                key={section.key}
                section={section}
                index={index}
                collapsed={isCollapsed}
                onToggle={() =>
                  setCollapsed((prev) => {
                    const next = new Set(prev);
                    if (next.has(section.key)) next.delete(section.key);
                    else next.add(section.key);
                    return next;
                  })
                }
              />
            );
          })}
        </div>
      ) : (
        <p className="aq-closing-empty-hint">今日收评 6 段暂无可用数据（盘后预载未落盘或数据源缺缓存）。</p>
      )}
    </section>
  );
}
