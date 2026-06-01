# 邮件通道配置（PR22 T3）

## 不要把密码写进任何被 git 追踪的文件

aqsp 邮件通道**只**从环境变量读凭证。

## 设置（一次性）

把以下加到 `~/.zshrc`（不进 git）：

```bash
export AQSP_SMTP_HOST="smtp.qq.com"      # 例：qq 邮箱用 smtp.qq.com
export AQSP_SMTP_PORT="587"
export AQSP_SMTP_USER="你的@qq.com"
export AQSP_SMTP_PASSWORD="授权码"        # qq 邮箱不是登录密码，是授权码
export AQSP_EMAIL_FROM="你的@qq.com"
export AQSP_EMAIL_TO="目标@x.com"         # 多个用逗号分隔
```

`source ~/.zshrc` 后验证：

```bash
aqsp briefing --output /tmp/test_briefing.md --email
```

预期 stdout：`✅ 邮件已发送`

## 各邮箱授权码获取

- QQ 邮箱：设置 → 账户 → POP3/SMTP → 开启 → 生成授权码
- 163 邮箱：同上路径，授权码非邮箱密码
- Gmail：要开两步验证后生成 App Password
- 公司邮箱：找 IT

## 不可调试时降级

如果环境变量不全，`--email` 会打印 `⚠️ ... 跳过邮件发送`，不会抛异常。
