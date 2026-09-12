// 策略实验室区块：变体实验的账户、生命周期与持仓。
//
// 这里刻意与「今日研究」分开：变体是历史 raw 回测，不参与当天结论。
// 版面用虚线边框 + 明确徽标表达"独立区域"，避免被误读成今日推荐。
import { FlaskConical } from "lucide-react";
import { Badge, Card, EmptyState, SectionHeader } from "@/components/ui/primitives";
import { cn } from "@/lib/utils";
import { changeClass } from "@/lib/format";
import type { DailyView } from "@/lib/daily-view";
import {
  variantDisplayName,
  variantHoldingsLabel,
  variantMoney,
  variantPercent,
  variantStrategyLogic,
  variantStrategyParameters,
} from "@/lib/variant-view";
import type { AqspVariant } from "@/types/aqsp";

function VariantCard({ variant }: { variant: AqspVariant }) {
  const pnl = variant.total_pnl;
  const holdings = variant.holdings;
  return (
    <Card>
      <div className="aq-card-head">
        <div className="aq-card-title">
          <h3>{variantDisplayName(variant)}</h3>
          <span className="aq-code">
            {variant.variant_id}
            {variant.rank ? ` · 回测第 ${variant.rank} 名` : ""}
          </span>
        </div>
        <strong className={cn("aq-variant-pnl", changeClass(pnl))}>{variantMoney(pnl)}</strong>
      </div>

      <div className="aq-variant-strategy">
        <p>
          <b>策略逻辑</b>
          {variantStrategyLogic(variant)}
        </p>
        <p>
          <b>关键参数</b>
          {variantStrategyParameters(variant)}
        </p>
      </div>

      <div className="aq-variant-lifecycle">
        <b>
          {variant.lifecycle_status || "样本积累"} · 第 {variant.generation ?? 1} 代 · 独立入场日{" "}
          {variant.independent_signal_days ?? 0}
        </b>
        <span>{variant.lifecycle_reason || "等待形成可比较样本"}</span>
        {(variant.discussion_links ?? []).slice(0, 3).map((link) => (
          <span key={link.symbol}>
            讨论联动：{link.display_name || link.symbol} ·{" "}
            {link.risk_gate || link.next_trigger || link.discussion_conclusion || "等待复核约束"}
          </span>
        ))}
      </div>

      <div className="aq-variant-account">
        <div>
          <span>初始资金</span>
          <b>{variantMoney(variant.initial_cash)}</b>
        </div>
        <div>
          <span>现金</span>
          <b>{variantMoney(variant.cash)}</b>
        </div>
        <div>
          <span>账户权益</span>
          <b>{variantMoney(variant.final_equity)}</b>
        </div>
        <div>
          <span>总盈亏</span>
          <b className={changeClass(pnl)}>{variantMoney(pnl)}</b>
        </div>
        <div>
          <span>收益率</span>
          <b>{variantPercent(variant.return_pct)}</b>
        </div>
      </div>

      <div className="aq-variant-holdings">
        <b>
          持仓 · {variantHoldingsLabel(holdings)} · 截至 {variant.end_date || "—"}
        </b>
        {holdings?.map((holding) => (
          <div className="aq-holding-row" key={holding.symbol}>
            <strong>
              {holding.name || "名称未记录"}
              <small>{holding.symbol}</small>
            </strong>
            <span>
              数量 {holding.quantity} 股
              <br />
              建仓 {holding.entry_date || "未记录"} · 持有 {holding.holding_days ?? 0} 天
            </span>
            <span>
              成本 {variantMoney(holding.average_price)}
              <br />
              最新 {variantMoney(holding.last_price)}
            </span>
            <span className={changeClass(holding.unrealized_pnl)}>
              市值 {variantMoney(holding.market_value)}
              <br />
              浮盈 {variantMoney(holding.unrealized_pnl)}
            </span>
          </div>
        ))}
      </div>

      <p className="aq-variant-rules">
        成交 {variant.filled_orders} · 拒绝 {variant.rejected_orders} ·{" "}
        {(variant.hard_rules ?? []).join(" · ") || "硬成交规则未记录"}
      </p>
    </Card>
  );
}

export function VariantSection({ view }: { view: DailyView | null }) {
  const variants = view?.variants ?? [];
  const historical = (view?.isHistorical ?? false) || variants.some((v) => v.data_mode.includes("historical"));
  const promoted = variants.filter((v) => v.lifecycle_status === "晋级验证").length;
  const eliminated = variants.filter((v) => v.lifecycle_status === "淘汰").length;
  const range = variants[0];

  return (
    <section className="aq-section aq-section-lab">
      <SectionHeader title="测试与变体" description="独立实验区，不进入正式结论" count={`共 ${variants.length} 套`}>
        <Badge tone={historical ? "warn" : "ok"}>{historical ? "历史回测 · 仅验证" : "当前实验结果"}</Badge>
      </SectionHeader>

      <div className="aq-lab-summary">
        <span>
          <FlaskConical aria-hidden="true" />
          数据区间：{range?.start_date || "—"} 至 {range?.end_date || "—"}
        </span>
        <span>股票覆盖：{view?.variantCoverageText ?? "覆盖率未记录"}</span>
        <span>
          生命周期：晋级 {promoted} · 淘汰 {eliminated} · 共 {variants.length}
        </span>
        <span>每套账户：100,000.00 元</span>
      </div>

      {variants.length === 0 ? (
        <EmptyState
          title="变体结果尚未产出"
          detail="实验结果独立于正式候选，产出后会显示在这里。"
          icon={FlaskConical}
        />
      ) : (
        <div className="aq-variant-grid">
          {variants.map((variant) => (
            <VariantCard key={variant.variant_id} variant={variant} />
          ))}
        </div>
      )}
    </section>
  );
}
