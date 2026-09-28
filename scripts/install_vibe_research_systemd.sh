#!/usr/bin/env bash
# AQSP systemd 服务安装脚本 - 支持前端/后端分离部署
#
# 使用方法：
#   --frontend-only: 仅安装前端服务（React）
#   --backend-only:  仅安装后端服务（FastAPI）
#   --env-file:      环境变量文件路径
#   --no-start:      安装后不自动启动服务

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# 默认参数
FRONTEND_ONLY=false
BACKEND_ONLY=false
NO_START=false
ENV_FILE="${ENV_FILE:-/etc/aqsp/vibe-research.env}"

# 解析命令行参数
while [[ $# -gt 0 ]]; do
    case "$1" in
        --frontend-only)
            FRONTEND_ONLY=true
            shift
            ;;
        --backend-only)
            BACKEND_ONLY=true
            shift
            ;;
        --env-file)
            ENV_FILE="$2"
            shift 2
            ;;
        --no-start)
            NO_START=true
            shift
            ;;
        *)
            echo "未知参数: $1"
            echo "用法: $0 [--frontend-only|--backend-only] [--env-file FILE] [--no-start]"
            exit 1
            ;;
    esac
done

# 检查权限
if [[ $EUID -ne 0 ]]; then
    echo "错误: 此脚本需要 root 权限运行"
    echo "请使用: sudo $0 $*"
    exit 1
fi

# 确定要安装的服务
INSTALL_FRONTEND=true
INSTALL_BACKEND=true

if [[ "$FRONTEND_ONLY" == "true" ]]; then
    INSTALL_BACKEND=false
    echo "模式: 仅安装前端服务"
elif [[ "$BACKEND_ONLY" == "true" ]]; then
    INSTALL_FRONTEND=false
    echo "模式: 仅安装后端服务"
else
    echo "模式: 安装前端和后端服务"
fi

echo "环境变量文件: $ENV_FILE"
echo "项目路径: $PROJECT_ROOT"
echo ""

# 检查必需的文件
check_prerequisites() {
    local missing_files=()

    if [[ "$INSTALL_BACKEND" == "true" ]]; then
        [[ ! -f "$PROJECT_ROOT/backend/app.py" ]] && missing_files+=("backend/app.py")
        [[ ! -d "$PROJECT_ROOT/.venv" ]] && missing_files+=(".venv (Python虚拟环境)")
    fi

    if [[ "$INSTALL_FRONTEND" == "true" ]]; then
        [[ ! -d "$PROJECT_ROOT/frontend/dist" ]] && missing_files+=("frontend/dist (前端构建产物)")
    fi

    if [[ ${#missing_files[@]} -gt 0 ]]; then
        echo "错误: 缺少必需的文件或目录:"
        printf '  - %s\n' "${missing_files[@]}"
        echo ""
        echo "请先完成以下操作:"
        [[ "$INSTALL_BACKEND" == "true" ]] && [[ ! -d "$PROJECT_ROOT/.venv" ]] && echo "  - 创建 Python 虚拟环境: python3 -m venv .venv && .venv/bin/pip install -e '.[data,api]'"
        [[ "$INSTALL_FRONTEND" == "true" ]] && [[ ! -d "$PROJECT_ROOT/frontend/dist" ]] && echo "  - 构建前端: cd frontend && npm install && npm run build"
        exit 1
    fi
}

check_prerequisites

# 创建日志目录
AQSP_LOG_DIR="${PROJECT_ROOT}/logs"
mkdir -p "$AQSP_LOG_DIR"
chown -R "$(stat -c '%U:%G' "$PROJECT_ROOT")" "$AQSP_LOG_DIR" 2>/dev/null || \
    chown -R "$(stat -f '%Su:%Sg' "$PROJECT_ROOT")" "$AQSP_LOG_DIR"

echo "日志目录: $AQSP_LOG_DIR"

# 获取项目属主
AQSP_USER=$(stat -c '%U' "$PROJECT_ROOT" 2>/dev/null || stat -f '%Su' "$PROJECT_ROOT")
AQSP_GROUP=$(stat -c '%G' "$PROJECT_ROOT" 2>/dev/null || stat -f '%Sg' "$PROJECT_ROOT")

echo "运行用户: $AQSP_USER:$AQSP_GROUP"

# 查找 npm 路径
find_npm() {
    local npm_path
    npm_path=$(command -v npm 2>/dev/null || echo "")
    if [[ -z "$npm_path" ]]; then
        # 尝试常见路径
        for path in /usr/local/bin/npm /usr/bin/npm "$HOME/.nvm/versions/node/"*/bin/npm; do
            if [[ -x "$path" ]]; then
                npm_path="$path"
                break
            fi
        done
    fi
    echo "$npm_path"
}

NPM_BIN=$(find_npm)

if [[ "$INSTALL_FRONTEND" == "true" ]] && [[ -z "$NPM_BIN" ]]; then
    echo "错误: 未找到 npm 命令"
    exit 1
fi

[[ -n "$NPM_BIN" ]] && echo "npm 路径: $NPM_BIN"

# 创建环境变量文件目录
ENV_DIR=$(dirname "$ENV_FILE")
mkdir -p "$ENV_DIR"

# 如果环境变量文件不存在，从项目复制
if [[ ! -f "$ENV_FILE" ]] && [[ -f "$PROJECT_ROOT/.env" ]]; then
    echo "复制环境变量文件: $PROJECT_ROOT/.env -> $ENV_FILE"
    cp "$PROJECT_ROOT/.env" "$ENV_FILE"
    chmod 600 "$ENV_FILE"
fi

# ---- 运行时读取路径 ACL 供给 ----
# 服务以 ${AQSP_USER} 运行，必须能读 API 会 serve 的每一个运行时路径（而非只硬编码几个）。
# 2026-09-18 教训：只给快照加 ACL，而 env 里声明的 8 条读取路径全是 600 ⇒
# 选股绩效/讨论/纸面交易/消息面雷达四个功能全部静默降级成空数据，一致性检查却一直报成功。
# 故读取路径**从环境文件派生**（绝对路径型 AQSP_* 变量），新增读取路径只改 env，ACL 自动跟上。
if [[ -f "$ENV_FILE" ]]; then
    set -a
    # shellcheck disable=SC1090
    . "$ENV_FILE"
    set +a

    READ_PATHS=()
    while IFS= read -r var_name; do
        var_value="${!var_name:-}"
        [[ "$var_value" == /* ]] && READ_PATHS+=("$var_value")
    done < <(grep -oE '^AQSP_[A-Z0-9_]+=' "$ENV_FILE" | tr -d '=')

    if ((${#READ_PATHS[@]})); then
        if ! command -v setfacl >/dev/null 2>&1; then
            echo "警告: 缺少 setfacl，运行时 600 文件可能对 ${AQSP_USER} 不可读（ACL 600 陷阱）。" >&2
        else
            for read_path in "${READ_PATHS[@]}"; do
                ancestor="$(dirname "$read_path")"
                while [[ "$ancestor" != "/" && "$ancestor" != "." ]]; do
                    setfacl -m "u:${AQSP_USER}:--x" "$ancestor" 2>/dev/null || true
                    ancestor="$(dirname "$ancestor")"
                done
                parent="$(dirname "$read_path")"
                [[ -d "$parent" ]] && setfacl -d -m "u:${AQSP_USER}:r--" "$parent" 2>/dev/null || true
                [[ -f "$read_path" ]] && setfacl -m "u:${AQSP_USER}:r--" "$read_path" 2>/dev/null || true
            done
            echo "已为 ${AQSP_USER} 补齐 ${#READ_PATHS[@]} 条运行时读取路径的 ACL"

            # 逐条验证：**任何一个读取路径不可读都算 provision 失败**。
            # 只验证两个快照文件是上次漏检的根因，这里必须覆盖全部。
            unreadable=()
            for read_path in "${READ_PATHS[@]}"; do
                [[ -e "$read_path" ]] || continue
                runuser -u "$AQSP_USER" -- test -r "$read_path" || unreadable+=("$read_path")
            done
            if ((${#unreadable[@]})); then
                echo "服务用户 ${AQSP_USER} 仍无法读取以下运行时路径：" >&2
                printf '  %s\n' "${unreadable[@]}" >&2
                echo "请检查: getfacl <路径>" >&2
                exit 1
            fi
        fi
    fi
fi

# 安装后端服务
install_backend_service() {
    echo ""
    echo "=== 安装后端服务 (FastAPI) ==="

    local service_file="/etc/systemd/system/aqsp-vibe-research-api.service"
    local template_file="$PROJECT_ROOT/deploy/systemd/aqsp-vibe-research-api.service"

    # 从模板生成服务文件
    sed -e "s|@AQSP_PROJECT_ROOT@|$PROJECT_ROOT|g" \
        -e "s|@AQSP_VENV_DIR@|$PROJECT_ROOT/.venv|g" \
        -e "s|@AQSP_ENV_FILE@|$ENV_FILE|g" \
        -e "s|@AQSP_LOG_DIR@|$AQSP_LOG_DIR|g" \
        -e "s|@AQSP_VIBE_USER@|$AQSP_USER|g" \
        -e "s|@AQSP_VIBE_GROUP@|$AQSP_GROUP|g" \
        "$template_file" > "$service_file"

    echo "已创建服务文件: $service_file"

    # 重载 systemd
    systemctl daemon-reload
    echo "已重载 systemd 配置"

    # 启用服务
    systemctl enable aqsp-vibe-research-api.service
    echo "已启用后端服务"
}

# 安装前端服务
install_frontend_service() {
    echo ""
    echo "=== 安装前端服务 (React) ==="

    local service_file="/etc/systemd/system/aqsp-vibe-research-preview.service"
    local template_file="$PROJECT_ROOT/deploy/systemd/aqsp-vibe-research-preview.service"

    # 从模板生成服务文件
    sed -e "s|@AQSP_PROJECT_ROOT@|$PROJECT_ROOT|g" \
        -e "s|@AQSP_VENV_DIR@|$PROJECT_ROOT/.venv|g" \
        -e "s|@AQSP_NPM_BIN@|$NPM_BIN|g" \
        -e "s|@AQSP_ENV_FILE@|$ENV_FILE|g" \
        -e "s|@AQSP_LOG_DIR@|$AQSP_LOG_DIR|g" \
        -e "s|@AQSP_VIBE_USER@|$AQSP_USER|g" \
        -e "s|@AQSP_VIBE_GROUP@|$AQSP_GROUP|g" \
        "$template_file" > "$service_file"

    echo "已创建服务文件: $service_file"

    # 重载 systemd
    systemctl daemon-reload
    echo "已重载 systemd 配置"

    # 启用服务
    systemctl enable aqsp-vibe-research-preview.service
    echo "已启用前端服务"
}

# 安装 target（仅在同时安装前后端时）
install_target() {
    if [[ "$INSTALL_FRONTEND" == "true" ]] && [[ "$INSTALL_BACKEND" == "true" ]]; then
        echo ""
        echo "=== 安装 systemd target ==="

        local target_file="/etc/systemd/system/aqsp-vibe-research.target"
        local template_file="$PROJECT_ROOT/deploy/systemd/aqsp-vibe-research.target"

        cp "$template_file" "$target_file"
        echo "已创建 target 文件: $target_file"

        systemctl daemon-reload
        systemctl enable aqsp-vibe-research.target
        echo "已启用 AQSP target"
    fi
}

# 执行安装
[[ "$INSTALL_BACKEND" == "true" ]] && install_backend_service
[[ "$INSTALL_FRONTEND" == "true" ]] && install_frontend_service
install_target

# 启动服务
if [[ "$NO_START" != "true" ]]; then
    echo ""
    echo "=== 启动服务 ==="

    if [[ "$INSTALL_FRONTEND" == "true" ]] && [[ "$INSTALL_BACKEND" == "true" ]]; then
        systemctl start aqsp-vibe-research.target
        echo "已启动 AQSP target"
    else
        [[ "$INSTALL_BACKEND" == "true" ]] && systemctl start aqsp-vibe-research-api.service && echo "已启动后端服务"
        [[ "$INSTALL_FRONTEND" == "true" ]] && systemctl start aqsp-vibe-research-preview.service && echo "已启动前端服务"
    fi

    # 等待服务启动
    echo "等待服务启动..."
    sleep 3

    # 检查服务状态
    echo ""
    echo "=== 服务状态 ==="
    [[ "$INSTALL_BACKEND" == "true" ]] && systemctl status aqsp-vibe-research-api.service --no-pager || true
    [[ "$INSTALL_FRONTEND" == "true" ]] && systemctl status aqsp-vibe-research-preview.service --no-pager || true
else
    echo ""
    echo "=== 服务已安装但未启动 ==="
    echo "启动服务命令:"
    if [[ "$INSTALL_FRONTEND" == "true" ]] && [[ "$INSTALL_BACKEND" == "true" ]]; then
        echo "  sudo systemctl start aqsp-vibe-research.target"
    else
        [[ "$INSTALL_BACKEND" == "true" ]] && echo "  sudo systemctl start aqsp-vibe-research-api.service"
        [[ "$INSTALL_FRONTEND" == "true" ]] && echo "  sudo systemctl start aqsp-vibe-research-preview.service"
    fi
fi

echo ""
echo "=== 安装完成 ==="
echo ""
echo "常用命令:"
[[ "$INSTALL_BACKEND" == "true" ]] && cat <<EOF
  后端服务:
    启动: sudo systemctl start aqsp-vibe-research-api.service
    停止: sudo systemctl stop aqsp-vibe-research-api.service
    重启: sudo systemctl restart aqsp-vibe-research-api.service
    状态: sudo systemctl status aqsp-vibe-research-api.service
    日志: sudo journalctl -u aqsp-vibe-research-api.service -f

EOF

[[ "$INSTALL_FRONTEND" == "true" ]] && cat <<EOF
  前端服务:
    启动: sudo systemctl start aqsp-vibe-research-preview.service
    停止: sudo systemctl stop aqsp-vibe-research-preview.service
    重启: sudo systemctl restart aqsp-vibe-research-preview.service
    状态: sudo systemctl status aqsp-vibe-research-preview.service
    日志: sudo journalctl -u aqsp-vibe-research-preview.service -f

EOF

if [[ "$INSTALL_FRONTEND" == "true" ]] && [[ "$INSTALL_BACKEND" == "true" ]]; then
    cat <<EOF
  整体管理:
    启动: sudo systemctl start aqsp-vibe-research.target
    停止: sudo systemctl stop aqsp-vibe-research.target
    重启: sudo systemctl restart aqsp-vibe-research.target
    状态: sudo systemctl status aqsp-vibe-research.target

EOF
fi

echo "健康检查:"
[[ "$INSTALL_BACKEND" == "true" ]] && echo "  后端: curl http://127.0.0.1:8900/api/health"
[[ "$INSTALL_FRONTEND" == "true" ]] && echo "  前端: curl http://127.0.0.1:5899/"
