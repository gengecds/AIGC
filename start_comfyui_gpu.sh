#!/bin/bash
# GPU上启动ComfyUI + 验证HunyuanVideo
set -e
export TQDM_DISABLE=1
cd /root/ComfyUI

# 杀旧ComfyUI
pkill -9 -f 'python main.py' 2>/dev/null || true
sleep 2

# 启动
nohup /root/miniconda3/bin/python main.py --listen 0.0.0.0 --port 8188 --highvram > /tmp/comfyui.log 2>&1 &
COMFY_PID=$!
echo "ComfyUI PID: $COMFY_PID"

# 等就绪
for i in $(seq 1 20); do
    sleep 3
    if curl -s http://localhost:8188/system_stats >/dev/null 2>&1; then
        echo "ComfyUI ready (${i}x3s)"
        break
    fi
done
