#!/usr/bin/env python3
"""ComfyUI 全链路测试 - 真实 GPU 推理验证"""

import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from providers.comfyui.client import ComfyUIClient

# ⚡ 配置 - 明晚改这里
HOST = "connect.bjb2.seetacloud.com"
PORT = 8188
# SSH隧道时用 127.0.0.1

def main():
    print("=" * 50)
    print("ComfyUI 全链路测试")
    print(f"Target: {HOST}:{PORT}")
    print("=" * 50)

    # 1. 文生图同步测试
    print("\n[1/2] 文生图测试")
    result = ComfyUIClient.txt2img_sync(
        host=HOST,
        port=PORT,
        ckpt_name="Realistic-Vision-V5.1.safetensors",
        prompt="a cute cat wearing a wizard hat, magical forest, photorealistic, highly detailed",
        negative_prompt="blurry, low quality, distorted",
        width=512,
        height=512,
        steps=12,
        seed=42,
    )

    if result["success"]:
        print(f"  ✅ 成功! prompt_id: {result['prompt_id']}")
        for img in result["images"]:
            fn = f"output_{img['filename']}"
            out_dir = os.path.join(os.path.dirname(__file__), "output")
            os.makedirs(out_dir, exist_ok=True)
            out_path = os.path.join(out_dir, fn)
            with open(out_path, "wb") as f:
                f.write(img["data"])
            print(f"  📸 {fn} ({img['size_kb']:.0f}KB)")
    else:
        print(f"  ❌ 失败: {result.get('error', 'unknown')}")
        return

    # 2. 接口级验证（通过异步client查模型列表）
    print("\n[2/2] 接口连通性验证")
    import asyncio
    async def verify():
        client = ComfyUIClient(server_addr=HOST, server_port=PORT)
        try:
            models = await client.list_models()
            print(f"  可用模型 ({len(models)}): {models[:5]}...")
            info = await client.get_object_info()
            node_count = len(info)
            print(f"  可用节点数: {node_count}")
            await client.close()
            return True
        except Exception as e:
            print(f"  ❌ 接口错误: {e}")
            await client.close()
            return False

    ok = asyncio.run(verify())
    if ok:
        print("  ✅ 接口正常")

    print("\n" + "=" * 50)
    print("全链路测试完成")
    print("=" * 50)


if __name__ == "__main__":
    main()
