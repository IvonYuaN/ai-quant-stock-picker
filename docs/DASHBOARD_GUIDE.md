# 主链看板使用指南

## 定位

`src/aqsp/web/dashboard.py` 现在是 **主链只读看板**，只展示项目已经落盘的确定性数据：

- `data/ledger.jsonl`
- `data/paper_trades.jsonl`
- `logs/trades/*.jsonl`

它 **不是** 券商账户终端，也 **不会** 推断真实账户总资产、实时市值、当日盈亏或策略胜率。

## 启动

```bash
bash scripts/start_dashboard.sh
```

或：

```bash
streamlit run src/aqsp/web/dashboard.py --server.port 8501
```

访问：

```text
http://localhost:8501
```

## 页面内容

看板默认只展示以下真实主链信息：

- 最新信号批次与候选列表
- 最近一次运行的数据源状态
- 当前虚拟持仓
- 虚拟盘事件（open / closed / pending_entry / not_executable）
- 最近 7 天执行日志

如果对应文件不存在，页面会显示空态提示；这代表“没有可展示的运行结果”，不是系统自动补了样例。

## 数据前提

建议先跑主链，再打开看板：

```bash
python -m aqsp run
```

如果还启用了虚拟盘同步，通常也会生成：

- `data/paper_trades.jsonl`
- `reports/paper.md`

## 安全部署

默认建议只在本机访问。

如果必须在服务器上打开：

```bash
streamlit run src/aqsp/web/dashboard.py --server.address 127.0.0.1 --server.port 8501
```

然后通过带鉴权的反向代理暴露，不要直接裸露到公网。

最低要求：

- 反向代理 Basic Auth 或统一登录
- 仅允许可信 IP
- TLS

不推荐直接使用下面这种方式对公网开放：

```bash
streamlit run src/aqsp/web/dashboard.py --server.address 0.0.0.0 --server.port 8501
```

除非前面已经有鉴权和访问控制。
