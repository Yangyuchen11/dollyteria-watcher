# 基础镜像: 官方精简版 Python 3.11
FROM python:3.11-slim

# 设置工作目录
WORKDIR /app

# 设置环境变量:
# 1. 禁用字节码编译与启用实时标准输出流 (方便在 Zeabur 控制台查看实时检测日志)
# 2. 默认监听 0.0.0.0 与 5000 端口 (Zeabur 会自动根据环境变量 PORT 动态路由)
# 3. 强制时区为 Asia/Tokyo (与 Dollyteria 官网 20:00/21:00 定点上新时刻精准对齐)
# 4. 指定持久化存储数据目录为 /app/data
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOST=0.0.0.0 \
    PORT=5000 \
    NO_BROWSER=1 \
    TZ=Asia/Tokyo \
    DATA_DIR=/app/data

# 安装基础系统依赖及时区数据 (用于设置 JST 日本时区)
RUN apt-get update && apt-get install -y --no-install-recommends \
    tzdata \
    curl \
    && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone \
    && rm -rf /var/lib/apt/lists/*

# 利用 Docker 层缓存机制: 先安装 Python 依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制整个项目源代码
COPY . .

# 确保持久化数据目录与初始 state 存在
RUN mkdir -p /app/data

# 暴露端口 (默认 5000)
EXPOSE 5000

# 健康检查 (方便容器平台自动检测服务存活健康状态)
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://127.0.0.1:${PORT:-5000}/api/status || exit 1

# 容器启动命令
CMD ["python", "main.py", "--host", "0.0.0.0", "--no-browser"]
