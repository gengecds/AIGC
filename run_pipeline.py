#!/usr/bin/env python3
"""全链路端到端运行脚本 - DeepSeek + ComfyUI + 回传 + 合成

用法:  python3 run_pipeline.py
要求: GPU 开机 + ComfyUI 运行中 + SSH 隧道（脚本自动建立）
"""

import sys, os, asyncio, json, subprocess, logging

sys.path.insert(0, os.path.dirname(__file__))
import site
site.addsitedir("/Users/mac/Library/Python/3.14/lib/python/site-packages")

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

from pathlib import Path

BASE_DIR = Path(__file__).parent
STORAGE = BASE_DIR / "storage" / "output"
STORAGE.mkdir(parents=True, exist_ok=True)

# ── SSH 隧道 ──
SSH_PW = "900917_19871002-Gz"
SSH_HOST = "connect.bjb2.seetacloud.com"
SSH_PORT = 30476
LOCAL_PORT = 18188

def ensure_tunnel():
    """确保 SSH 隧道存在"""
    subprocess.run(f"kill $(lsof -t -i :{LOCAL_PORT}) 2>/dev/null", shell=True)
    r = subprocess.run([
        "sshpass", "-p", SSH_PW, "ssh", "-o", "StrictHostKeyChecking=no",
        "-p", str(SSH_PORT), "-L", f"{LOCAL_PORT}:localhost:8188",
        "-f", "-N", f"root@{SSH_HOST}"
    ], capture_output=True, text=True)
    if r.returncode != 0 and "already in use" not in r.stderr:
        logger.error(f"隧道建立失败: {r.stderr}")
        return False
    logger.info(f"🔗 SSH 隧道 localhost:{LOCAL_PORT} → GPU:8188")
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

    print("=" * 60)
    print("全链路 Pipeline 运行")
    print("  LLM:    DeepSeek API")
    print("  Image:  ComfyUI SD (GPU)")
    print("  Video:  ComfyUI HunyuanVideo (GPU)")
    print("=" * 60)

    # 1. SSH 隧道
    if not ensure_tunnel():
        return

    # 2. 共享 ComfyUIClient
    comfy = ComfyUIClient(server_addr="127.0.0.1", server_port=LOCAL_PORT)

    # 3. 初始化所有 Agent（真 Provider）
    agents = [
        ScriptAgent(llm_provider="deepseek"),
        StoryboardAgent(llm_provider="deepseek"),
        CharacterDesignAgent(use_comfyui=True, comfy_client=comfy),
        ImageGenAgent(use_comfyui=True),
        VideoGenAgent(use_comfyui=True, comfy_client=comfy),
        SubtitleAgent(),
        ComposeAgent(),
    ]

    # 共享 comfy client 给 image_agent
    agents[3].image_provider.client = comfy

    # 4. Pipeline 调度
    pipeline = Pipeline(PipelineState(str(BASE_DIR / "storage" / "checkpoints_real")))
    user_input = "末世题材，主角林峰在丧尸爆发后独自生存"

    print(f"\n输入: {user_input}\n")

    result = await pipeline.run(agents, user_input, resume=False)

    if result["success"]:
        print(f"\n{'='*60}")
        print("✅ 全链路 Pipeline 执行成功！")
        print(f"  Pipeline ID: {result['pipeline_id']}")
    else:
        print(f"\n❌ 在 {result['failed_at']} 阶段失败")
        print(f"  错误: {result['results'].get(result['failed_at'], {}).get('error', '?')}")
        # 继续回传已完成阶段的结果

    # 5. 回传结果
    print(f"\n{'='*60}")
    print("开始回传生成结果...")
    fetcher = ResultFetcher(server_addr="127.0.0.1", server_port=LOCAL_PORT)
    try:
        fetched = await fetcher.fetch_results(result["results"])
        total_files = sum(len(shots) for shots in fetched.values())
        print(f"  ✅ 回传完成: {total_files} 个文件到 {STORAGE}")
    except Exception as e:
        logger.error(f"回传失败: {e}")
    finally:
        await fetcher.close()

    # 6. 报告
    print(f"\n{'='*60}")
    print("运行报告")
    print(f"{'='*60}")
    for name, r in result["results"].items():
        d = r.get("data", {}) if isinstance(r, dict) else {}
        m = r.get("metadata", {}) if isinstance(r, dict) else {}
        status = "✅" if r.get("success", True) else "❌"
        print(f"  {status} {name}: {m}")
    print(f"\n本地文件: {STORAGE}")
    print("完成！")

    # 关闭
    await comfy.close()
    subprocess.run(f"kill $(lsof -t -i :{LOCAL_PORT}) 2>/dev/null", shell=True)


if __name__ == "__main__":
    asyncio.run(main())
