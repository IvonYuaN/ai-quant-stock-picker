# Project Profile

## Summary

AI 量化选股原型，目标是把开源量化生态与 `daily_stock_analysis` 的分析/推送能力衔接起来，先交付可解释、可测试的候选池生成器。

## Stack

Python 3.10+，pandas/numpy，AKShare 可选数据源，pytest 验证。

## Default Squad

Primary: Quant Research Engineer.

Support: Data Engineer, Minimal Change Engineer, Code Reviewer.

## Current Goals

实现独立 GitHub 项目：开盘/尾盘 A 股选股、最新数据检查、Markdown/CSV 输出、GitHub Actions 定时运行、Telegram/企业微信/飞书/Webhook 通知。

## Constraints

不自动下单；不放入用户原始项目；不把 LLM 判断作为第一版硬依赖；所有策略必须可解释、可回测、可替换数据源；数据过期必须失败而不是静默推送。
