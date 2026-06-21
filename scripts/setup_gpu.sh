#!/bin/bash
# GPU 服务器一键部署脚本
# 用法：在 AutoDL Jupyter 终端执行
# wget -qO- https://your-url/setup_gpu.sh | bash

set -e

echo "=== Step 1: 检查 ComfyUI ==="
if [ ! -d /root/ComfyUI ]; then
    echo "ComfyUI 未安装，请使用 AutoDL 社区镜像：ComfyUI-HunyuanVideo"
    exit 1
fi

echo "=== Step 2: 安装自定义节点 ==="
cd /root/ComfyUI/custom_nodes

# 必备节点列表
NODES=(
    "https://github.com/ltdrdata/ComfyUI-Manager.git"
    "https://github.com/Kosinkadink/ComfyUI-VideoHelperSuite.git"
    "https://github.com/kijai/ComfyUI-HunyuanVideoWrapper.git"
    "https://github.com/cubiq/ComfyUI_IPAdapter_plus.git"
    "https://github.com/Fannovel16/comfyui_controlnet_aux.git"
    "https://github.com/1201ForU/ComfyUI-Custom-Scripts.git"
    "https://github.com/AIGODLIKE/AIGODLIKE-COMFYUI-TRANSLATION.git"
    "https://github.com/rgthree/rgthree-comfy.git"
    "https://github.com/WASasquatch/was-node-suite-comfyui.git"
)

for url in "${NODES[@]}"; do
    name=$(basename "$url" .git)
    if [ ! -d "$name" ]; then
        echo "安装节点: $name"
        git clone --depth=1 "$url" 2>/dev/null || echo "跳过 $name (可能已存在)"
    fi
done

echo "=== Step 3: 下载 SD 模型 ==="
mkdir -p /root/ComfyUI/models/checkpoints
if [ ! -f /root/ComfyUI/models/checkpoints/Realistic-Vision-V5.1.safetensors ]; then
    echo "下载 Realistic Vision V5.1..."
    cd /root/ComfyUI/models/checkpoints
    # 用 huggingface 或 civitai 下载
    pip install huggingface-hub -q
    python3 -c "
from huggingface_hub import hf_hub_download
hf_hub_download('SG161222/Realistic_Vision_V5.1_noVAE',
    filename='Realistic-Vision-V5.1.safetensors',
    local_dir='/root/ComfyUI/models/checkpoints')
" 2>/dev/null || echo "下载失败，请手动下载 SD 模型"
fi

echo "=== Step 4: 验证 ==="
cd /root/ComfyUI
python3 main.py --listen 0.0.0.0 --port 8188 --highvram &
sleep 10
echo "ComfyUI 启动完成"
curl -s http://localhost:8188/api/system/stats

echo "=== 完成！==="
echo "通过 SSH 隧道: ssh -L 18188:localhost:8188 root@YOUR_HOST"
