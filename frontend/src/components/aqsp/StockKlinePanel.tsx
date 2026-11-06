import { useEffect, useMemo, useState } from "react";
import { EChart } from "@/components/ui/EChart";
import { EmptyState, LoadingState, SectionHeader, StatePanel } from "@/components/ui/primitives";
import { api, type KlinePayload } from "@/lib/api";
import { formatEvidenceTime } from "@/lib/format";
import { KLINE_RANGE_BARS, normalizeKlineBars, stockKlineOption, type KlineRange } from "@/lib/stock-kline-view";
import { useThemeMode } from "@/lib/theme-mode";

const RANGE_LABELS: ReadonlyArray<readonly [KlineRange, string]> = [
  ["3m", "3个月"],
  ["6m", "6个月"],
  ["1y", "1年"],
];

function sourceLabel(source: string): string {
  if (source === "mootdx") return "通达信日线";
  if (source === "tencent") return "腾讯日线备源";
  return "未知行情源";
}

export function StockKlinePanel({ symbol }: { symbol: string }) {
  const [payload, setPayload] = useState<KlinePayload | null>(null);
  const [payloadSymbol, setPayloadSymbol] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [range, setRange] = useState<KlineRange>("6m");
  const [reload, setReload] = useState(0);
  const [compact, setCompact] = useState(() => typeof window !== "undefined" && window.innerWidth <= 767);
  const mode = useThemeMode();

  useEffect(() => {
    const media = window.matchMedia("(max-width: 767px)");
    const update = () => setCompact(media.matches);
    update();
    media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, []);

  useEffect(() => {
    let current = true;
    setPayload(null);
    setLoading(true);
    setError("");
    api.kline(symbol).then((result) => {
      if (current) {
        setPayload(result);
        setPayloadSymbol(symbol);
      }
    }).catch((reason: unknown) => {
      if (current) setError(reason instanceof Error ? reason.message : "日K数据读取失败");
    }).finally(() => {
      if (current) setLoading(false);
    });
    return () => { current = false; };
  }, [symbol, reload]);

  const currentPayload = payloadSymbol === symbol ? payload : null;
  const rows = useMemo(() => normalizeKlineBars(currentPayload?.data ?? []), [currentPayload]);
  const visibleRows = useMemo(() => rows.slice(-KLINE_RANGE_BARS[range]), [rows, range]);
  const option = useMemo(() => stockKlineOption(visibleRows, mode), [visibleRows, mode]);
  const meta = currentPayload?.meta;
  const basisLabel = meta?.price_basis === "qfq_display"
    ? "价格口径：前复权，仅用于展示"
    : meta?.price_basis === "provider_default"
      ? "价格口径：数据源默认口径，未声明复权状态，仅用于展示"
      : "价格口径：未记录，仅用于展示";

  return (
    <section className="aq-detail-block aq-stock-kline" aria-label="个股日K走势">
      <SectionHeader
        title="日K走势"
        description="历史行情仅作走势查看，不替代研究快照、交易价格或回测数据。"
        count={meta ? `${sourceLabel(meta.source)} · 数据截至 ${meta.as_of || "未标注"}` : undefined}
      >
        <div className="aq-kline-ranges" role="group" aria-label="K线显示区间">
          {RANGE_LABELS.map(([value, label]) => (
            <button
              key={value}
              type="button"
              className={`aq-btn${range === value ? " aq-btn-primary" : ""}`}
              aria-pressed={range === value}
              onClick={() => setRange(value)}
            >
              {label}
            </button>
          ))}
        </div>
      </SectionHeader>
      {loading || payloadSymbol !== symbol ? <LoadingState label="读取日K与成交量…" /> : null}
      {!loading && payloadSymbol === symbol && error ? (
        <StatePanel tone="warn" action={<button type="button" className="aq-btn" onClick={() => setReload((value) => value + 1)}>重试图表</button>}>
          {error}
        </StatePanel>
      ) : null}
      {!loading && payloadSymbol === symbol && !error && rows.length === 0 ? (
        <EmptyState title="日K行情未返回" detail="当前行情源没有可展示的日线数据；未用实时价格或候选评分补造图表。" />
      ) : null}
      {!loading && payloadSymbol === symbol && !error && visibleRows.length > 0 ? (
        <>
          <p className="aq-detail-muted">{basisLabel} · 取数 {meta?.generated_at ? formatEvidenceTime(meta.generated_at) : "时间未记录"}</p>
          <EChart
            option={option}
            height={compact ? 175 : 340}
            fallback={<p className="aq-detail-muted">图表环境不可用。已读取 {visibleRows.length} 根日K，数据截至 {meta?.as_of || visibleRows[visibleRows.length - 1]?.date || "未标注"}。</p>}
          />
        </>
      ) : null}
    </section>
  );
}
