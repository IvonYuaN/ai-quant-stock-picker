// 数据源健康提示的契约断言（由 npm test / run-contracts 真正求值）。
//
// 这条提示存在的**唯一理由**：面板为空有两种原因，而界面此前只会说同一句话。
//   - 今天确实没数据（正常）
//   - 源连不上（该动手）
// 所以断言必须守住两件事：
//   1. 健康时不提示（误报比不报更糟，用户会以为行情坏了）；
//   2. 提示里同时出现「哪个源坏了」和「空不代表没数据」。
import type { DataSourceHealthRow } from "./api";
import { summarizeSourceHealth } from "./market-view";

function row(over: Partial<DataSourceHealthRow> = {}): DataSourceHealthRow {
  return {
    source: "push2.eastmoney.com",
    healthy: false,
    consecutive_errors: 12,
    ok_count: 0,
    error_count: 12,
    last_ok_at: null,
    last_error_at: "2026-10-19T12:19:00+08:00",
    last_error: "ConnectionError: RemoteDisconnected",
    down_since: "2026-10-19T11:37:02+08:00",
    ...over,
  };
}

const healthy = summarizeSourceHealth([
  row({ source: "push2ex.eastmoney.com", healthy: true, consecutive_errors: 0, down_since: null }),
]);
const single = summarizeSourceHealth([row()]);
const four = summarizeSourceHealth([
  row({ source: "push2.eastmoney.com" }),
  row({ source: "push2his.eastmoney.com" }),
  row({ source: "push2delay.eastmoney.com" }),
  row({ source: "push2ex.eastmoney.com" }),
]);
const unknownHost = summarizeSourceHealth([row({ source: "example.internal" })]);
const noStamp = summarizeSourceHealth([row({ down_since: null })]);
const zeroConsecutive = summarizeSourceHealth([row({ consecutive_errors: 0 })]);

export const sourceHealthContract = {
  /* ---- 不误报 ---- */
  silentWhenAllHealthy: healthy === null,
  silentWhenNoRows: summarizeSourceHealth([]) === null,
  silentWhenLedgerMissing: summarizeSourceHealth(null) === null,
  silentWhenUndefined: summarizeSourceHealth(undefined) === null,
  // 台账行缺失（旧版后端/字段漂移）时不能把整页提示条炸掉
  toleratesMissingRows: summarizeSourceHealth([undefined as unknown as DataSourceHealthRow]) === null,

  /* ---- 说清是哪个源 ---- */
  reportsUnhealthyCount: single?.unhealthy === 1,
  namesKnownHost: single?.detail.includes("东财行情") === true,
  // 认不出的主机原样显示，不编造标签
  keepsUnknownHostVerbatim: unknownHost?.detail.includes("example.internal") === true,
  titleIsPlain: single?.title === "数据源不可达",
  // 连续失败次数是判断「偶发」还是「持续」的关键信息
  reportsConsecutiveErrors: single?.detail.includes("连续 12 次失败") === true,
  // 故障起点用后端给的 +08:00 ISO，只截成「MM-DD HH:MM」，不做时区换算
  reportsDownSince: single?.detail.includes("自 10-19 11:37") === true,
  omitsSinceWhenUnknown: noStamp?.detail.includes("自 ") === false,
  // 没有连续失败计数时退化成「失败」，不写成「连续 0 次失败」这种自相矛盾的话
  noZeroConsecutive: zeroConsecutive?.detail.includes("连续 0 次") === false,

  /* ---- 数量多时不刷屏 ---- */
  namesAtMostThree: four?.unhealthy === 4,
  collapsesTheRest: four?.detail.includes("等 4 个源") === true,

  /* ---- 人话标签覆盖台账里会出现的每个主机 ---- */
  // 这些键必须与 backend 实际请求的主机一致；漏一个的后果只是显示原始域名，
  // 但「东财行情（…）」这种说法能让非运维用户也知道是哪块面板受影响。
  labelsCoverAllEastmoneyHosts: [
    "push2.eastmoney.com",
    "push2his.eastmoney.com",
    "push2delay.eastmoney.com",
    "push2ex.eastmoney.com",
    "datacenter-web.eastmoney.com",
    "searchapi.eastmoney.com",
  ].every((host) => (summarizeSourceHealth([row({ source: host })])?.detail ?? "").includes("东财")),
  labelsCoverAkshareHosts: [
    "legulegu.com",
    "data.10jqka.com.cn",
  ].every((host) => (summarizeSourceHealth([row({ source: host })])?.detail ?? "").length > 0),

  /* ---- 必须纠正那句假陈述 ---- */
  saysEmptyIsNotNoData: single?.detail.includes("不代表今天没有数据") === true,
};
