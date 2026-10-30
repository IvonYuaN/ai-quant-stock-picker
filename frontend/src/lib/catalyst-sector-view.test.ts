import { MemoryRouter } from "react-router-dom";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server.edge";
import { CatalystSectorView } from "@/components/aqsp/CatalystSectorSection";
import { InfoStreamLoadStatus, InfoStreamPage } from "@/pages/InfoStreamPage";
import { ConceptsPage } from "@/pages/ConceptsPage";
import { catalystReportMetadata, catalystSourceUrl } from "./catalyst-sector-view";
import { type CatalystData, type CatalystEvent } from "./catalyst-client";
import { LEGACY_ROUTES, NAV_LINES } from "./ia";

const event: CatalystEvent = {
  title: "半导体政策材料", source: "原文来源", published_at: "2020-01-01T09:05:00+08:00",
  impact: "positive", category: "政策", url: "https://example.com/original",
  affected_sectors: ["半导体", "设备"], affected_symbols: [],
  transmission_path: ["政策", "国产设备"], transmission_hypothesis: "报告中的设备需求传导假设",
};
const report: CatalystData = {
  date: "2020-01-01", generated_at: "2020-01-02T12:30:00+08:00", source_status: "partial",
  warnings: ["private operational command"], events: [event],
};
const render = (data: CatalystData | null, error = "", loading = false) => renderToStaticMarkup(createElement(CatalystSectorView, {
  state: { data, error, loading }, onRefresh: () => {},
}));
const html = render(report);
const missing = render({ source_status: "no_data", warnings: ["please run news-catalysts"], events: [] });
const failed = render(report, "请求失败");
const current = catalystReportMetadata({ date: "2026-10-23", source_status: "ok", events: [] }, "2026-10-23");
const future = catalystReportMetadata({ date: "2026-10-24", events: [] }, "2026-10-23");
const unknown = catalystReportMetadata({ generated_at: "2026-10-23T14:00:00+08:00", source_status: "toString", events: [] }, "2026-10-23");

export const catalystSectorViewContract = {
  groupsExistingAffectedSectors: html.includes("半导体</h3>") && html.includes("设备</h3>") && html.includes("2 个板块"),
  keepsReportDateSeparateFromGeneratedTime: html.includes("报告日期 2020-01-01") && html.includes("报告生成 01-02 12:30"),
  usesEventPublicationTime: html.includes("事件发布时间：01-01 09:05") && !html.includes("事件发布时间：01-02 12:30"),
  preservesTransmissionPathAndHypothesis: html.includes("政策 → 国产设备") && html.includes("报告中的设备需求传导假设"),
  providesSafeOriginalSource: html.includes('href="https://example.com/original"') && html.includes('rel="noopener noreferrer"'),
  historyIsExplicit: html.includes("历史报告 · 非今日信号"),
  sourceFailureIsIndependentOfDate: html.includes("部分来源缺失"),
  defaultEvidenceIsCollapsed: html.includes('<details class="aq-catalyst-evidence">') && !html.includes(" open="),
  noReportIsDifferentFromHealthyEmpty: missing.includes("催化报告尚未生成") && render({ source_status: "ok", events: [] }).includes("当前报告没有催化事件"),
  noReportDoesNotExposeRuntimeCommands: !missing.includes("news-catalysts") && !html.includes("private operational command"),
  readFailureKeepsDatedEvidenceWithoutDuplicateWarning: failed.includes("仍显示上次成功读取的报告") && failed.includes("半导体政策材料") && failed.includes("2020-01-01") && (failed.match(/aq-state-warn/g) ?? []).length === 1,
  failureWithoutDataKeepsRetry: render(null, "请求失败").includes("重试") && !render(null, "请求失败").includes("暂无可聚合"),
  refreshKeepsPriorEvidenceVisible: render(report, "", true).includes("保留上次内容") && render(report, "", true).includes("半导体政策材料"),
  sourceUnknownIsNotHealthy: unknown.sourceLabel === "来源状态未记录" && !unknown.sourceOk,
  generationCannotInventReportDate: unknown.reportDate === "未记录有效日期" && !unknown.current,
  validCurrentReportIsRecognized: current.current && current.dateStatus === "当日报告" && current.sourceOk,
  futureDateIsNotCurrent: !future.current && future.dateStatus.includes("异常"),
  invalidCalendarDateIsRejected: !catalystReportMetadata({ date: "2026-02-30", events: [] }, "2026-10-23").current && catalystReportMetadata({ date: "2026-02-30", events: [] }).reportDate === "未记录有效日期",
  javascriptAndDataLinksAreRejected: !catalystSourceUrl("javascript:alert(1)") && !catalystSourceUrl("data:text/html,a") && !catalystSourceUrl("/relative"),
  invalidLinkRendersSourceWithoutAnchor: !render({ ...report, events: [{ ...event, url: "javascript:alert(1)" }] }).includes("javascript:") && render({ ...report, events: [{ ...event, url: "javascript:alert(1)" }] }).includes("来源：原文来源"),
  unclassifiedEventRemainsVisible: render({ ...report, events: [{ ...event, affected_sectors: [] }] }).includes("未分类</h3>"),
  missingEventTimeIsExplicit: render({ ...report, events: [{ ...event, published_at: "" }] }).includes("事件发布时间：未记录"),
  streamMountsCatalystIndependentlyOfNewsAvailability: renderToStaticMarkup(createElement(MemoryRouter, null, createElement(InfoStreamPage))).includes("板块催化与传导"),
  streamFailureKeepsPriorDataWithOneRetryableWarning: (() => {
    const html = renderToStaticMarkup(createElement(InfoStreamLoadStatus, {
      error: "连接超时", loading: false, hasData: true, onRefresh: () => {},
    }));
    return html.includes("连接超时；仍显示上次成功读取的数据") && html.includes("重试") &&
      (html.match(/aq-state-warn/g) ?? []).length === 1;
  })(),
  legacyWrapperSharesSameSection: renderToStaticMarkup(createElement(ConceptsPage)).includes("板块催化与传导"),
  routeAndFourNavigationRemain: LEGACY_ROUTES["/concepts"] === "/stream" && NAV_LINES.flatMap((line) => line.items).length === 4,
};
