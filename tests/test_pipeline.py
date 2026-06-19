#!/usr/bin/env python3
"""Phase 1 管线快速验证脚本

有 DEEPSEEK_API_KEY：真调 Agent 1~2
无 DEEPSEEK_API_KEY：用预设数据模拟 Agent 1~2，Mock 跑通剩余数据流
"""

import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from agents.script_agent import ScriptAgent
from agents.storyboard_agent import StoryboardAgent
from agents.character_agent import CharacterDesignAgent
from agents.image_agent import ImageGenAgent
from agents.video_agent import VideoGenAgent
from agents.subtitle_agent import SubtitleAgent
from agents.compose_agent import ComposeAgent


# 预设测试数据（无 API Key 时使用）
MOCK_SCRIPT = {
    "title": "末世求生",
    "genre": "末世",
    "summary": "丧尸爆发后，独行侠林峰偶遇小女孩小雪，二人结伴寻找安全区",
    "characters": [
        {
            "name": "林峰",
            "gender": "男",
            "age": "青年",
            "appearance": "短发，棱角分明，身穿深色战术夹克，背背包",
            "personality": "冷静果断",
            "role": "主角"
        },
        {
            "name": "小雪",
            "gender": "女",
            "age": "少年",
            "appearance": "齐肩黑发，圆脸，穿着破旧的粉色外套",
            "personality": "天真坚强",
            "role": "主角"
        }
    ],
    "episodes": [
        {
            "episode_number": 1,
            "title": "相遇",
            "plot": "林峰在废弃超市遇到被丧尸围困的小雪，出手相救",
            "dialogues": [
                {"scene": "废弃超市", "character": "林峰", "line": "别怕，到我后面来。"},
                {"scene": "废弃超市", "character": "小雪", "line": "哥哥，我妈妈她..."},
                {"scene": "街道", "character": "林峰", "line": "先离开这里再说。"}
            ]
        }
    ]
}

MOCK_STORYBOARD = {
    "episodes": [
        {
            "episode_number": 1,
            "title": "相遇",
            "shots": [
                {
                    "shot_id": 1, "scene": "废弃超市", "shot_type": "远",
                    "duration": 5, "camera_movement": "固定",
                    "characters": ["林峰", "小雪"],
                    "action": "林峰站在超市门口观望",
                    "background": "凌乱的超市货架，玻璃破碎",
                    "lighting": "昏暗", "dialogue": "",
                    "sd_prompt": "A man in tactical jacket at a ruined supermarket entrance, masterpiece, best quality",
                },
                {
                    "shot_id": 2, "scene": "废弃超市", "shot_type": "中",
                    "duration": 6, "camera_movement": "推",
                    "characters": ["小雪"],
                    "action": "小雪躲在货架后哭泣",
                    "background": "倒塌的货架，散落的食品",
                    "lighting": "昏暗", "dialogue": "",
                    "sd_prompt": "A young girl hiding behind shelves, crying, masterpiece, best quality",
                },
                {
                    "shot_id": 3, "scene": "废弃超市", "shot_type": "近",
                    "duration": 4, "camera_movement": "固定",
                    "characters": ["林峰"],
                    "action": "林峰伸手拉小雪起来",
                    "background": "超市过道",
                    "lighting": "逆光", "dialogue": "别怕，到我后面来。",
                    "sd_prompt": "Close up of a man reaching out his hand, masterpiece, best quality",
                },
            ]
        }
    ]
}


async def main():
    print("=" * 60)
    print("AI 漫剧创作平台 - Phase 1 管线验证")
    print("=" * 60)
    has_api_key = bool(os.getenv("DEEPSEEK_API_KEY", "").strip())

    # 1. ScriptAgent
    print("\n[1/7] ScriptAgent - 剧本生成")
    if has_api_key:
        agent1 = ScriptAgent()
        result1 = await agent1.run("末世题材，主角林峰遇到小女孩小雪")
    else:
        print("  ⚠️ 无 DEEPSEEK_API_KEY，使用预设数据")
        result1 = asyncio.run.__class__.__new__(asyncio.run.__class__)
        # 使用简单的 dict 模拟 result
        mock_result_script = MOCK_SCRIPT
        script_data = mock_result_script
        result1_success = True
        # 直接 mock 跳过
        class MockResult:
            success = True
            data = mock_result_script
            metadata = {
                "agent": "script_agent",
                "characters": len(mock_result_script["characters"]),
                "episodes": len(mock_result_script["episodes"]),
                "timestamp": "2026-06-19T00:00:00",
            }
            def to_dict(self):
                return {"success": True, "data": self.data, "metadata": self.metadata}
        mock_result1 = MockResult()
        script_data = mock_result1.data
        print(f"  ✅ 预设剧本: {script_data['title']}")
        print(f"     角色: {len(script_data['characters'])} 个")
        print(f"     集数: {len(script_data['episodes'])} 集")

    # 2. StoryboardAgent
    print("\n[2/7] StoryboardAgent - 分镜生成")
    if has_api_key:
        agent2 = StoryboardAgent()
        result2 = await agent2.run(script_data)
        storyboard_data = result2.data
    else:
        storyboard_data = MOCK_STORYBOARD
        print(f"  ✅ 预设分镜: {sum(len(e['shots']) for e in storyboard_data['episodes'])} 个")

    # 3. CharacterAgent
    print("\n[3/7] CharacterAgent - 角色定妆照")
    agent3 = CharacterDesignAgent()
    result3 = await agent3.run(script_data)
    print(f"  ✅ 角色: 生成{result3.metadata['generated']}, 跳过{result3.metadata['skipped']}")
    char_assets = {
        c["name"]: c.get("asset", {})
        for c in result3.data.get("characters", [])
    }

    # 4. ImageGenAgent
    print("\n[4/7] ImageGenAgent - 批量出图")
    agent4 = ImageGenAgent()
    result4 = await agent4.run(storyboard_data, char_assets)
    image_count = result4.metadata.get("total_images", 0)
    print(f"  ✅ 出图: {image_count} 张")

    # 5. VideoGenAgent
    print("\n[5/7] VideoGenAgent - 图生视频")
    agent5 = VideoGenAgent()
    result5 = await agent5.run(result4)
    video_count = result5.metadata.get("total_videos", 0)
    print(f"  ✅ 视频: {video_count} 段")

    # 6. SubtitleAgent
    print("\n[6/7] SubtitleAgent - 字幕生成")
    agent6 = SubtitleAgent()
    result6 = await agent6.run(script_data, storyboard_data)
    print(f"  ✅ 字幕: {result6.metadata['files']} 个文件")

    # 7. ComposeAgent (会 FFmpeg 合成)
    print("\n[7/7] ComposeAgent - 视频合成")
    agent7 = ComposeAgent()
    result7 = await agent7.run(result5, result6)
    published = result7.metadata.get("episodes", 0)
    if published > 0:
        print(f"  ✅ 合成: {published} 集完成")
    else:
        print(f"  ⚠️ 合成跳过（Mock视频无真实文件）")

    # 汇总
    print("\n" + "=" * 60)
    print("Phase 1 管线验证完成 ✅")
    print(f"Mock 文件输出: storage/output/")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
