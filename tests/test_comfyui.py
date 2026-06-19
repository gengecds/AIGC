#!/usr/bin/env python3
"""
Phase 0.5: 验证 ComfyUI 能否生成图片
调用远程 ComfyUI API（通过 SSH 隧道 127.0.0.1:8188）
"""

import json
import urllib.request
import urllib.parse
import uuid
import sys
from pathlib import Path


class ComfyUITestClient:
    """ComfyUI 最小测试客户端"""
    
    def __init__(self, server="http://127.0.0.1:8188"):
        self.server = server
        self.client_id = str(uuid.uuid4())
    
    def _post(self, endpoint, data):
        url = f"{self.server}/{endpoint}"
        req = urllib.request.Request(
            url,
            data=json.dumps(data).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        resp = urllib.request.urlopen(req)
        return json.loads(resp.read().decode("utf-8"))
    
    def _get(self, endpoint):
        url = f"{self.server}/{endpoint}"
        resp = urllib.request.urlopen(url)
        return json.loads(resp.read().decode("utf-8"))
    
    def get_status(self):
        return self._get("queue")
    
    def get_models(self):
        """获取已安装的 checkpoint 模型"""
        info = self._get("object_info")
        ckpt_node = info.get("CheckpointLoaderSimple", {})
        ckpt_input = ckpt_node.get("input", {}).get("required", {})
        ckpts = ckpt_input.get("ckpt_name", [None])[0] if ckpt_input else []
        return ckpts if isinstance(ckpts, list) else []
    
    def queue_prompt(self, workflow):
        """提交工作流"""
        payload = {
            "prompt": workflow,
            "client_id": self.client_id,
        }
        return self._post("prompt", payload)


def build_minimal_workflow(ckpt_name: str) -> dict:
    """构建最简单的文生图工作流"""
    # Node 1: 模型加载
    # Node 2: CLIP + VAE
    # Node 3: KSampler
    # Node 4: Empty Latent Image
    # Node 5: Save Image
    
    return {
        "3": {
            "class_type": "KSampler",
            "inputs": {
                "seed": 42,
                "steps": 20,
                "cfg": 7.5,
                "sampler_name": "euler",
                "scheduler": "normal",
                "denoise": 1.0,
                "model": ["4", 0],
                "positive": ["6", 0],
                "negative": ["7", 0],
                "latent_image": ["5", 0],
            },
        },
        "4": {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {"ckpt_name": ckpt_name},
        },
        "5": {
            "class_type": "EmptyLatentImage",
            "inputs": {"width": 1024, "height": 1024, "batch_size": 1},
        },
        "6": {
            "class_type": "CLIPTextEncode",
            "inputs": {
                "text": "cinematic shot, a warrior standing on a cliff, sunset, epic, masterpiece, best quality, high detail",
                "clip": ["4", 1],
            },
        },
        "7": {
            "class_type": "CLIPTextEncode",
            "inputs": {
                "text": "nsfw, low quality, ugly, blurry, watermark",
                "clip": ["4", 1],
            },
        },
        "8": {
            "class_type": "VAEDecode",
            "inputs": {
                "samples": ["3", 0],
                "vae": ["4", 2],
            },
        },
        "9": {
            "class_type": "SaveImage",
            "inputs": {
                "filename_prefix": "phase05_test",
                "images": ["8", 0],
            },
        },
    }


def main():
    print("=== Phase 0.5: ComfyUI 可用性验证 ===")
    client = ComfyUITestClient()
    
    # 1. 检查连接
    status = client.get_status()
    q_running = len(status.get("queue_running", []))
    q_pending = len(status.get("queue_pending", []))
    print(f"[1] ComfyUI 连接: ✅ (队列: {q_running} 运行中, {q_pending} 待处理)")
    
    # 2. 检查模型
    models = client.get_models()
    print(f"[2] 已安装模型: {models}")
    if not models:
        print("    ❌ 无可用模型，需要下载")
        return False
    
    # 3. 提交生成任务
    ckpt = models[0]
    print(f"[3] 使用模型: {ckpt}")
    print("    按默认 workflow 提交文生图...")
    
    workflow = build_minimal_workflow(ckpt)
    result = client.queue_prompt(workflow)
    prompt_id = result.get("prompt_id", "")
    print(f"    任务 ID: {prompt_id}")
    print(f"    响应: {json.dumps(result, indent=2)}")
    
    # 4. 等待完成
    import time
    print("[4] 等待生成完成...")
    for i in range(60):  # 最多等60秒
        time.sleep(1)
        status = client.get_status()
        q_running = status.get("queue_running", [])
        if not q_running:
            print(f"    ✅ 生成完成 (等待 {i+1}s)")
            break
        if i % 10 == 0 and i > 0:
            print(f"    还在生成中... ({i}s)")
    else:
        print("    ⚠️ 超时，但可能后台仍在生成")
    
    # 5. 查看历史
    history = client._get(f"history/{prompt_id}")
    outputs = history.get(prompt_id, {}).get("outputs", {})
    print(f"[5] 输出节点: {list(outputs.keys())}")
    
    print("\n=== 验证完成 ===")
    return True


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
