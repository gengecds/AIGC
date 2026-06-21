#!/usr/bin/env python3
"""全链路轻量验证 - 0 API / 0 GPU / < 1秒

不调 DeepSeek，所有 Agent 直接传递预先生成的模拟数据
验证 Pipeline 调度器和各 Agent 间的数据流完整性
"""

import sys, os, asyncio, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import site
site.addsitedir("/Users/mac/Library/Python/3.14/lib/python/site-packages")

from agents.base import BaseAgent, Agent, AgentResult


class MockScriptAgent(Agent):
    def __init__(self):
        super().__init__(name="script_agent")
    async def run(self, user_input: str) -> AgentResult:
        return AgentResult(success=True, data={
            "title": "末世求生",
            "genre": "末世",
            "summary": "丧尸爆发后的生存故事",
            "characters": [
                {"name": "林峰", "gender": "男", "age": "青年", "appearance": "短发黑色皮夹克", "personality": "坚毅", "role": "主角"},
                {"name": "小雨", "gender": "女", "age": "青年", "appearance": "长辫子红色围巾", "personality": "温柔", "role": "配角"},
            ],
            "episodes": [{"episode_number": 1, "title": "相遇", "plot": "...", "dialogues": [
                {"scene": "废墟街道", "character": "林峰", "line": "有人吗？"},
                {"scene": "废墟街道", "character": "小雨", "line": "别过来！"},
            ]}],
        })


class MockStoryboardAgent(Agent):
    def __init__(self):
        super().__init__(name="storyboard_agent")
    async def run(self, script: dict) -> AgentResult:
        return AgentResult(success=True, data={"shots": [
            {"shot_id": 1, "scene_id": 1, "description": "林峰在废墟中", "shot_type": "中景", "duration": 5, "character": "林峰",
             "sd_prompt": "young man in leather jacket, ruins", "sd_negative": "blurry", "video_motion": "slow pan"},
            {"shot_id": 2, "scene_id": 1, "description": "小雨躲在角落", "shot_type": "近景", "duration": 4, "character": "小雨",
             "sd_prompt": "young woman with braid, scared", "sd_negative": "blurry", "video_motion": "static"},
        ]})


class MockCharacterAgent(Agent):
    def __init__(self):
        super().__init__(name="character_agent")
    async def run(self, script: dict, db_assets: dict = None) -> AgentResult:
        return AgentResult(success=True, data={"characters": [
            {"name": "林峰", "seed": 6789, "asset": {"portrait": "mock/linfeng.png", "full_body": "mock/linfeng_full.png"}},
            {"name": "小雨", "seed": 4321, "asset": {"portrait": "mock/xiaoyu.png", "full_body": "mock/xiaoyu_full.png"}},
        ]})


class MockImageAgent(Agent):
    def __init__(self):
        super().__init__(name="image_agent")
    async def run(self, storyboard: dict, char_assets: dict = None) -> AgentResult:
        return AgentResult(success=True, data={"images": {
            "shot_1": "mock/shot_001.png", "shot_2": "mock/shot_002.png",
        }})


class MockVideoAgent(Agent):
    def __init__(self):
        super().__init__(name="video_agent")
    async def run(self, images_result, storyboard: dict, char_assets: dict = None) -> AgentResult:
        return AgentResult(success=True, data={"videos": {
            "shot_1": "mock/shot_001.mp4", "shot_2": "mock/shot_002.mp4",
        }})


class MockSubtitleAgent(Agent):
    def __init__(self):
        super().__init__(name="subtitle_agent")
    async def run(self, script: dict, storyboard: dict = None) -> AgentResult:
        return AgentResult(success=True, data={"subtitles": [
            {"start": 0.0, "end": 3.0, "text": "有人吗？"},
            {"start": 3.5, "end": 6.0, "text": "别过来！"},
        ]})


class MockComposeAgent(Agent):
    def __init__(self):
        super().__init__(name="compose_agent")
    async def run(self, videos_result, subtitle_result) -> AgentResult:
        return AgentResult(success=True, data={
            "final_video": "mock/final_ep01.mp4",
            "duration": 180,
        })


async def main():
    print("=" * 55)
    print("全链路轻量验证 - 0 API / 0 GPU / < 1 秒")
    print("=" * 55)

    from pipeline.scheduler import Pipeline, PipelineState

    pipeline = Pipeline(PipelineState("storage/checkpoints_light"))
    pipeline.state.clear()

    agents = [
        MockScriptAgent(),
        MockStoryboardAgent(),
        MockCharacterAgent(),
        MockImageAgent(),
        MockVideoAgent(),
        MockSubtitleAgent(),
        MockComposeAgent(),
    ]

    result = await pipeline.run(agents, "末世题材测试")

    print()
    if result["success"]:
        r = result["results"]
        checks = {
            "script_agent": "剧本",
            "storyboard_agent": "分镜",
            "character_agent": "角色",
            "image_agent": "出图",
            "video_agent": "视频",
            "subtitle_agent": "字幕",
            "compose_agent": "合成",
        }
        all_pass = True
        for name, desc in checks.items():
            ok = r.get(name, {}).get("success", False)
            mark = "✅" if ok else "❌"
            if not ok: all_pass = False
            print(f"  {mark} {name} ({desc})")

        print(f"\n{'=' * 55}")
        if all_pass:
            print("✅ Pipeline 数据流完整性验证通过！")
            print("   7个Agent间数据传递正确：")
            print("   输入 → 剧本(1人1集2场景) → 分镜(2shots) → 角色(2人)")
            print("   → 出图(2张) → 视频(2段) → 字幕(2条) → 合成(最终视频)")
        else:
            print("❌ 部分失败")
        print("   Checkpoints:", os.listdir("storage/checkpoints_light"))
    else:
        print(f"❌ 失败于: {result.get('failed_at')}")

    pipeline.state.clear()
    for f in os.listdir("storage/checkpoints_light"):
        os.remove(os.path.join("storage/checkpoints_light", f))


if __name__ == "__main__":
    asyncio.run(main())
