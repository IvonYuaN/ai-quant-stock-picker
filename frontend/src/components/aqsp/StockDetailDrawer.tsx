// 个股简介抽屉。
//
// ⚠️ 刻意做得轻：只回答本项目该回答的问题 —— **为什么选它**（评分 / 依据 / 策略 / 证据），
// 加上一行行情快照，深度数据（K线、财务、资金流、研报、公告）交给东方财富、同花顺、巨潮。
//
// 曾经这里聚合了 19 个后端接口，把行情终端重做了一遍 —— 那是舍本逐末：
// 本项目的价值在选股与复盘，专业站点在个股数据上做得远比我们好。
import { useCallback, useEffect, useRef, useState } from "react";
import { ExternalLink, X } from "lucide-react";
import { api, type Quote } from "@/lib/api";
import { Badge, LoadingState, StatePanel, Tag } from "@/components/ui/primitives";
import { changeClass, formatSignedPct } from "@/lib/format";
import { externalLinks } from "@/lib/external-links";
import { cn } from "@/lib/utils";

/** AQSP 自己的判断摘要（外部站点没有的信息）。 */
export interface StockIntro {
  scoreText: string;
  status: string;
  evidence: string;
  ready: boolean;
  strategies: readonly string[];
  reasons: readonly string[];
  nextStep: string;
  context: string;
}

const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

export function StockDetailDrawer({
  symbol,
  onClose,
  intro,
}: {
  symbol: string | null;
  onClose: () => void;
  intro?: StockIntro;
}) {
  const [quote, setQuote] = useState<Quote | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const panelRef = useRef<HTMLElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!symbol) {
      setQuote(null);
      setError("");
      return;
    }
    let alive = true;
    setLoading(true);
    api
      .quote(symbol)
      .then((data) => {
        if (!alive) return;
        setQuote(data[symbol] ?? null);
        setError("");
      })
      .catch((reason: unknown) => {
        if (alive) setError(reason instanceof Error ? reason.message : "行情读取失败");
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [symbol]);

  useEffect(() => {
    if (!symbol) return;
    const opener = document.activeElement as HTMLElement | null;
    const panel = panelRef.current;
    closeRef.current?.focus();

    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        onClose();
        return;
      }
      if (event.key !== "Tab" || !panel) return;
      const items = [...panel.querySelectorAll<HTMLElement>(FOCUSABLE)].filter(
        (el) => el.offsetParent !== null || el === document.activeElement,
      );
      if (items.length === 0) return;
      const first = items[0];
      const last = items[items.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };

    window.addEventListener("keydown", onKey);
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      window.removeEventListener("keydown", onKey);
      document.body.style.overflow = previousOverflow;
      opener?.focus?.();
    };
  }, [symbol, onClose]);

  const close = useCallback(() => onClose(), [onClose]);
  if (!symbol) return null;

  const links = externalLinks(symbol);

  return (
    <div className="aq-drawer-overlay" role="presentation" onClick={close}>
      <aside
        ref={panelRef}
        className="aq-drawer-panel"
        role="dialog"
        aria-modal="true"
        tabIndex={-1}
        aria-label={`${quote?.name ?? symbol} 简介`}
        onClick={(event) => event.stopPropagation()}
      >
        <header className="aq-drawer-panel-head">
          <div>
            <p className="aq-eyebrow">个股简介 · 选股视角</p>
            <div className="aq-title-row">
              <h2>{quote?.name ?? "读取中…"}</h2>
              <strong>{symbol}</strong>
            </div>
            {quote ? (
              <p className="aq-detail-muted">
                现价 <b className="aq-num">{quote.price.toFixed(2)}</b>{" "}
                <b className={cn("aq-num", changeClass(quote.change_pct))}>{formatSignedPct(quote.change_pct)}</b>
                {quote.pe_ttm ? ` · PE ${quote.pe_ttm.toFixed(1)}` : ""}
                {quote.pb ? ` · PB ${quote.pb.toFixed(2)}` : ""}
                {quote.mcap_yi ? ` · 市值 ${quote.mcap_yi.toFixed(0)} 亿` : ""}
                {` · 换手 ${quote.turnover_pct.toFixed(2)}%`}
              </p>
            ) : null}
          </div>
          <button
            ref={closeRef}
            type="button"
            className="aq-btn aq-btn-icon"
            onClick={close}
            title="关闭（Esc）"
            aria-label="关闭个股简介"
          >
            <X aria-hidden="true" />
          </button>
        </header>

        <div className="aq-drawer-panel-body">
          {loading ? <LoadingState label="正在读取行情…" /> : null}
          {error ? <StatePanel tone="warn">{error}，不影响下方链接。</StatePanel> : null}

          <section className="aq-detail-block">
            <p className="aq-label">AQSP 为什么把它选出来</p>
            {intro ? (
              <>
                <div className="aq-intro-head">
                  <div className="aq-score">
                    <b>{intro.scoreText}</b>
                    <span>评分</span>
                  </div>
                  <div className="aq-tag-row">
                    <Tag tone="primary">{intro.status}</Tag>
                    <Tag>{intro.evidence}</Tag>
                    {intro.ready ? <Tag tone="ok">可复核</Tag> : <Tag tone="warn">仅观察</Tag>}
                  </div>
                </div>
                {intro.context ? <p className="aq-detail-muted">{intro.context}</p> : null}
                {intro.strategies.length > 0 ? (
                  <div className="aq-tag-row">
                    {intro.strategies.map((item) => (
                      <Tag key={item}>{item}</Tag>
                    ))}
                  </div>
                ) : null}
                {intro.reasons.length > 0 ? (
                  <ul className="aq-reason-list">
                    {intro.reasons.slice(0, 4).map((item) => (
                      <li key={item}>{item}</li>
                    ))}
                  </ul>
                ) : null}
                {intro.nextStep ? <p className="aq-next-step">下一观察：{intro.nextStep}</p> : null}
              </>
            ) : (
              <p className="aq-detail-muted">
                这只票不在当天候选里，因此没有评分与证据链。下方链接可查看它的行情、财务与公告。
              </p>
            )}
          </section>

          <section className="aq-detail-block">
            <p className="aq-label">深入数据 · 交给专业站点</p>
            {links.length === 0 ? (
              <p className="aq-detail-muted">{symbol} 不是有效的 6 位 A 股代码，无法生成外部链接。</p>
            ) : (
              <ul className="aq-detail-list">
                {links.map((link) => (
                  <li key={link.key}>
                    <a className="aq-radar-title" href={link.url} target="_blank" rel="noreferrer">
                      {link.label}
                      <ExternalLink aria-hidden="true" />
                    </a>
                    <Badge tone="neutral">{link.site}</Badge>
                  </li>
                ))}
              </ul>
            )}
            <p className="aq-detail-muted">
              深度数据（K线 / 财务 / 资金流 / 研报 / 公告 / 互动易）在以上站点更完整，本项目不做重复呈现。
            </p>
          </section>
        </div>
      </aside>
    </div>
  );
}
