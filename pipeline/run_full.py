#!/usr/bin/env python3
"""全链路真Provider运行（正式 Pipeline 调度器 v2）
用法:
  python3 pipeline/run_full.py                          # 全新执行
  python3 pipeline/run_full.py --resume                  # 从断点恢复
  python3 pipeline/run_full.py --no-review               # 跳过审核断点（全自动）
  python3 pipeline/run_full.py --mock                    # Mock 模式（无GPU/无API）
"""
import sys, os, json, asyncio
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pipeline.scheduler import Pipeline
from agents.script_agent import ScriptAgent
from agents.storyboard_agent import StoryboardAgent
from agents.character_agent import CharacterDesignAgent
from agents.image_agent import ImageGenAgent
from agents.video_agent import VideoGenAgent
from agents.subtitle_agent import SubtitleAgent
from agents.video_compose_agent import VideoComposeAgent
from agents.publish_agent import PublishAgent

# ComfyUI 模式
from providers.comfyui.client import ComfyUIClient
client = ComfyUIClient(server_addr="127.0.0.1", server_port=18188)

import argparse
parser = argparse.ArgumentParser()
parser.add_argument("--resume", action="store_true", help="从断点恢复")
parser.add_argument("--no-review", action="store_true", help="跳过审核断点（全自动）")
parser.add_argument("--mock", action="store_true", help="Mock 模式（无GPU/无API）")
args = parser.parse_args()

STORY = (
    "一个程序员穿越到猫娘世界，被猫娘公主关在城堡里当宠物，"
    "最终用Java代码逃出生天"
)

if args.mock:
    from agents.base import AgentResult
    from providers.mock_provider import MockLLMProvider, MockImageProvider, MockVideoProvider
    mock_llm = MockLLMProvider()
    mock_img = MockImageProvider()
    mock_vid = MockVideoProvider()
    print("⚠️ Mock 模式: 使用 MockProvider（0 API/GPU 费用）")
    agents = [
        ScriptAgent(llm_provider=mock_llm),
        StoryboardAgent(llm_provider=mock_llm),
        CharacterDesignAgent(use_comfyui=False, image_provider=mock_img),
        ImageGenAgent(use_comfyui=False, image_provider=mock_img),
        VideoGenAgent(use_comfyui=False, video_provider=mock_vid),
        SubtitleAgent(),
        VideoComposeAgent(),
        PublishAgent(),
    ]
else:
    agents = [
        ScriptAgent(),
        StoryboardAgent(),
        CharacterDesignAgent(use_comfyui=True, comfy_client=client),
        ImageGenAgent(use_comfyui=True, comfy_client=client),
        VideoGenAgent(use_comfyui=True, comfy_client=client),
        SubtitleAgent(),
        VideoComposeAgent(),
        PublishAgent(),
    ]

# Callback 示例：终端打印进度
async def on_start(name, idx, total):
    print(f"  [{idx+1}/{total}] 🚀 {name} 开始")

async def on_complete(name, idx, total, meta):
    print(f"  [{idx+1}/{total}] ✅ {name} 完成")

async def on_fail(name, idx, total, err):
    print(f"  [{idx+1}/{total}] ❌ {name} 失败: {err}")

async def on_done(final):
    results = final.get("results", {})
    pub = results.get("publish_agent", {}).get("data", {}).get("published", [])
    if not pub:
        pub = results.get("video_compose_agent", {}).get("data", {}).get("published", [])
    print(f"\n{'='*50}")
    print(f"🎉 {'|'.join(str(p.get('episode_number','')) for p in pub)} 集完成!")
    for p in pub:
        fp = p.get("final_path", "")
        sz = p.get("file_size_kb", 0)
        print(f"  第{p.get('episode_number','')}集: {fp} ({sz}KB)")
    print(f"{'='*50}")

async def on_review(agent_name, reason, data):
    print(f"\n⏸️ [审核] {agent_name}: {reason}")
    shot_count = 0
    if reason == "storyboard_approval":
        eps = data.get("episodes", [])
        shot_count = sum(len(e.get("shots", [])) for e in eps)
        print(f"  分镜: {len(eps)}集/{shot_count}个镜头")
    elif reason == "character_approval":
        chars = data.get("characters", [])
        print(f"  角色: {len(chars)}个")
    print("  默认自动确认...\n")

pipeline = Pipeline()
pipeline.set_callbacks(
    on_agent_start=on_start,
    on_agent_complete=on_complete,
    on_agent_fail=on_fail,
    on_pipeline_complete=on_done,
    on_review_needed=on_review,
)

# 等待确认（终端模式自动确认所有审核）
async def auto_approve(pipeline, delay=2):
    while True:
        if pipeline._paused:
            await asyncio.sleep(delay)
            pipeline.approve_review()
        await asyncio.sleep(1)

async def main():
    auto_task = asyncio.create_task(auto_approve(pipeline))
    
    result = await pipeline.run(
        agents,
        user_input=STORY,
        resume=args.resume,
        enable_review=not args.no_review,
    )
    
    auto_task.cancel()
    
    if result.get("success"):
        print(f"\n✅ 管线执行成功: {result['pipeline_id']}")
    elif result.get("paused"):
        print(f"\n⏸️ 管线暂停: {result['paused_at']}")
    else:
        print(f"\n❌ 管线失败: {result.get('failed_at','')}: {result.get('error','')}")

asyncio.run(main())
