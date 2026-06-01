# launchd 装载状态（PR22 T2）

## 装载日期
2026-05-28

## launchctl list 输出
```
# launchctl load 因沙箱 TCC 权限被拒（Bootstrap failed: 5: Input/output error）
# 需用户手动执行：
#   cp scripts/launchd/com.aqsp.daily.plist ~/Library/LaunchAgents/
#   launchctl load ~/Library/LaunchAgents/com.aqsp.daily.plist
#   launchctl list | grep com.aqsp
# plist 文件已就位: ~/Library/LaunchAgents/com.aqsp.daily.plist ✓
# 但 launchctl load 需在终端中手动执行，agent 沙箱无权限操作 LaunchAgents 域
```

## 第一次手动触发结果
- 跑前 ledger 行数: 3
- 跑后 ledger 行数: 3
- 是否新增信号: 否
- 原因: `aqsp run --source akshare` 在本地网络环境因 SNI 阻断无法访问 push2his.eastmoney.com（ProxyError），run 子命令失败。briefing 子命令正常执行并生成了日报内容（含 600519/300750/000001 三只候选），但因 run 未产出新信号，ledger 未增长。

## 下次自动触发
需用户先手动装载 launchd 后，预期下个工作日 16:00（UTC+8）由 launchd 触发。
但 run 子命令是否成功取决于网络环境是否能访问 eastmoney API。

## 卸载方式
```
launchctl unload ~/Library/LaunchAgents/com.aqsp.daily.plist
rm ~/Library/LaunchAgents/com.aqsp.daily.plist
```