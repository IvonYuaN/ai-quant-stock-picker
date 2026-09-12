// 外链构建的契约断言（由 npm test 真正执行）。
//
// 重点是"跳对地方"：市场判断错了，链接就会 404 或跳到另一只票 ——
// 这种错误在界面上只是一个"点了没反应/内容不对"，很难被发现。
import { externalLinks, marketOf, type MarketCode } from "./external-links";

const sh = externalLinks("600519");
const sz = externalLinks("300750");
const bj = externalLinks("830799");

const byKey = (list: ReturnType<typeof externalLinks>, key: string) => list.find((l) => l.key === key)?.url ?? "";

export const externalLinksContract = {
  /* ---- 市场判断 ---- */
  sixIsShanghai: marketOf("600519") === "SH",
  zeroIsShenzhen: marketOf("000001") === ("SZ" as MarketCode),
  threeIsShenzhen: marketOf("300750") === ("SZ" as MarketCode),
  eightIsBeijing: marketOf("830799") === ("BJ" as MarketCode),
  fourIsBeijing: marketOf("430047") === ("BJ" as MarketCode),
  // 非法代码必须返回 null，不能瞎猜
  tooShortIsNull: marketOf("60051") === null,
  tooLongIsNull: marketOf("6005190") === null,
  nonNumericIsNull: marketOf("abcdef") === null,
  emptyIsNull: marketOf("") === null,
  unknownHeadIsNull: marketOf("900001") === null,

  /* ---- 链接生成 ---- */
  shLinksUseLowerMarket: byKey(sh, "em-quote") === "https://quote.eastmoney.com/sh600519.html",
  szLinksUseLowerMarket: byKey(sz, "em-quote") === "https://quote.eastmoney.com/sz300750.html",
  bjLinksGenerated: bj.length > 0,
  // 雪球用的是大写市场前缀，与东财相反，容易写错
  xueqiuUsesUpperMarket: byKey(sz, "xq") === "https://xueqiu.com/S/SZ300750",
  thsUrlUsesCodeOnly: byKey(sz, "ths") === "https://stockpage.10jqka.com.cn/300750/",
  flowUrlUsesCodeOnly: byKey(sz, "em-flow") === "https://data.eastmoney.com/zjlx/300750.html",
  reportUrlUsesCodeOnly:
    byKey(sz, "em-report") === "https://data.eastmoney.com/report/singlestock.jshtml?stockcode=300750",
  cninfoUrlUsesCodeOnly:
    byKey(sz, "cninfo") === "http://www.cninfo.com.cn/new/disclosure/stock?stockCode=300750",

  /* ---- 边界 ---- */
  // 非法代码不给链接：宁可不给，也不要给会 404 的
  invalidCodeYieldsNoLinks: externalLinks("60051").length === 0,
  invalidCodeYieldsNoLinksForLetters: externalLinks("abc").length === 0,
  everyLinkHasSiteLabel: sh.every((link) => link.site.length > 0 && link.label.length > 0),
  everyLinkIsHttpsOrHttp: sh.every((link) => /^https?:\/\//.test(link.url)),
  keysAreUnique: new Set(sh.map((l) => l.key)).size === sh.length,
};
