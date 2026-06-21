#!/bin/bash
# AutoDL GPU 一键启动 ComfyUI
# 开机后运行: bash start_comfyui.sh

REMOTE="root@connect.bjb2.seetacloud.com"
PORT=30476
PASS="900917_19871002-Gz"

echo "=== 启动 ComfyUI ==="
sshpass -p "$PASS" ssh -o StrictHostKeyChecking=no -p "$PORT" "$REMOTE" "
cd /root/ComfyUI
pkill -f 'python main.py' 2>/dev/null
sleep 2
/root/miniconda3/bin/python main.py --listen 0.0.0.0 --port 8188 </dev/null >/dev/null 2>&1 &
disown
echo 'ComfyUI launched'
" 2>&1

echo ""
echo "=== 等待就绪 ==="
for i in $(seq 1 20); do
  sleep 3
  if sshpass -p "$PASS" ssh -o StrictHostKeyChecking=no -p "$PORT" "$REMOTE" "curl -s http://localhost:8188/system_stats >/dev/null 2>&1" 2>/dev/null; then
    echo "Ready! ComfyUI at http://localhost:8188"
    break
  fi
  echo "  waiting... \${i}s"
done
