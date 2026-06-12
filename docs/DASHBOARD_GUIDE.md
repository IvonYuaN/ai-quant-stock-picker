# 研究工作台使用指南

## 定位

`src/aqsp/web/dashboard.py` 现在是 **按日期与任务回看的研究工作台**，只展示项目已经落盘的确定性数据：

- `data/ledger.jsonl`
- `data/paper_trades.jsonl`
- `logs/trades/*.jsonl`
- `reports/*.md`

它 **不是** 券商账户终端，也 **不会** 推断真实账户总资产、实时市值、当日盈亏或策略胜率。

## 启动

```bash
bash scripts/start_dashboard.sh
```

或：

```bash
streamlit run src/aqsp/web/dashboard.py --server.port 8501
```

本机访问：

```text
http://localhost:8501
```

## 页面内容

当前页面不是单一候选表，而是由几块职责明确的区域组成：

- 顶部日期导航
  - 先按日期回看，再切换当天的 `盘前主链 / 早盘观察 / 尾盘确认 / 收盘复盘 / 次日预案`
- 决策首页
  - 首屏驾驶舱
  - 今日决策流
  - 同日流程导航
  - 虚拟盘状态
- 候选复盘
  - 单候选深挖
  - 当日候选路径
  - 同日联动与执行证据
- 虚拟盘执行
  - 按标的聚焦的执行工作台
- 归档回看
  - 同日归档中心
  - 执行摘要 / 明日重点 / 运行快照
  - 原始 markdown 正文

如果对应文件不存在，页面会显示空态提示；这代表“没有可展示的运行结果”，不是系统自动补了样例。

## 数据前提

建议先跑主链，再打开看板：

```bash
python -m aqsp run
```

如果还启用了虚拟盘同步，通常也会生成：

- `data/paper_trades.jsonl`
- `reports/paper.md`

如果还产出了简报与复盘，则研究工作台会额外读取：

- `reports/latest.md`
- `reports/briefing-YYYY-MM-DD.md`

## 推荐部署

现在有已备案域名时，推荐默认走：

- `dashboard.yourdomain.com` 这类二级域名
- Nginx / Caddy 反向代理
- HTTPS
- 统一登录或 Basic Auth

Streamlit 建议只监听本机回环地址，再由反向代理对外提供服务：

```bash
streamlit run src/aqsp/web/dashboard.py --server.address 127.0.0.1 --server.port 8501
```

然后把域名指向你的服务器，并在反向代理里转发到 `127.0.0.1:8501`。

最低要求：

- HTTPS
- 反向代理 Basic Auth 或统一登录
- 仅允许可信 IP 或额外 WAF / CDN 访问控制

Nginx 示例：

```nginx
server {
    listen 80;
    server_name dashboard.yourdomain.com;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl http2;
    server_name dashboard.yourdomain.com;

    ssl_certificate     /path/to/fullchain.pem;
    ssl_certificate_key /path/to/privkey.pem;

    auth_basic "AQSP Dashboard";
    auth_basic_user_file /etc/nginx/.htpasswd;

    location / {
        proxy_pass http://127.0.0.1:8501;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

不要把 Streamlit 端口直接暴露到公网。公网入口只保留反向代理层，应用进程继续监听 `127.0.0.1:8501`；如果要托管静态面板，发布 `dist/dashboard/` 到受保护的 Web 目录。
