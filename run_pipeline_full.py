#!/usr/bin/env python3
"""全链路端到端 - 自动执行完所有 Phase 2-4 内容
自动: 剧本→分镜→定妆照→出图→视频→字幕→合成→回传
"""

import sys, os, asyncio, json, subprocess, logging
sys.path.insert(0, os.path.dirname(__file__))
import site
site.addsitedir("/Users/mac/Library/Python/3.14/lib/python/site-packages")

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
from pathlib import Path

BASE = Path(__file__).parent
STORAGE = BASE / "storage" / "output"
STORAGE.mkdir(parents=True, exist_ok=True)

SSH_PW = "900917_19871002-Gz"
LOCAL_PORT = 18188

def ensure_tunnel():
    subprocess.run(f"kill $(lsof -t -i :{LOCAL_PORT}) 2>/dev/null", shell=True)
    r = subprocess.run(["sshpass","-p",SSH_PW,"ssh","-o","StrictHostKeyChecking=no",
        "-p","30476","-L","18188:localhost:8188","-f","-N","root@connect.bjb2.seetacloud.com"],
        capture_output=True, text=True)
    if r.returncode != 0 and "already in use" not in r.stderr:
        print(f"❌ 隧道失败: {r.stderr}"); return False
    print("🔗 SSH 隧道 ok")
    return True

async def main():
    from providers.comfyui.client import ComfyUIClient
    from providers.result_fetcher import ResultFetcher
    from agents.script_agent import ScriptAgent
    from agents.storyboard_agent import StoryboardAgent
    from agents.character_agent import CharacterDesignAgent
    from agents.image_agent import ImageGenAgent
    from agents.video_agent import VideoGenAgent
    from agents.subtitle_agent import SubtitleAgent
    from agents.compose_agent import ComposeAgent
    from pipeline.scheduler import Pipeline, PipelineState

    print("="*60)
    print("全链路端到端运行")
    print("="*60)

    if not ensure_tunnel(): return

    comfy = ComfyUIClient(server_addr="127.0.0.1", server_port=LOCAL_PORT)

    # 所有真 Provider Agent
    agents = [
        ScriptAgent(llm_provider="deepseek"),
        StoryboardAgent(llm_provider="deepseek"),
        CharacterDesignAgent(use_comfyui=True, comfy_client=comfy),
        ImageGenAgent(use_comfyui=True),
        VideoGenAgent(use_comfyui=True, comfy_client=comfy),
        SubtitleAgent(),
        ComposeAgent(),
    ]
    agents[3].image_provider.client = comfy

    # 执行
    pipeline = Pipeline(PipelineState(str(BASE/"storage"/"checkpoints_real")))
    result = await pipeline.run(agents, "末世题材，主角林峰在丧尸爆发后独自生存", resume=False)

    if not result["success"]:
        print(f"\n❌ 失败于 {result.get('failed_at','?')}")
        import traceback
        traceback.print_exc()

    # 回传
    print("\n回传结果...")
    fetcher = ResultFetcher(server_addr="127.0.0.1", server_port=LOCAL_PORT)
    try:
        fetched = await fetcher.fetch_results(result["results"])
        total = sum(len(v) for v in fetched.values())
        print(f"✅ 回传 {total} 个文件")
    except Exception as e:
        print(f"回传错误: {e}")

    await comfy.close()
    await fetcher.close()

    # 报告
    print(f"\n{'='*60}")
    print("运行报告")
    for name, r in result["results"].items():
        m = (r.get("data",{}) or {}).get("metadata", r.get("metadata",{}))
        status = "✅" if r.get("success",True) else "❌"
        print(f"  {status} {name}: {m}")

    subprocess.run(f"kill $(lsof -t -i :{LOCAL_PORT}) 2>/dev/null", shell=True)
    print(f"\n完成！文件在: {STORAGE}")

if __name__ == "__main__":
    asyncio.run(main())
