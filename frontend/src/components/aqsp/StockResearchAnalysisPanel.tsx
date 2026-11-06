import { useEffect, useRef, useState, type FormEvent } from "react";
import { Check, RefreshCw, Save, Sparkles } from "lucide-react";
import { useSearchParams } from "react-router-dom";
import { api, type StockResearchPersonalContext, type StockResearchReport } from "@/lib/api";
import { EmptyState, LoadingState, SectionHeader, StatePanel, Tag } from "@/components/ui/primitives";
import { formatStockResearchEvidenceValue, resolveStockResearchEvidence } from "@/lib/stock-research-analysis-view";
import { formatEvidenceTime } from "@/lib/format";

const DIMENSIONS = [
  ["technical", "技术"],
  ["fundamental", "基本面"],
  ["financial", "财务"],
  ["news", "消息"],
] as const;

const DIMENSION_STATUS: Record<string, { label: string; tone: "ok" | "warn" | "neutral" }> = {
  available: { label: "证据可用", tone: "ok" },
  partial: { label: "部分可用", tone: "warn" },
  unavailable: { label: "证据缺失", tone: "neutral" },
};

const emptyContext = (): StockResearchPersonalContext => ({
  personal_intent: "",
  time_horizon: "",
  personal_hypothesis: "",
  counterargument: "",
});

function errorText(reason: unknown): string {
  return reason instanceof Error ? reason.message : "请求未完成，请稍后重试。";
}

function EvidenceReferences({ ids, report }: { ids: readonly string[]; report: StockResearchReport }) {
  const items = resolveStockResearchEvidence(ids, report.evidence);
  if (items.length === 0) return null;
  return <ul className="aq-stock-research-citations">{items.map(({ id, item }) => (
    <li key={id}>
      <details>
        <summary>{item?.title || id}</summary>
        {item ? <p>{formatStockResearchEvidenceValue(item.value)}{item.source ? ` · ${item.source}` : ""}{item.as_of ? ` · ${item.as_of}` : ""}{item.price_basis ? ` · ${item.price_basis}` : ""}</p> : <p>报告引用了不可解析的证据编号：{id}</p>}
      </details>
    </li>
  ))}</ul>;
}

export function StockResearchAnalysisPanel({ symbol, currentResearch, historicalResearch, candidateResearch }: {
  symbol: string;
  currentResearch: boolean;
  historicalResearch: boolean;
  candidateResearch: boolean;
}) {
  const [searchParams] = useSearchParams();
  const linkedReportId = searchParams.get("report_id") ?? "";
  const [context, setContext] = useState<StockResearchPersonalContext>(emptyContext);
  const [report, setReport] = useState<StockResearchReport | null>(null);
  const [savedReports, setSavedReports] = useState<StockResearchReport[]>([]);
  const [loadingReports, setLoadingReports] = useState(true);
  const [reportsError, setReportsError] = useState("");
  const [busy, setBusy] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [errorAction, setErrorAction] = useState<"analyze" | "save" | "report" | "">("");
  const requestSequence = useRef(0);
  const formRef = useRef<HTMLFormElement>(null);

  useEffect(() => {
    const requestId = ++requestSequence.current;
    setContext(emptyContext());
    setReport(null);
    setError("");
    setErrorAction("");
    setBusy(false);
    setSaving(false);
    setLoadingReports(true);
    setReportsError("");
    setSavedReports([]);
    let active = true;
    api.stockResearchReports(symbol).then(({ items }) => {
      if (active) setSavedReports(items);
    }).catch(() => {
      if (active) {
        setSavedReports([]);
        setReportsError("已保存报告暂不可用；不会把读取失败当成没有历史记录。");
      }
    }).finally(() => {
      if (active) setLoadingReports(false);
    });
    if (linkedReportId) {
      api.stockResearchReport(linkedReportId).then((saved) => {
        if (!active) return;
        if (saved.symbol !== symbol || !saved.saved) {
          setError("链接报告与当前股票不匹配或尚未保存。");
          setErrorAction("report");
          return;
        }
        setReport(saved);
      }).catch((reason: unknown) => {
        if (active) {
          setError(errorText(reason));
          setErrorAction("report");
        }
      });
    }
    return () => {
      active = false;
      requestSequence.current = Math.max(requestSequence.current, requestId + 1);
    };
  }, [symbol, linkedReportId]);

  useEffect(() => {
    if (currentResearch && !candidateResearch) return;
    requestSequence.current += 1;
    setReport(null);
    setBusy(false);
    setSaving(false);
    setError("");
    setErrorAction("");
  }, [currentResearch, candidateResearch]);

  const updateContext = (key: keyof StockResearchPersonalContext, value: string) => {
    setContext((previous) => ({ ...previous, [key]: value }));
    setReport(null);
    setError("");
    setErrorAction("");
  };

  async function analyze(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy || saving || !currentResearch || candidateResearch) return;
    const requestId = ++requestSequence.current;
    setBusy(true);
    setReport(null);
    setError("");
    setErrorAction("");
    try {
      const next = await api.stockResearchAnalyze(symbol, {
        ...context,
        personal_intent: context.personal_intent.trim(),
        time_horizon: context.time_horizon.trim(),
        personal_hypothesis: context.personal_hypothesis.trim(),
        counterargument: context.counterargument.trim(),
      });
      if (requestSequence.current === requestId) setReport(next);
    } catch (reason) {
      if (requestSequence.current === requestId) {
        setError(errorText(reason));
        setErrorAction("analyze");
      }
    } finally {
      if (requestSequence.current === requestId) setBusy(false);
    }
  }

  async function saveReport() {
    if (!report || report.status !== "complete" || report.saved || saving) return;
    setSaving(true);
    setError("");
    setErrorAction("");
    try {
      const saved = await api.saveStockResearchReport(report.report_id);
      if (saved.symbol !== symbol) throw new Error("报告标的已变化，请刷新后重试。");
      setReport(saved);
      setSavedReports((previous) => [saved, ...previous.filter((item) => item.report_id !== saved.report_id)]);
    } catch (reason) {
      setError(errorText(reason));
      setErrorAction("save");
    } finally {
      setSaving(false);
    }
  }

  return (
    <section id="stock-research-analysis" className="aq-detail-block aq-stock-research-analysis" aria-label="个股四维研究">
      <SectionHeader
        title="四维研究"
        description="技术、基本面、财务与消息证据分别核对；不改确定性评分，也不生成买卖指令。"
        icon={Sparkles}
        count={loadingReports ? undefined : `${savedReports.length} 份已存报告`}
      />

      {candidateResearch ? (
        <StatePanel tone="neutral">当前股票属于量化候选，请使用同日候选复核；该流程绑定当前研究快照，并保留本人的问题、假设与反方理由。</StatePanel>
      ) : !currentResearch ? (
        <StatePanel tone="neutral">{historicalResearch ? "当前页面绑定了历史或指定日期研究。四维分析只读取当前证据，不能附加到所选日期。" : "当前研究时点尚未确认，暂不能生成四维报告。"}</StatePanel>
      ) : (
        <form ref={formRef} className="aq-stock-research-form" onSubmit={(event) => void analyze(event)}>
          <fieldset className="aq-candidate-context" disabled={busy || saving}>
            <legend>本人研究上下文</legend>
            <div className="aq-candidate-context-grid">
              <label>
                这次想弄清什么
                <textarea className="aq-input" value={context.personal_intent} maxLength={500} onChange={(event) => updateContext("personal_intent", event.target.value)} required placeholder="写下希望这次研究回答的问题" />
              </label>
              <label>
                观察期限
                <input className="aq-input" value={context.time_horizon} maxLength={120} onChange={(event) => updateContext("time_horizon", event.target.value)} required placeholder="例如：下一个财报披露前" />
              </label>
              <label>
                本人假设
                <textarea className="aq-input" value={context.personal_hypothesis} maxLength={500} onChange={(event) => updateContext("personal_hypothesis", event.target.value)} placeholder="当前判断；没有就留空" />
              </label>
              <label>
                反方理由
                <textarea className="aq-input" value={context.counterargument} maxLength={500} onChange={(event) => updateContext("counterargument", event.target.value)} placeholder="可能推翻判断的证据或条件" />
              </label>
            </div>
          </fieldset>
          <div className="aq-stock-research-actions">
            <p className="aq-note">分析只引用服务端取得并标明时点的证据；不可用的数据会单独列出。</p>
            <button type="submit" className="aq-btn aq-btn-primary" disabled={busy || saving || !context.personal_intent.trim() || !context.time_horizon.trim()}>
              {busy ? <RefreshCw className="aq-spin" aria-hidden="true" /> : <Sparkles aria-hidden="true" />}
              {busy ? "模型分析中…" : report ? "重新分析" : "生成四维分析"}
            </button>
          </div>
        </form>
      )}

      {busy ? <LoadingState label="正在读取证据并等待模型完成；离开当前股票会丢弃未完成结果。" /> : null}
      {error ? <StatePanel tone="warn" action={currentResearch && errorAction !== "report" ? <button type="button" className="aq-btn" onClick={() => {
        if (errorAction === "save") void saveReport();
        else formRef.current?.requestSubmit();
      }}>重试{errorAction === "save" ? "保存" : "分析"}</button> : undefined}>{error}</StatePanel> : null}

      {report?.status === "failed" ? (
        <StatePanel tone="warn">{report.issue || "模型分析未通过校验，未形成报告。"}</StatePanel>
      ) : null}
      {report?.status === "complete" ? (
        <div className="aq-stock-research-result">
          <div className="aq-stock-research-summary">
            <div className="aq-stock-research-meta">
              <Tag>{report.name} · {report.symbol}</Tag>
              <Tag>生成 {formatEvidenceTime(report.generated_at)}</Tag>
              {report.saved_at ? <Tag>保存 {formatEvidenceTime(report.saved_at)}</Tag> : null}
              {report.model ? <Tag>{report.model}</Tag> : null}
              {report.saved ? <Tag tone="ok">已保存到复盘</Tag> : <Tag tone="neutral">未保存</Tag>}
            </div>
            <p>{report.summary}</p>
            <EvidenceReferences ids={report.summary_evidence_ids} report={report} />
            {report.missing_evidence.length ? <p className="aq-note">尚缺证据：{report.missing_evidence.join("；")}</p> : null}
            <div className="aq-stock-research-actions">
              <p className="aq-note">{report.notice}</p>
              {currentResearch && !report.saved ? (
                <button type="button" className="aq-btn aq-btn-primary" onClick={() => void saveReport()} disabled={saving}>
                  {saving ? <RefreshCw className="aq-spin" aria-hidden="true" /> : <Save aria-hidden="true" />}
                  {saving ? "保存中…" : "保存并加入复盘"}
                </button>
              ) : report.saved ? <Tag tone="ok"><Check aria-hidden="true" />已保存</Tag> : null}
            </div>
          </div>

          <div className="aq-stock-research-dimensions">
            {DIMENSIONS.map(([key, label]) => {
              const dimension = report.dimensions[key];
              const status = DIMENSION_STATUS[dimension.status] ?? DIMENSION_STATUS.unavailable;
              return (
                <article className="aq-stock-research-dimension" key={key}>
                  <header><h3>{label}</h3><Tag tone={status.tone}>{status.label}</Tag></header>
                  {dimension.findings.length ? <ul>{dimension.findings.map((finding, index) => (
                    <li key={`${key}-${index}`}>
                      <p>{finding.text}</p>
                      {finding.evidence_ids.length ? <EvidenceReferences ids={finding.evidence_ids} report={report} /> : <p className="aq-detail-muted">没有可核验的证据引用。</p>}
                    </li>
                  ))}</ul> : <EmptyState title="没有通过校验的结论" detail="模型未能基于当前证据形成可引用的判断。" />}
                  {dimension.missing.length ? <p className="aq-note">缺少：{dimension.missing.join("；")}</p> : null}
                </article>
              );
            })}
          </div>
        </div>
      ) : null}

      <div className="aq-stock-research-saved">
        <h3>已保存的研究报告</h3>
        {loadingReports ? <LoadingState label="读取已保存报告…" /> : reportsError ? <StatePanel tone="warn">{reportsError}</StatePanel> : savedReports.length ? (
          <ul>{savedReports.slice(0, 5).map((item) => <li key={item.report_id}>
            <div><strong>{item.generated_at}</strong><Tag>{item.model || "模型未标注"}</Tag></div>
            <p>{item.summary}</p>
          </li>)}</ul>
        ) : <EmptyState title="尚无已保存报告" detail="完成分析后，保存的报告会出现在这里和个人复盘索引。" />}
      </div>
    </section>
  );
}
