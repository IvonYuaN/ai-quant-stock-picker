# Secret and Upload Policy

本项目可以上传 GitHub，但只能上传代码、文档、测试、公开项目元数据和可复现配置。

## 绝对不能上传

- `.env`
- `GITHUB_TOKEN`
- `GITEE_TOKEN`
- `TUSHARE_TOKEN`
- 通知 webhook、Telegram token、邮箱密码
- `data/cache.db`
- `data/predictions.jsonl`
- `data/weight_history.jsonl`
- `A股量化分析数据/`
- `private_data/`
- `*.db` 私有行情库和本地缓存
- `logs/`
- `reports/*.md`、`reports/*.csv`

## 可以上传

- `data/open_source_research.jsonl`
- `docs/open_source_quant_research.md`
- `docs/research_absorption.md`
- `docs/research_absorption.json`

这些文件只保存公开仓库元数据和人工审阅队列，例如仓库 URL、stars、更新时间、描述、分类和入库门槛，不保存账户 token、私有数据或交易账本。

## 本地使用 token

推荐写入本地 `.env`，该文件已被 `.gitignore` 忽略：

```bash
GITHUB_TOKEN=...
GITEE_TOKEN=...
TUSHARE_TOKEN=...
```

## 私有大数据文件

2G 级别本地行情库不要上传 GitHub。私有 SQLite 行情库应作为外部数据源挂载:

```bash
export AQSP_SQLITE_DB_PATH="/absolute/path/to/private-market-data/astocks_qfq.db"
```

服务器部署时把数据库放到服务器本地磁盘,再在 systemd、cron 或 GitHub Actions SSH 目标环境中配置同名环境变量。

注意: 当前 `astocks_qfq.db` 表为 `daily_qfq`,价格是前复权口径。它可以用于展示、研究扫描和候选生成,但不应作为 ledger/真实 walk-forward 的最终成交价来源；真实验证仍需不复权价格或 point-in-time 复权因子。

临时运行也可以用环境变量：

```bash
export GITHUB_TOKEN="..."
export GITEE_TOKEN="..."
export TUSHARE_TOKEN="..."
```

## GitHub Actions 使用 token

在仓库 `Settings -> Secrets and variables -> Actions -> Secrets` 中配置：

- `GITHUB_TOKEN`
- `GITEE_TOKEN`
- `TUSHARE_TOKEN`

不要写入 `Variables`，不要写入 YAML。

## 上传前检查

```bash
python3 scripts/check_no_secrets.py
python3 -m scripts.preflight_upload
git status --short --ignored
git diff --cached --name-only
```

如果 secret 扫描失败，不允许提交。
