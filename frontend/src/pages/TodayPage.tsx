// 今日研究：系统线主页。
//
// 改造前这里叫「每日复盘」，另有一个独立的「今日推荐」页 —— 两者其实是同一个交易日的
// 同一份数据，拆成两页会让"今天能不能动"被割到两个入口，还容易口径不一致。
// 现在合并为一页，按判断顺序分段，细节收进页签。
import { TodayWorkspace } from "@/components/aqsp/TodayWorkspace";

export function TodayPage() {
  return <TodayWorkspace />;
}
