// 个股外部链接。
//
// 为什么需要：个股的深度数据（K线 / 财务 / 资金流 / 龙虎榜 / 研报 / 公告 / 互动易）
// 东方财富、同花顺、巨潮早已做得远比本项目细致，重复造一遍既费力又落后。
//
// 本项目的定位是**选股与复盘**：回答"为什么选它、证据是什么、今天能不能动"。
// 其余交给专业站点 —— 这里只负责把用户准确送过去。
export type MarketCode = "SH" | "SZ" | "BJ";

/** 沪深京判断：6=沪市，0/3=深市，4/8=北交所。非法代码返回 null（不要瞎猜市场）。 */
export function marketOf(code: string): MarketCode | null {
  if (!/^\d{6}$/.test(code)) return null;
  const head = code[0];
  if (head === "6") return "SH";
  if (head === "0" || head === "3") return "SZ";
  if (head === "4" || head === "8") return "BJ";
  return null;
}

export interface ExternalLink {
  key: string;
  label: string;
  /** 站点简称，显示在按钮副标题，让用户点之前就知道会跳去哪 */
  site: string;
  url: string;
}

/**
 * 生成个股外链。代码非法时返回空数组 —— 宁可不给链接，也不要给一个会 404 的链接。
 */
export function externalLinks(code: string): ExternalLink[] {
  const market = marketOf(code);
  if (!market) return [];
  const lower = market.toLowerCase();
  return [
    {
      key: "em-quote",
      label: "行情与盘口",
      site: "东方财富",
      url: `https://quote.eastmoney.com/${lower}${code}.html`,
    },
    {
      key: "em-flow",
      label: "资金流向",
      site: "东方财富",
      url: `https://data.eastmoney.com/zjlx/${code}.html`,
    },
    {
      key: "em-report",
      label: "券商研报",
      site: "东方财富",
      url: `https://data.eastmoney.com/report/singlestock.jshtml?stockcode=${code}`,
    },
    {
      key: "ths",
      label: "个股全景",
      site: "同花顺",
      url: `https://stockpage.10jqka.com.cn/${code}/`,
    },
    {
      key: "xq",
      label: "讨论与观点",
      site: "雪球",
      url: `https://xueqiu.com/S/${market}${code}`,
    },
    {
      key: "cninfo",
      label: "法定公告",
      site: "巨潮资讯",
      url: `http://www.cninfo.com.cn/new/disclosure/stock?stockCode=${code}`,
    },
  ];
}
