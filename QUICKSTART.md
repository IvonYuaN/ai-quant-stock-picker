# AI 量化选股 - 10分钟快速上手指南

本地优先的 A 股量化筛选工作台，可选 GitHub 自动化。

## 🚀 快速启动（3 步）

### 1. 克隆项目（如已克隆可跳过）

```bash
git clone <your-repo-url>
cd AI量化选股
```

### 2. 安装依赖

```bash
make install
```

这将自动安装：
- Python 依赖（FastAPI + 数据源 + 测试工具）
- Node.js 依赖（React + Vite + TypeScript）

**环境要求：**
- Python >= 3.10
- Node.js >= 18

### 3. 启动开发环境

```bash
make dev
```

自动完成：
- ✓ 检查 Python 和 Node.js 版本
- ✓ 检测并安装缺失的依赖
- ✓ 启动 FastAPI 后端（127.0.0.1:8900）
- ✓ 启动 Vite 前端（127.0.0.1:5899）
- ✓ 健康检查和错误提示

**访问应用：**
- 前端：http://127.0.0.1:5899
- API 文档：http://127.0.0.1:8900/docs

按 `Ctrl+C` 停止所有服务。

---

## 📦 项目结构

```
AI量化选股/
├── backend/           # FastAPI 后端（A股数据层 HTTP 接口）
│   ├── app.py        # 主应用入口
│   └── tests/        # 后端测试
├── frontend/         # React + Vite 前端
│   ├── src/          # 源代码
│   └── package.json  # Node.js 依赖
├── scripts/          # 辅助脚本
│   └── dev.sh        # 开发环境启动脚本
├── tests/            # 集成测试
├── Makefile          # 开发工作流快捷命令
├── pyproject.toml    # Python 项目配置
└── QUICKSTART.md     # 本文件
```

---

## 🛠️ 常用命令

### 开发命令

```bash
make dev        # 启动开发环境（推荐）
make test       # 运行所有测试
make lint       # 代码检查（Ruff + TypeScript）
make clean      # 清理临时文件
```

### 手动启动（高级）

如果需要分别启动前后端：

**后端（FastAPI）：**
```bash
python3 -m uvicorn backend.app:app --host 127.0.0.1 --port 8900 --reload
```

**前端（Vite）：**
```bash
cd frontend
npm run dev
```

---

## 🧪 测试

### 运行所有测试

```bash
make test
```

### 单独运行后端测试

```bash
pytest                    # 跳过联网测试（默认）
pytest -m live            # 仅运行联网测试（需要网络）
pytest tests/specific_test.py  # 运行特定测试
```

### 单独运行前端测试

```bash
cd frontend
npm test                  # 类型检查 + 契约测试
```

---

## 🔍 代码检查

### 运行所有检查

```bash
make lint
```

### 单独运行 Python 检查

```bash
ruff check .              # 检查代码风格
ruff check . --fix        # 自动修复
```

### 单独运行前端检查

```bash
cd frontend
npx tsc --noEmit          # TypeScript 类型检查
```

---

## 🧹 清理

```bash
make clean
```

清理内容：
- Python 缓存（`__pycache__`, `.pytest_cache`, `.ruff_cache`）
- 编译文件（`*.pyc`, `*.pyo`）
- 测试临时文件（`.pytest_run`, `.pytest_tmp`）
- 前端构建缓存（`dist`, `.vite`）

---

## ⚙️ 配置

### 环境变量（可选）

复制示例配置：

```bash
cp .env.example .env
```

常用配置：
- `VR_API_KEY`: API 密钥（公网模式需要）
- `VR_PUBLIC_MODE`: 公网模式开关（1/true/yes/on）
- `VITE_API_URL`: 前端 API 代理地址（默认 http://127.0.0.1:8900）

**本地开发无需配置**，默认即可运行。

---

## 🐛 常见问题

### 1. 端口被占用

**症状：**启动时提示 `端口 8900 或 5899 已被占用`

**解决：**
```bash
# 查找占用进程
lsof -i :8900
lsof -i :5899

# 终止进程
kill -9 <PID>
```

或者在 `make dev` 启动时选择 `y` 自动终止占用进程。

### 2. Python 依赖安装失败

**症状：**`pip install` 报错

**解决：**
```bash
# 升级 pip
python3 -m pip install --upgrade pip

# 使用国内镜像（可选）
pip install -e ".[api,dev,data]" -i https://pypi.tuna.tsinghua.edu.cn/simple
```

### 3. Node.js 依赖安装失败

**症状：**`npm install` 报错

**解决：**
```bash
cd frontend

# 清理缓存
rm -rf node_modules package-lock.json
npm cache clean --force

# 重新安装
npm install

# 或使用国内镜像
npm install --registry=https://registry.npmmirror.com
```

### 4. 后端启动失败

**症状：**后端服务无法启动

**解决：**
```bash
# 查看详细日志
tail -f /tmp/aqsp_backend_*.log

# 检查依赖
python3 -c "import fastapi, uvicorn"

# 重新安装
pip install -e ".[api,dev,data]" --force-reinstall
```

### 5. 前端启动失败

**症状：**前端服务无法启动

**解决：**
```bash
# 查看详细日志
tail -f /tmp/aqsp_frontend_*.log

# 检查 Node.js 版本
node --version  # 需要 >= 18

# 清理并重建
cd frontend
rm -rf node_modules dist
npm install
```

---

## 📚 更多文档

- [项目 README](./README.md) - 完整项目说明
- [后端 API 文档](http://127.0.0.1:8900/docs) - FastAPI 自动生成（启动后访问）
- [前端开发文档](./frontend/README.md) - 前端架构和组件说明
- [部署指南](./docs/deployment.md) - 生产环境部署

---

## 🆘 获取帮助

```bash
make help  # 查看所有可用命令
```

遇到问题？
1. 查看本文档的「常见问题」
2. 检查项目 Issues
3. 提交新 Issue 并附上错误日志

---

**祝开发顺利！** 🎉
