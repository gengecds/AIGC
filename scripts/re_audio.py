"""手动对现有成片重新合成增强音频（复用 audio_agent，不重跑全管线）

用法：python scripts/re_audio.py
基于 storage/output/ep_1_with_sub.mp4（无声成片）重新合成：
情绪 BGM + 场景音效 + 配音 + 更高音量 → ep_1_with_sub_audio.mp4
"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from agents.audio_agent import AudioAgent


class SimpleResult:
    """轻量 AgentResult 容器（只带 data）"""

    def __init__(self, data):
        self.data = data


async def main():
    out_dir = Path("storage/output")
    srt_path = out_dir / "ep_1.srt"
    video_path = out_dir / "ep_1_with_sub.mp4"
    if not video_path.exists() or not srt_path.exists():
        print("缺少成片或字幕:", video_path, srt_path)
        return

    # compose_result：指向现有无声成片
    compose = SimpleResult({"published": [{"final_path": str(video_path)}]})
    # script_result：空 → 走字幕提取对白
    script = SimpleResult({})
    # subtitle_result：直接放 SRT 文本（_extract_from_subtitle 遍历 data.values 字符串）
    srt_text = srt_path.read_text(encoding="utf-8")
    subtitle = SimpleResult({"srt": srt_text})
    # 制作方案：锅贴广告温馨调 + 场景音效
    plan = {
        "bgm_mood": "温馨",
        "scene_sounds": ["煎锅滋滋", "车流"],
    }
    # 分镜断点（可选）：读入后可按镜头角色/说话人分配音色，让配音与人物对得上
    storyboard = None
    sb_path = Path("storage/checkpoints_real/storyboard_agent_checkpoint.json")
    if sb_path.exists():
        storyboard = SimpleResult(json.loads(sb_path.read_text(encoding="utf-8")).get("data") or {})

    agent = AudioAgent()
    result = await agent.run(compose, script, subtitle, plan,
                             storyboard_result=storyboard, voice_plan=None)
    if result.success and result.data.get("final_video"):
        print("✓ 增强音频成片:", result.data["final_video"])
        print("  配音:", len(result.data.get("voices", [])), "句")
        print("  时长:", result.data.get("duration"))
    else:
        print("✗ 失败:", result.error)


if __name__ == "__main__":
    asyncio.run(main())
