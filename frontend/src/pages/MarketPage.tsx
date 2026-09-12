// 市场环境页：判断"今天能不能动"的外部前提。
//
// 数据全部来自后端已有的公开数据接口，只做客观展示（不评分、不预测、不推荐）。
// 每块独立降级：接口失败 → 提示可重试；接口返回空 → 说明数据源未覆盖。两者文案不同。
import { useMemo, useState } from "react";
import { Activity, BarChart3, Flame, Globe2, Layers, RefreshCw, TrendingUp } from "lucide-react";
import {
  Badge,
  EmptyState,
  LoadingState,
  SectionHeader,
  StatePanel,
  Tag,
  ToneCallout,
  clickableRowProps,
} from "@/components/ui/primitives";
import { EChart } from "@/components/ui/EChart";
import { cn } from "@/lib/utils";
import { changeClass, formatRate, formatSignedPct, formatYi } from "@/lib/format";
import { industryOption, ladderOption, sectorFlowOption } from "@/lib/chart-options";
import { useThemeMode } from "@/lib/theme-mode";
import { sectionAvailability, type MarketView } from "@/lib/market-view";
import { useMarketData } from "@/components/aqsp/useMarketData";
import { StockDetailDrawer } from "@/components/aqsp/StockDetailDrawer";

function IndexCards({ view }: { view: MarketView }) {
  return (
    <div className="aq-index-grid">
      {view.indices.map((item) => (
        <div className="aq-index-card" key={item.key}>
          <span className="aq-index-name">{item.name}</span>
          <b className="aq-num">{item.price.toFixed(2)}</b>
          <span className={cn("aq-num aq-index-change", changeClass(item.changePct))}>
            {formatSignedPct(item.changePct)}
            <small>{item.changeAmt >= 0 ? `+${item.changeAmt.toFixed(2)}` : item.changeAmt.toFixed(2)}</small>
          </span>
        </div>
      ))}
    </div>
  );
}

function BreadthBlock({ view }: { view: MarketView }) {
  const b = view.breadth;
  if (!b) return null;
  const ratio = b.upDownRatio;
  return (
    <div className="aq-breadth-block">
      <div className="aq-breadth-metrics">
        <div>
          <span>上涨</span>
          <b className="aq-tone-up aq-num">{b.up}</b>
        </div>
        <div>
          <span>下跌</span>
          <b className="aq-tone-down aq-num">{b.down}</b>
        </div>
        <div>
          <span>平盘</span>
          <b className="aq-num">{b.flat}</b>
        </div>
        <div>
          <span>涨跌比</span>
          <b className="aq-num">{ratio == null ? "—" : ratio.toFixed(2)}</b>
        </div>
        <div>
          <span>涨停</span>
          <b className="aq-tone-up aq-num">{b.ztReal || b.zt}</b>
        </div>
        <div>
          <span>跌停</span>
          <b className="aq-tone-down aq-num">{b.dtReal || b.dt}</b>
        </div>
        <div>
          <span>宽度</span>
          <b>{b.breadth || "—"}</b>
        </div>
        <div>
          <span>题材投机</span>
          <b>{b.speculation || "—"}</b>
        </div>
      </div>
      {b.active ? <p className="aq-note">活跃度：{b.active}{b.date ? ` · 统计日 ${b.date}` : ""}</p> : null}
    </div>
  );
}

function SectorFlowBlock({ view }: { view: MarketView }) {
  const mode = useThemeMode();
  const { sectorsTop, sectorsBottom } = view;
  if (sectorsTop.length === 0 && sectorsBottom.length === 0) return null;
  const all = [...sectorsBottom].reverse().concat(sectorsTop); // 由低到高，纵向条形更好读
  const option = useMemo(() => sectorFlowOption(all, mode), [all.length, mode]);
  const row = (name: string, net: number, pct: number) => (
    <div className="aq-flow-row" key={name}>
      <span className="aq-flow-name">{name}</span>
      <span className={cn("aq-num", changeClass(pct))}>{formatSignedPct(pct)}</span>
      <span className={cn("aq-num", net >= 0 ? "aq-tone-up" : "aq-tone-down")}>{formatYi(net)}</span>
    </div>
  );
  return (
    <EChart
      option={option}
      height={Math.max(180, all.length * 26)}
      fallback={
        <div className="aq-flow-cols">
          <div>
            <p className="aq-label">资金净流入前列</p>
            {sectorsTop.map((item) => row(item.name, item.net, item.pct))}
          </div>
          <div>
            <p className="aq-label">资金净流出前列</p>
            {sectorsBottom.map((item) => row(item.name, item.net, item.pct))}
          </div>
        </div>
      }
    />
  );
}

function EmotionBlock({ view, onPick }: { view: MarketView; onPick: (symbol: string) => void }) {
  const mode = useThemeMode();
  const e = view.emotion;
  const ladderChart = useMemo(
    () => (e && e.ladder.length > 0 ? ladderOption(e.ladder, mode) : null),
    [e, mode],
  );
  if (!e) return null;
  return (
    <div className="aq-emotion-block">
      <div className="aq-breadth-metrics">
        <div>
          <span>涨停家数</span>
          <b className="aq-tone-up aq-num">{e.ztCount}</b>
        </div>
        <div>
          <span>跌停家数</span>
          <b className="aq-tone-down aq-num">{e.dtCount}</b>
        </div>
        <div>
          <span>炸板家数</span>
          <b className="aq-num">{e.zbCount}</b>
        </div>
        <div>
          <span>最高连板</span>
          <b className="aq-num">{e.maxBoards} 板</b>
        </div>
        <div>
          <span>封板率</span>
          <b className="aq-num">{formatRate(e.sealRate)}</b>
        </div>
        <div>
          <span>炸板率</span>
          <b className="aq-num">{formatRate(e.breakRate)}</b>
        </div>
        <div>
          <span>晋级率</span>
          <b className="aq-num">{formatRate(e.promotionRate)}</b>
        </div>
        <div>
          <span>昨日涨停</span>
          <b className="aq-num">{e.yztCount}</b>
        </div>
      </div>

      {ladderChart ? (
        <div className="aq-ladder">
          <p className="aq-label">连板梯队</p>
          <EChart
            option={ladderChart}
            height={150}
            fallback={
              <div className="aq-ladder-rows">
                {e.ladder.map((tier) => (
                  <span className="aq-ladder-chip" key={tier.boards}>
                    {tier.plus ? `${tier.boards} 板+` : `${tier.boards} 板`}
                    <b>{tier.count}</b>
                  </span>
                ))}
              </div>
            }
          />
        </div>
      ) : null}

      {e.lianbanStocks.length > 0 ? (
        <div className="aq-ladder">
          <p className="aq-label">连板股清单 · {e.lianbanStocks.length} 只 · 客观榜单，非推荐</p>
          <div className="aq-table-wrap">
            <table className="aq-table">
              <thead>
                <tr>
                  <th>代码 / 名称</th>
                  <th className="aq-num">连板</th>
                  <th className="aq-num">现价</th>
                  <th className="aq-num">涨跌幅</th>
                  <th className="aq-num">成交额</th>
                  <th>行业 / 概念</th>
                </tr>
              </thead>
              <tbody>
                {e.lianbanStocks.slice(0, 20).map((item) => (
                  <tr key={item.code} {...clickableRowProps(() => onPick(item.code), "查看个股详情")}>
                    <td>
                      <strong>{item.name}</strong>
                      <div className="aq-code">{item.code}</div>
                    </td>
                    <td className="aq-num">
                      <Badge tone="warn">{item.boards} 板</Badge>
                    </td>
                    <td className="aq-num">{item.price.toFixed(2)}</td>
                    <td className={cn("aq-num", changeClass(item.pct))}>{formatSignedPct(item.pct)}</td>
                    <td className="aq-num">{formatYi(item.amount)}</td>
                    <td>{item.industry || "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ) : null}
    </div>
  );
}

function TurnoverBlock({ view, onPick }: { view: MarketView; onPick: (symbol: string) => void }) {
  if (view.turnover.length === 0) return null;
  return (
    <div className="aq-table-wrap">
      <table className="aq-table">
        <thead>
          <tr>
            <th>#</th>
            <th>代码 / 名称</th>
            <th className="aq-num">现价</th>
            <th className="aq-num">涨跌幅</th>
            <th className="aq-num">成交额</th>
            <th className="aq-num">流通市值</th>
            <th>行业</th>
          </tr>
        </thead>
        <tbody>
          {view.turnover.map((item, index) => (
            <tr key={item.code} {...clickableRowProps(() => onPick(item.code), "查看个股详情")}>
              <td className="aq-num">{index + 1}</td>
              <td>
                <strong>{item.name}</strong>
                <div className="aq-code">{item.code}</div>
              </td>
              <td className="aq-num">{item.price == null ? "—" : item.price.toFixed(2)}</td>
              <td className={cn("aq-num", changeClass(item.pct))}>{formatSignedPct(item.pct)}</td>
              <td className="aq-num">{formatYi(item.amount)}</td>
              <td className="aq-num">{formatYi(item.floatCap)}</td>
              <td>{item.industry || "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function IndustryBlock({ view }: { view: MarketView }) {
  const mode = useThemeMode();
  if (view.industryTop.length === 0 && view.industryBottom.length === 0) return null;
  const rows = [...view.industryBottom].reverse().concat(view.industryTop);
  const option = useMemo(() => industryOption(rows, mode), [rows.length, mode]);
  const table = (title: string, list: MarketView["industryTop"]) => (
    <div>
      <p className="aq-label">{title}</p>
      <div className="aq-table-wrap">
        <table className="aq-table">
          <thead>
            <tr>
              <th>#</th>
              <th>行业</th>
              <th className="aq-num">涨跌幅</th>
              <th className="aq-num">上涨 / 下跌</th>
            </tr>
          </thead>
          <tbody>
            {list.map((item) => (
              <tr key={`${title}-${item.code}-${item.rank}`}>
                <td className="aq-num">{item.rank}</td>
                <td>{item.name}</td>
                <td className={cn("aq-num", changeClass(item.changePct))}>{formatSignedPct(item.changePct)}</td>
                <td className="aq-num">
                  <span className="aq-tone-up">{item.upCount}</span> /{" "}
                  <span className="aq-tone-down">{item.downCount}</span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
  return (
    <EChart
      option={option}
      height={Math.max(200, rows.length * 24)}
      fallback={
        <div className="aq-flow-cols">
          {table("涨幅前列", view.industryTop)}
          {table("跌幅前列", view.industryBottom)}
        </div>
      }
    />
  );
}

function GlobalBlock({ view }: { view: MarketView }) {
  if (view.global.length === 0) return null;
  return (
    <div className="aq-index-grid">
      {view.global.map((item) => (
        <div className="aq-index-card" key={item.key}>
          <span className="aq-index-name">
            {item.name}
            <Tag>{item.region}</Tag>
          </span>
          <b className="aq-num">{item.price == null ? "—" : item.price.toFixed(2)}</b>
          <span className={cn("aq-num aq-index-change", changeClass(item.changePct))}>
            {formatSignedPct(item.changePct)}
          </span>
        </div>
      ))}
    </div>
  );
}

export function MarketPage() {
  const { view, loading, failedSections, refresh } = useMarketData();
  const [picked, setPicked] = useState<string | null>(null);
  const availability = view ? sectionAvailability(view) : null;

  const section = (opts: {
    title: string;
    description: string;
    icon: typeof Activity;
    available: boolean;
    emptyTitle: string;
    emptyDetail: string;
    children: React.ReactNode;
  }) => {
    return (
      <section className="aq-section">
        <SectionHeader title={opts.title} description={opts.description} icon={opts.icon} />
        {opts.available ? (
          opts.children
        ) : (
          <EmptyState title={opts.emptyTitle} detail={opts.emptyDetail} />
        )}
      </section>
    );
  };

  return (
    <div className="aq-page">
      <header className="aq-page-head">
        <div>
          <p className="aq-eyebrow">
            <Globe2 aria-hidden="true" />
            AQSP · 市场环境
          </p>
          <div className="aq-title-row">
            <h1>市场环境</h1>
            {view?.updated ? <strong>{view.updated}</strong> : null}
          </div>
          <p className="aq-page-sub">指数 · 涨跌家数 · 短线情绪 · 成交额榜 · 行业榜 · 隔夜外围</p>
        </div>
        <button type="button" className="aq-btn" onClick={refresh} disabled={loading} title="刷新市场数据">
          <RefreshCw className={cn(loading && "aq-spin")} aria-hidden="true" />
          刷新
        </button>
      </header>

      {failedSections.length > 0 ? (
        <ToneCallout
          tone="warn"
          title="部分数据源本次未取到"
          detail={`${failedSections.join("、")} 读取失败，其余板块照常显示。可点「刷新」重试。`}
        />
      ) : null}

      {loading && !view ? <LoadingState label="正在读取市场环境数据…" /> : null}

      {!loading && !view ? (
        <StatePanel tone="warn">市场环境数据暂不可用，请确认后端已启动。</StatePanel>
      ) : null}

      {view && availability ? (
        <>
          {section({
            title: "大盘指数",
            description: "上证 / 深证成指 / 创业板指 / 沪深300",
            icon: TrendingUp,
            available: availability.indices,
            emptyTitle: "指数行情未返回",
            emptyDetail: "行情源未返回指数数据，可稍后刷新重试。",
            children: <IndexCards view={view} />,
          })}

          {section({
            title: "市场宽度与板块资金",
            description: "涨跌家数 · 涨跌停 · 活跃度 · 行业资金净流入",
            icon: BarChart3,
            available: availability.breadth,
            emptyTitle: "涨跌家数未返回",
            emptyDetail: "市场活跃度数据源本次未返回；成交额榜与行业榜不受影响。",
            children: (
              <>
                <BreadthBlock view={view} />
                <SectorFlowBlock view={view} />
              </>
            ),
          })}

          {section({
            title: "短线情绪",
            description: "连板梯队 · 封板率 / 炸板率 / 晋级率 · 连板股清单",
            icon: Flame,
            available: availability.emotion,
            emptyTitle: "短线情绪未返回",
            emptyDetail: "涨停板数据源本次未返回（非交易日或盘前常见）。",
            children: <EmotionBlock view={view} onPick={setPicked} />,
          })}

          {section({
            title: "成交额榜",
            description: "全市场成交额 Top20 · 客观榜单，非推荐",
            icon: Layers,
            available: availability.turnover,
            emptyTitle: "成交额榜未返回",
            emptyDetail: "行情中心本次未返回榜单数据。",
            children: <TurnoverBlock view={view} onPick={setPicked} />,
          })}

          {section({
            title: "行业涨跌榜",
            description: `全行业涨跌幅排名${view.industryTotal ? ` · 共 ${view.industryTotal} 个行业` : ""}`,
            icon: Activity,
            available: availability.industry,
            emptyTitle: "行业排名未返回",
            emptyDetail: "行业板块数据源本次未返回。",
            children: <IndustryBlock view={view} />,
          })}

          {section({
            title: "隔夜外围",
            description: "美股 / 港股指数 —— A 股开盘前的外部环境",
            icon: Globe2,
            available: availability.global,
            emptyTitle: "外围指数未返回",
            emptyDetail: "海外指数数据源本次未返回。",
            children: <GlobalBlock view={view} />,
          })}
        </>
      ) : null}

      <StockDetailDrawer symbol={picked} onClose={() => setPicked(null)} />
    </div>
  );
}
