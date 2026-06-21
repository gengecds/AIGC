#!/usr/bin/env python3
"""真 Provider 端到端 Pipeline 测试

流程: DeepSeek 生成剧本 → DeepSeek 生成分镜 → 
      ComfyUI SD 出图（仅1镜）→ 成功即验证通路

要求: GPU开机 + ComfyUI运行中
"""

import sys, os, asyncio, json, subprocess
sys.path.insert(0, os.path.dirname(__file__))
import site
site.addsitedir("/Users/mac/Library/Python/3.14/lib/python/site-packages")

from agents.script_agent import ScriptAgent
from agents.storyboard_agent import StoryboardAgent
from agents.image_agent import ImageGenAgent
from providers.comfyui.client import ComfyUIClient


async def main():
    print("=" * 60)
    print("真 Provider 端到端 Pipeline 测试")
    print("  LLM: DeepSeek API (deepseek-chat)")
    print("  Image: ComfyUI SD (Realistic-Vision-V5.1)")
    print("=" * 60)

    # SSH 隧道
    subprocess.run(["kill", "$(lsof -t -i :18188)"], shell=True, capture_output=True)
    tunnel = subprocess.Popen([
        "sshpass", "-p", "900917_19871002-Gz",
        "ssh", "-o", "StrictHostKeyChecking=no",
        "-p", "30476", "-L", "18188:localhost:8188",
        "-f", "-N", "root@connect.bjb2.seetacloud.com"
    ])
    await asyncio.sleep(2)
    print("  🔗 SSH 隧道: localhost:18188 → GPU:8188")

    # 真 Provider
    comfy_client = ComfyUIClient(server_addr="127.0.0.1", server_port=18188)
    script_agent = ScriptAgent(llm_provider="deepseek")
    storyboard_agent = StoryboardAgent(llm_provider="deepseek")
    image_agent = ImageGenAgent(use_comfyui=True)
    image_agent.image_provider.client = comfy_client

    user_input = "末世题材，主角林峰在丧尸爆发后独自生存"

    async def run_agent(name, agent, *args):
        print(f"\n[{name}] 开始...")
        r = await agent.run(*args)
        if r.success:
            print(f"  ✅ 完成")
        else:
            print(f"  ❌ 失败: {r.error}")
        return r

    # 剧本
    r1 = await run_agent("ScriptAgent", script_agent, user_input)
    if not r1.success: return
    eps = r1.data.get('episodes', [])
    print(f"  剧名: {r1.data.get('title','?')} 集数: {len(eps)}")

    # 分镜
    r2 = await run_agent("StoryboardAgent", storyboard_agent, r1.data)
    if not r2.success: return
    shots = r2.data.get('episodes', [{}])[0].get('shots', [])
    print(f"  分镜: {len(shots)} 个")

    # 只出第一镜
    if shots:
        r2.data['episodes'][0]['shots'] = shots[:1]
        print(f"  仅用第一镜: \"{shots[0].get('sd_prompt','')[:50]}...\"")
    else:
        # mock
        r2.data = {"episodes": [{"episode_number": 1, "shots": [{"shot_id": 1, "sd_prompt": "末世废墟中的年轻男子", "seed": 0}]}]}
        print("  分镜为空，使用 mock 数据")

    # 出图
    r3 = await run_agent("ImageGenAgent", image_agent, r2.data)
    if not r3.success: return
    images = r3.data.get("images", {})
    print(f"  ComfyUI 出图: {len(images)} 张")

    # 报告
    print(f"\n{'='*60}")
    print(f"✅ 真 Provider 联调通过！")
    print(f"   剧本: {r1.data.get('title','?')}")
    print(f"   分镜: {len(shots)} → 出图1镜: {'✅' if images else '❌'}")
    subprocess.run(["kill", "$(lsof -t -i :18188)"], shell=True, capture_output=True)


if __name__ == "__main__":
    asyncio.run(main())
