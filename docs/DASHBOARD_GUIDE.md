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

本机访问：

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

不推荐直接使用下面这种方式对公网开放：

```bash
streamlit run src/aqsp/web/dashboard.py --server.address 0.0.0.0 --server.port 8501
```

除非前面已经有鉴权和访问控制。
