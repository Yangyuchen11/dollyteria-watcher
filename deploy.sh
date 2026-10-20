#!/bin/bash
# ==============================================================================
# Dollyteria 上新提醒小工具 - 腾讯云 Lighthouse 一键部署与管理脚本
# ==============================================================================

set -e

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$APP_DIR"

# 检查 Docker 运行环境
check_docker() {
    if ! command -v docker &> /dev/null; then
        echo "❌ 未检测到 Docker，请先安装 Docker！"
        echo "💡 Ubuntu/Debian 快捷安装命令: curl -fsSL https://get.docker.com | bash"
        exit 1
    fi
}

# 检查 Docker Compose
get_compose_cmd() {
    if docker compose version &> /dev/null; then
        echo "docker compose"
    elif command -v docker-compose &> /dev/null; then
        echo "docker-compose"
    else
        echo "❌ 未检测到 Docker Compose，请先安装！" >&2
        exit 1
    fi
}

action="${1:-start}"
COMPOSE_CMD=$(get_compose_cmd)

case "$action" in
    start|up)
        check_docker
        echo "🚀 正在启动 Dollyteria 上新监控服务..."
        mkdir -p "$APP_DIR/data"
        $COMPOSE_CMD up -d --build
        echo ""
        echo "✅ 服务已在后台成功启动！"
        echo "🌐 Web 看板访问地址: http://<您的服务器公网IP>:5000"
        echo "💡 提示: 请确保已在腾讯云 Lighthouse 控制台【防火墙】中开放 TCP 5000 端口！"
        ;;
    stop|down)
        echo "🛑 正在停止 Dollyteria 服务..."
        $COMPOSE_CMD down
        echo "✅ 服务已停止。"
        ;;
    restart)
        echo "🔄 正在重启服务..."
        $COMPOSE_CMD restart
        echo "✅ 重启成功。"
        ;;
    logs)
        echo "📜 正在输出实时运行日志 (按 Ctrl + C 退出)..."
        $COMPOSE_CMD logs -f --tail=100
        ;;
    status)
        echo "📊 当前容器运行状态:"
        $COMPOSE_CMD ps
        ;;
    update)
        echo "📥 正在拉取最新代码并热更新部署..."
        git pull || true
        $COMPOSE_CMD up -d --build
        echo "✅ 代码已更新并完成重新构建！"
        ;;
    *)
        echo "使用方法: bash deploy.sh [start|stop|restart|logs|status|update]"
        echo "  - start   : 构建并启动服务 (默认)"
        echo "  - stop    : 停止服务"
        echo "  - restart : 重启服务"
        echo "  - logs    : 查看实时运行日志"
        echo "  - status  : 查看容器运行状态"
        echo "  - update  : 拉取最新代码并重新部署"
        ;;
esac
