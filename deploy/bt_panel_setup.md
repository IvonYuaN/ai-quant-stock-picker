# 宝塔面板配置指南

本文档指导在宝塔面板上配置 AI量化选股项目的定时任务、监控告警和日志管理。

---

## 1. 前置条件

- 已运行 `deploy/setup.sh` 完成基础部署
- 宝塔面板 9.x 已安装并可访问
- 服务器已开放面板端口（默认 8888）

---

## 2. 安装 Python 项目管理器（可选）

宝塔面板的 Python 项目管理器可以图形化管理 Python 项目，但非必须。

### 2.1 安装步骤

1. 登录宝塔面板
2. 进入 **软件商店** → 搜索 **Python项目管理器**
3. 点击 **安装**，选择版本（推荐 2.x）
4. 安装完成后刷新页面

### 2.2 配置项目

1. 进入 **Python项目管理器**
2. 点击 **添加项目**
3. 填写配置：

| 配置项 | 值 |
|--------|-----|
| 项目名称 | `aqsp` |
| 项目路径 | `/opt/aqsp` |
| Python版本 | 系统 Python 3.10+ |
| 启动方式 | `python -m aqsp run` |
| 端口 | 留空（非 Web 服务） |
| 虚拟环境 | `/opt/aqsp/.venv` |

4. 点击 **确定** 保存

> **注意**: 本项目是命令行工具而非 Web 服务，Python 项目管理器主要用于查看运行状态。定时任务建议通过宝塔的 **计划任务** 功能配置。

---

## 3. 配置定时任务（推荐方式）

### 3.1 通过宝塔面板配置

1. 登录宝塔面板
2. 进入 **计划任务**
3. 点击 **添加任务**
4. 配置如下：

| 配置项 | 值 |
|--------|-----|
| 任务类型 | **Shell脚本** |
| 任务名称 | `aqsp-每日选股` |
| 执行周期 | **每天** |
| 执行时间 | `02:00`（凌晨2点，北京时间） |
| 脚本内容 | 见下方 |

**脚本内容**:

```bash
#!/bin/bash
cd /opt/aqsp
source .venv/bin/activate
bash scripts/daily_pipeline.sh
```

5. 点击 **添加任务**

### 3.2 手动配置 crontab

如果不用宝塔面板，直接编辑 crontab：

```bash
crontab -e
```

添加以下行（周一至周五凌晨 2:00 北京时间）：

```
0 2 * * 1-5 /bin/bash /opt/aqsp/scripts/daily_pipeline.sh >> /opt/aqsp/logs/cron.log 2>&1
```

> **说明**: 为什么是周一至周五？A 股市场周末休市，无需运行选股策略。

### 3.3 验证定时任务

```bash
# 查看当前定时任务
crontab -l

# 手动触发一次测试
bash /opt/aqsp/scripts/daily_pipeline.sh --dry-run
```

---

## 4. 配置监控告警

### 4.1 企业微信通知

1. 在企业微信群中添加 **群机器人**
2. 获取 Webhook URL
3. 编辑 `/opt/aqsp/.env`，填入：

```bash
WECHAT_WEBHOOK_URL=https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=YOUR_KEY
```

### 4.2 飞书通知

1. 在飞书群中添加 **自定义机器人**
2. 获取 Webhook URL
3. 编辑 `/opt/aqsp/.env`：

```bash
FEISHU_WEBHOOK_URL=https://open.feishu.cn/open-apis/bot/v2/hook/YOUR_TOKEN
```

### 4.3 Telegram 通知

1. 通过 @BotFather 创建 Bot，获取 Token
2. 获取 Chat ID（可通过 @userinfobot）
3. 编辑 `/opt/aqsp/.env`：

```bash
TELEGRAM_BOT_TOKEN=YOUR_BOT_TOKEN
TELEGRAM_CHAT_ID=YOUR_CHAT_ID
```

### 4.4 邮件通知

编辑 `/opt/aqsp/.env`：

```bash
AQSP_SMTP_HOST=smtp.qq.com
AQSP_SMTP_PORT=465
AQSP_SMTP_USER=your_email@qq.com
AQSP_SMTP_PASSWORD=your_smtp_password
AQSP_SMTP_FROM=your_email@qq.com
AQSP_SMTP_TO=receiver@example.com
```

> **注意**: QQ 邮箱需要使用授权码而非登录密码。

### 4.5 钉钉通知（通过通用 Webhook）

1. 在钉钉群中添加 **自定义机器人**
2. 获取 Webhook URL
3. 编辑 `/opt/aqsp/.env`：

```bash
GENERIC_WEBHOOK_URL=https://oapi.dingtalk.com/robot/send?access_token=YOUR_TOKEN
```

---

## 5. 宝塔面板监控配置

### 5.1 进程监控

1. 进入 **监控** → **进程管理**
2. 添加监控规则：

| 配置项 | 值 |
|--------|-----|
| 进程名 | `python3` |
| 关键词 | `aqsp` |
| 告警方式 | 选择已配置的通知渠道 |

### 5.2 日志监控

1. 进入 **文件** → 导航到 `/opt/aqsp/logs/`
2. 可以在面板中直接查看日志文件
3. 建议定期检查 `logs/error/` 目录中的错误日志

### 5.3 定时任务日志

在宝塔面板 **计划任务** 页面：
1. 点击任务右侧的 **日志** 按钮
2. 可查看每次执行的输出和状态

---

## 6. 常见问题排查

### Q1: 定时任务没有执行

**排查步骤**:

```bash
# 1. 检查 crontab 是否正确
crontab -l

# 2. 检查 cron 服务是否运行
systemctl status cron

# 3. 检查日志
tail -50 /opt/aqsp/logs/cron.log

# 4. 手动运行测试
cd /opt/aqsp && source .venv/bin/activate && python -m aqsp run --dry-run
```

### Q2: Python 虚拟环境报错

```bash
# 重新创建虚拟环境
cd /opt/aqsp
rm -rf .venv
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[data,dev]"
```

### Q3: 数据源获取失败

```bash
# 检查网络连通性
curl -s https://push2his.eastmoney.com/ > /dev/null && echo "东方财富 OK" || echo "东方财富 FAIL"
curl -s https://hq.sinajs.cn/ > /dev/null && echo "新浪 OK" || echo "新浪 FAIL"

# 检查数据源健康状态
cd /opt/aqsp && source .venv/bin/activate
python -m aqsp sources
```

### Q4: 内存不足（2G 服务器）

```bash
# 创建 2G swap 文件
sudo fallocate -l 2G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile

# 永久生效
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab

# 验证
free -h
```

### Q5: 时区问题

```bash
# 检查时区
timedatectl

# 设置为上海时区
sudo timedatectl set-timezone Asia/Shanghai

# 验证
date
```

### Q6: git pull 更新代码失败

```bash
cd /opt/aqsp

# 丢弃本地修改
git checkout -- .

# 拉取最新代码
git pull origin main

# 重新安装依赖
source .venv/bin/activate
pip install -e ".[data,dev]"
```

### Q7: 日志文件过大

```bash
# 查看日志大小
du -sh /opt/aqsp/logs/*

# 清理 30 天前的日志
find /opt/aqsp/logs -name "*.log" -mtime +30 -delete

# 建议配置 logrotate
cat > /etc/logrotate.d/aqsp << 'EOF'
/opt/aqsp/logs/*.log {
    daily
    rotate 30
    compress
    delaycompress
    missingok
    notifempty
}
EOF
```

---

## 7. 日常运维命令

```bash
# 进入项目目录
cd /opt/aqsp
source .venv/bin/activate

# 手动运行选股
python -m aqsp run

# 查看数据源状态
python -m aqsp sources

# 生成每日简报
python -m aqsp briefing

# 查看运行监控
python -m aqsp monitor

# 更新代码
git pull origin main
pip install -e ".[data,dev]"

# 查看最新报告
cat reports/latest.md

# 查看今日日志
tail -100 logs/daily/$(date +%Y-%m-%d).log
```

---

## 8. 备份建议

建议定期备份以下目录：

```bash
# 数据文件（最重要）
/opt/aqsp/data/

# 配置文件
/opt/aqsp/.env
/opt/aqsp/config/

# 报告
/opt/aqsp/reports/
```

可以使用宝塔面板的 **计划任务** → **备份网站** 功能，或手动配置 rsync。

---

## 9. 本地运行 + 服务器展示方案（推荐）

### 9.1 架构说明

**推荐方案**: 本地电脑运行策略，服务器只托管 Dashboard 前端。

```
┌─────────────────────────────────────────────────────────┐
│  本地电脑 (你的Mac)                                      │
│  ├─ 每天15:15 launchd触发                                │
│  ├─ 运行选股策略 (需要大量数据+算力)                       │
│  ├─ 生成 dashboard.html                                  │
│  └─ SCP自动上传到服务器                                   │
└─────────────────────────────────────────────────────────┘
                         │
                         ▼ SCP/SSH
┌─────────────────────────────────────────────────────────┐
│  宝塔服务器 (2核2G)                                       │
│  ├─ Nginx静态托管 dashboard.html                         │
│  ├─ 你通过浏览器远程访问                                   │
│  └─ 不存储大量数据，只做展示                               │
└─────────────────────────────────────────────────────────┘
```

**优点**:
- 服务器不需跑策略，2核2G完全够用
- 数据留在本地，不占服务器空间
- 利用本地已有的 launchd 自动化

### 9.2 本地配置

在本地 `.env` 文件中添加：

```bash
# Dashboard 部署配置
AQSP_DEPLOY_DASHBOARD=true
AQSP_DEPLOY_HOST=你的服务器IP
AQSP_DEPLOY_PORT=22
AQSP_DEPLOY_USER=root
AQSP_DEPLOY_PATH=/www/wwwroot/dashboard/aqsp
AQSP_DEPLOY_SSH_KEY_PATH=~/.ssh/id_rsa
```

### 9.3 服务器 Nginx 配置

在宝塔面板中配置 Nginx 静态托管：

1. 登录宝塔面板
2. 进入 **网站** → **添加站点**
3. 配置如下：

| 配置项 | 值 |
|--------|-----|
| 域名 | `dashboard.yourdomain.com` 或 `服务器IP` |
| 根目录 | `/www/wwwroot/dashboard/aqsp` |
| PHP版本 | 纯静态 |

4. 点击 **提交** 保存

5. 进入站点 **设置** → **配置文件**，添加以下 Nginx 配置：

```nginx
server {
    listen 80;
    server_name dashboard.yourdomain.com;  # 替换为你的域名或IP

    root /www/wwwroot/dashboard/aqsp;
    index index.html;

    # 启用 gzip 压缩
    gzip on;
    gzip_types text/html text/css application/javascript;
    gzip_min_length 1000;

    # 缓存静态资源
    location ~* \.(css|js|png|jpg|jpeg|gif|ico|svg)$ {
        expires 7d;
        add_header Cache-Control "public, immutable";
    }

    # 禁止访问隐藏文件
    location ~ /\. {
        deny all;
    }

    # 允许跨域（如果需要）
    add_header Access-Control-Allow-Origin *;

    location / {
        try_files $uri $uri/ /index.html;
    }
}
```

6. 点击 **保存** 并重启 Nginx

### 9.4 配置 HTTPS（可选但推荐）

1. 在宝塔面板站点设置中，点击 **SSL**
2. 选择 **Let's Encrypt** 免费证书
3. 勾选 **强制HTTPS**
4. 点击 **申请** 并等待生效

### 9.5 访问 Dashboard

配置完成后，通过以下地址访问：

- HTTP: `http://dashboard.yourdomain.com`
- HTTPS: `https://dashboard.yourdomain.com`
- 或直接: `http://服务器IP`

### 9.6 自动更新流程

本地 launchd 每天15:15自动执行：

```bash
# 1. 运行选股策略
python -m aqsp run

# 2. 生成 Dashboard
python scripts/render_dashboard.py

# 3. 自动部署到服务器（已在 daily_pipeline.sh 中集成）
bash scripts/deploy_dashboard.sh
```

服务器上的 Dashboard 会自动更新，你只需打开浏览器访问即可。

### 9.7 数据存储建议

本地数据增长情况及管理策略：

| 数据类型 | 大小 | 增长速度 | 管理策略 |
|---------|------|---------|---------|
| 历史数据库 | 500MB-2GB | 慢 | 保留近3年数据 |
| 通达信数据 | 200MB-1GB | 慢 | 定期清理旧文件 |
| SQLite缓存 | 10-100MB | 中等 | 自动7天过期 |
| JSONL记录 | 几KB/天 | 很慢 | 每月归档 |

**自动清理配置**（已在 `daily_pipeline.sh` 中集成）：

```bash
# 每周日凌晨自动执行数据清理
# 清理过期缓存、归档旧数据、删除旧日志
```

手动清理命令：

```bash
# 清理 90 天前的通达信数据
find private_data/tdx/vipdoc/ -mtime +90 -delete

# 归档旧的 predictions 文件
mv data/predictions.jsonl data/archive/predictions_$(date +%Y%m).jsonl

# 清理旧日志
find logs/ -name "*.log" -mtime +30 -delete
```

### 9.8 远程备份（可选）

如果需要将本地数据备份到服务器：

```bash
# 在本地 .env 中添加
AQSP_BACKUP_ENABLED=true
AQSP_BACKUP_HOST=你的服务器IP
AQSP_BACKUP_PATH=/www/backup/aqsp

# 备份脚本会自动同步以下目录：
# - data/ (预测记录、快照)
# - config/ (配置文件)
# - reports/ (报告)
```
