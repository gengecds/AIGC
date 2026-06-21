# AIGC 漫剧创作平台 - 后端 Docker 镜像
# 用途：将 FastAPI 后端 + 静态前端 打包为单容器
# 注意：GPU 推理依赖远程 ComfyUI（不在此容器内）

FROM python:3.14-slim AS builder

WORKDIR /app

# 安装系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    curl \
    && rm -rf /var/lib/apt/lists/*

# 安装 Python 依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ---- 第二阶段：运行 ----
FROM python:3.14-slim

WORKDIR /app

# 从 builder 复制 Python 环境
COPY --from=builder /usr/local/lib/python3.14/site-packages /usr/local/lib/python3.14/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
COPY --from=builder /usr/bin/ffmpeg /usr/bin/ffmpeg

# 复制项目代码
COPY . .

# 创建必要目录
RUN mkdir -p storage/checkpoints storage/output storage/videos storage/scripts

# 默认使用 mock 引擎（无需 GPU）
ENV IMAGE_PROVIDER=mock
ENV VIDEO_PROVIDER=mock
ENV LLM_PROVIDER=mock
ENV COMFYUI_ADDR=127.0.0.1
ENV COMFYUI_PORT=18188
ENV DEEPSEEK_API_KEY=""

EXPOSE 8888

# 启动命令
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8888"]
