"""TDD smoke test：验证 VideoComposeAgent._build_enhanced_ffmpeg_command 的命令构造正确性。

所有测试都用 mock 跳过真实文件/ffprobe 调用，**绝不真正执行 ffmpeg 生成视频**。
"""

import subprocess
import unittest
from subprocess import CompletedProcess
from unittest.mock import patch, MagicMock

# 把项目根加进 import path，这样可以直接 import agents
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.video_compose_agent import VideoComposeAgent


def _ffprobe_mock_side_effect(args, **kwargs):
    """根据传入的 ffprobe 命令，返回对应的假结果。

    负责两类 ffprobe 调用：
      - format=duration      → 返回 "5.0"（每段视频 5 秒）
      - select_streams a ... → 返回 "1"  （视频都带音轨）
    """
    cmd_str = " ".join(args)

    if "ffprobe" in cmd_str and "format=duration" in cmd_str:
        return CompletedProcess(args, returncode=0, stdout="5.0", stderr="")
    if "ffprobe" in cmd_str and "select_streams" in cmd_str:
        return CompletedProcess(args, returncode=0, stdout="1", stderr="")

    # 理论上本测试里不会触发非 ffprobe 的 sp.run
    raise RuntimeError(f"测试不允许执行真实命令，截获: {cmd_str}")


class TestBuildEnhancedFfmpegCommand(unittest.TestCase):
    """纯命令构造测试（不真正运行 FFmpeg）。"""

    def setUp(self):
        self.agent = VideoComposeAgent(ffmpeg_path="ffmpeg")
        # 统一 patch：Path.exists=True + subprocess.run 走上面的 ffprobe mock
        self._patchers = [
            patch(
                "pathlib.Path.exists",
                return_value=True,
            ),
            patch(
                "subprocess.run",
                side_effect=_ffprobe_mock_side_effect,
            ),
        ]
        for p in self._patchers:
            p.start()

    def tearDown(self):
        for p in reversed(self._patchers):
            p.stop()

    # ────────────────── TEST 1：3 段视频必须 4 类滤镜齐 ──────────────────

    def test_three_shots_all_four_filter_keywords(self):
        """给 3 段假视频 → args 里必须同时出现：
           xfade、(colorgrade 或 contrast=1.05)、acompressor、afade
        """
        fake_videos = [
            "/tmp/shot_1.mp4",
            "/tmp/shot_2.mp4",
            "/tmp/shot_3.mp4",
        ]
        args, meta = self.agent._build_enhanced_ffmpeg_command(
            shot_videos=fake_videos,
            subtitles_path=None,
            bgm_path=None,
            output_path="/tmp/out.mp4",
            force_eq_fallback=False,
        )

        # 把整个 args 拼成一个大字符串，方便用 in 做关键词断言
        flat = " ".join(args)

        # 1) 必须有 xfade 转场
        self.assertIn("xfade", flat, "3 段视频应该生成 xfade 转场滤镜")

        # 2) 必须有调色（colorgrade 或 eq fallback contrast=1.05）
        has_color = "colorgrade" in flat or "contrast=1.05" in flat
        self.assertTrue(has_color, "必须包含 colorgrade 或 eq(contrast=1.05) 调色")

        # 3) 必须有音频压限 acompressor
        self.assertIn("acompressor", flat, "必须包含 acompressor 音频压限")

        # 4) 必须有 afade 淡入淡出
        self.assertIn("afade", flat, "必须包含 afade 淡入淡出")

        # 元数据校验：过渡次数 = 3-1 = 2
        self.assertEqual(meta["transition_count"], 2)
        # 默认 force_eq_fallback=False → 没用 eq 回退
        self.assertEqual(meta["used_colorgrade_fallback"], False)
        # 总时长 ≈ 5*3 - 2*1 = 13 秒
        self.assertAlmostEqual(meta["total_duration"], 13.0, places=2)

    # ────────────────── TEST 2：1 段视频不能有 xfade ──────────────────

    def test_single_shot_no_xfade_but_other_filters(self):
        """给 1 段视频 → 不需要转场：args 里绝对不能出现 xfade，
        但必须有调色/acompressor/afade。
        """
        fake_videos = ["/tmp/only_shot.mp4"]
        args, meta = self.agent._build_enhanced_ffmpeg_command(
            shot_videos=fake_videos,
            subtitles_path=None,
            bgm_path=None,
            output_path="/tmp/out_single.mp4",
            force_eq_fallback=False,
        )
        flat = " ".join(args)

        # ⚠️ 单段 → 一定没有 xfade
        self.assertNotIn("xfade", flat, "单段视频不应该出现 xfade 转场")

        # 但其他三类滤镜必须有
        has_color = "colorgrade" in flat or "contrast=1.05" in flat
        self.assertTrue(has_color, "单段视频也必须有调色滤镜")
        self.assertIn("acompressor", flat, "单段视频也必须有 acompressor")
        self.assertIn("afade", flat, "单段视频也必须有 afade 淡入淡出")

        # 元数据校验
        self.assertEqual(meta["transition_count"], 0)
        self.assertAlmostEqual(meta["total_duration"], 5.0, places=2)

    # ────────────────── TEST 3：传 .srt 必须有 subtitles= ──────────────────

    def test_srt_path_adds_subtitles_filter(self):
        """传 fake .srt 路径 → args 里必须包含 subtitles= 关键词
        （形式可以是 subtitles=' 或 subtitles=xxx，只要有就行）。
        """
        fake_videos = ["/tmp/a.mp4", "/tmp/b.mp4"]
        fake_srt = "/tmp/fake_subtitles.srt"

        args, meta = self.agent._build_enhanced_ffmpeg_command(
            shot_videos=fake_videos,
            subtitles_path=fake_srt,
            bgm_path=None,
            output_path="/tmp/out_sub.mp4",
            force_eq_fallback=True,  # 顺便验证 eq fallback 的 contrast=1.05
        )
        flat = " ".join(args)

        # 必须出现 subtitles= （FFmpeg 滤镜入口）
        self.assertTrue(
            "subtitles=" in flat or "subtitles\\'" in flat,
            f"args 里应该包含 subtitles= 滤镜关键词，实际是: {flat[:300]}..."
        )

        # force_eq_fallback=True → 调色用的是 eq，且 contrast 应该是 1.05
        self.assertIn("contrast=1.05", flat, "eq fallback 必须含 contrast=1.05")
        self.assertEqual(meta["used_colorgrade_fallback"], True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
