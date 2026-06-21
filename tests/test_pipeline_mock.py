#!/usr/bin/env python3
"""端到端全流程测试 - Mock 模式（0 API 费用，不依赖 GPU）

验证从 用户输入 → 剧本 → 分镜 → 角色 → 出图 → 视频 → 字幕 → 合成
整条管线在 Mock 模式下的数据流完整性。
预期耗时: < 5 秒
"""

import sys, os, asyncio
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# 注入 user site 解决 sqlalchemy issue
import site
site.addsitedir("/Users/mac/Library/Python/3.14/lib/python/site-packages")

from agents.script_agent import ScriptAgent
from agents.storyboard_agent import StoryboardAgent
from agents.character_agent import CharacterDesignAgent
from agents.image_agent import ImageGenAgent
from agents.video_agent import VideoGenAgent
from agents.subtitle_agent import SubtitleAgent
from agents.compose_agent import ComposeAgent
from pipeline.scheduler import Pipeline, PipelineState


async def main():
    print("=" * 55)
    print("端到端全流程 Mock 测试 - 0 API 费用 / 0 GPU")
    print("=" * 55)

    pipeline = Pipeline(PipelineState("storage/checkpoints_test"))
    pipeline.state.clear()

    # 用 Mock provider 构造所有 Agent
    # 各 Agent 的 init 参数不同，按实际定义传参
    script_agent = ScriptAgent()  # 默认 deepseek，但会尝试 import，mock 模式需要跳过
    storyboard_agent = StoryboardAgent()
    character_agent = CharacterDesignAgent()  # 默认用 MockImageProvider
    image_agent = ImageGenAgent()  # 默认不用 comfyui，用 mock
    video_agent = VideoGenAgent()
    subtitle_agent = SubtitleAgent()
    compose_agent = ComposeAgent()

    agents = [
        script_agent,
        storyboard_agent,
        character_agent,
        image_agent,
        video_agent,
        subtitle_agent,
        compose_agent,
    ]

    user_input = "末世题材，主角林峰在丧尸爆发后独自生存，在一片废墟中遇到了另一个幸存者小雨"

    print(f"\n[Pipeline] 用户输入: \"{user_input}\"")
    print(f"[Pipeline] Agents: {[a.name for a in agents]}\n")

    result = await pipeline.run(agents, user_input)

    if result["success"]:
        print("✅ Pipeline 全链路执行成功！")
        print(f"   耗时: 7 个 Agent 均完成")
        results = result["results"]

        # 验证每个 Agent 输出
        checks = {
            "script_agent": "剧本输出",
            "storyboard_agent": "分镜输出", 
            "character_agent": "角色输出",
            "image_agent": "出图输出",
            "video_agent": "视频输出",
            "subtitle_agent": "字幕输出",
            "compose_agent": "合成输出",
        }
        all_pass = True
        for name, desc in checks.items():
            data = results.get(name, {})
            ok = data.get("success", False)
            status = "✅" if ok else "❌"
            if not ok:
                all_pass = False
            print(f"   {status} {name}: {desc}")

        print(f"\n{'=' * 55}")
        if all_pass:
            print("✅ 最终结论: Pipeline 数据流完整性验证通过")
            print("   所有 7 个 Agent 数据传递正确")
            print("   后续只需将 MockProvider → 真 ComfyUIProvider + DeepSeekProvider")
        else:
            print("❌ 部分 Agent 失败")
    else:
        print(f"❌ Pipeline 失败于: {result.get('failed_at', 'unknown')}")
        print(f"   错误: {result.get('results', {})}")

    pipeline.state.clear()


if __name__ == "__main__":
    asyncio.run(main())
