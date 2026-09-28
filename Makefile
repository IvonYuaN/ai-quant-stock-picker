.PHONY: help install dev test lint clean

help:
	@echo "AI量化选股 - 开发工具"
	@echo ""
	@echo "可用命令："
	@echo "  make install    - 安装所有依赖（Python + Node.js）"
	@echo "  make dev        - 启动开发环境（后端 8900 + 前端 5899）"
	@echo "  make test       - 运行测试"
	@echo "  make lint       - 代码检查"
	@echo "  make clean      - 清理临时文件"
	@echo ""

install:
	@echo "📦 安装 Python 依赖..."
	@pip install -e ".[api,dev,data]"
	@echo ""
	@echo "📦 安装 Node.js 依赖..."
	@cd frontend && npm install
	@echo ""
	@echo "✅ 依赖安装完成！"

dev:
	@bash scripts/dev.sh

test:
	@echo "🧪 运行后端测试..."
	@pytest
	@echo ""
	@echo "🧪 运行前端类型检查和契约测试..."
	@cd frontend && npm test
	@echo ""
	@echo "✅ 所有测试通过！"

lint:
	@echo "🔍 运行 Python 代码检查（Ruff）..."
	@ruff check .
	@echo ""
	@echo "🔍 运行前端类型检查..."
	@cd frontend && npx tsc --noEmit
	@echo ""
	@echo "✅ 代码检查完成！"

clean:
	@echo "🧹 清理临时文件..."
	@find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	@find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
	@find . -type d -name ".ruff_cache" -exec rm -rf {} + 2>/dev/null || true
	@find . -type f -name "*.pyc" -delete 2>/dev/null || true
	@find . -type f -name "*.pyo" -delete 2>/dev/null || true
	@rm -rf .pytest_run .pytest_tmp 2>/dev/null || true
	@rm -rf frontend/dist frontend/node_modules/.vite 2>/dev/null || true
	@echo "✅ 清理完成！"
