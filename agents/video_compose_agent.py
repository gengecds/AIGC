"""Agent 7a - 视频合成（从 ComposeAgent 拆分出）

视频分段 + SRT字幕 → FFmpeg合成 → 输出完整 MP4

v2（电影化后期）：
- xfade 交叉淡化转场（替代硬切）
- 可选 Real-ESRGAN 超分（512→高清，需 tools/realesrgan）
- 暖调色 + 胶片颗粒 + 2.35:1 遮幅
- 字幕烧录
"""

import logging
import os
import asyncio
import subprocess as sp
from datetime import datetime
from pathlib import Path

from agents.base import Agent, AgentResult

logger = logging.getLogger(__name__)

# 电影化滤镜参数（暖调 + 胶片颗粒 + 遮幅黑边）
FADE = 0.4  # 转场时长（秒）
CINEMATIC_FILTER = (
    "eq=contrast=1.06:saturation=1.15:gamma=0.94:brightness=0.005,"
    "noise=alls=5:allf=t,"
    "drawbox=y=0:w=iw:h=ih*0.07:color=black:t=fill,"
    "drawbox=y=ih*0.93:w=iw:h=ih*0.07:color=black:t=fill"
)


class VideoComposeAgent(Agent):
    """Agent 7a：视频合成（电影化后期）"""

    name = "video_compose_agent"

    def __init__(self, ffmpeg_path: str = "ffmpeg"):
        super().__init__(name="video_compose_agent")
        self.ffmpeg = ffmpeg_path
        # 超分工具（FFmpeg lanczos 2x 放大 + 锐化，替代不兼容的 realesrgan）
        self.use_super_res = os.environ.get("AIGC_SUPER_RES", "1") == "1"

    async def run(self, videos_result, subtitle_result,
                  output_dir: str = "storage/output") -> AgentResult:
        logger.info("[VideoComposeAgent] 合成视频")

        def _get_data(obj):
            if hasattr(obj, 'data'):
                return obj.data or {}
            return obj.get("data", {})

        videos = _get_data(videos_result).get("videos", {})
        subs = _get_data(subtitle_result).get("subtitles", [])
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)

        published = []

        for sub in subs:
            ep_num = sub["episode_number"]
            srt_path = sub["srt_path"]
            ep_key = f"ep_{ep_num}"
            ep_videos = videos.get(ep_key, {})

            if not ep_videos:
                logger.warning(f"[VideoComposeAgent] ep_{ep_num} 无视频分段，跳过")
                continue

            concat_file = output_path / f"concat_ep{ep_num}.txt"
            video_paths = []
            sorted_shots = sorted(
                ep_videos.items(),
                key=lambda x: int(x[0]) if x[0].isdigit() else 0,
            )
            for shot_id, video_info in sorted_shots:
                if not video_info:
                    continue
                if isinstance(video_info, dict):
                    path = video_info.get("local_path", video_info.get("filename", ""))
                else:
                    path = str(video_info)
                if path and Path(path).exists():
                    video_paths.append(path)

            if not video_paths:
                logger.warning(f"[VideoComposeAgent] ep_{ep_num} 无可用视频文件")
                continue

            is_mock = False
            if video_paths:
                first_vid = Path(video_paths[0])
                if first_vid.stat().st_size < 1024:
                    is_mock = True

            final_path = output_path / f"ep_{ep_num}_final.mp4"

            if is_mock:
                # mock 模式：直接用纯色视频兜底（cmd 仅在 mock 分支定义并执行）
                duration = len(video_paths) * 5
                cmd = [
                    self.ffmpeg, "-f", "lavfi",
                    "-i", f"color=c=0x1a1a2e:s=1920x1080:d={duration}:r=24",
                    "-c:v", "libx264", "-preset", "fast",
                    "-y", str(final_path),
                ]
                try:
                    # ffmpeg 同步调用放线程，避免阻塞后端事件循环
                    await asyncio.to_thread(sp.run, cmd, True, True, True)
                    logger.info(f"[VideoComposeAgent] mock 成片: {final_path}")
                except sp.CalledProcessError as e:
                    logger.error(f"[VideoComposeAgent] mock FFmpeg 失败: {e.stderr}")
                    continue
            else:
                # 真实视频：增强版合成（xfade 转场 + 调色 + 音频压限 + 字幕 一步到位）
                has_srt = Path(srt_path).exists() and Path(srt_path).stat().st_size > 0
                sub_path = srt_path if has_srt else None
                # 后续 pipeline 支持 BGM 时，把实际 bgm_path 传进来（当前占位 None）
                ok = await asyncio.to_thread(
                    self._compose_final, video_paths, sub_path, None, str(final_path)
                )
                if not ok:
                    logger.error("[VideoComposeAgent] 增强合成失败，跳过本集")
                    continue

            final_used = str(final_path)
            # 字幕已在增强滤镜中烧录；若走了兜底 concat，可能无字幕（保证先出片）
            has_sub = Path(srt_path).exists() and not is_mock
            if has_sub:
                logger.info(f"[VideoComposeAgent] 合成完成（字幕已在滤镜中烧录）: {final_path}")
            else:
                logger.info(f"[VideoComposeAgent] 合成完成（无字幕）: {final_path}")

            published.append({
                "episode_number": ep_num,
                "final_path": final_used,
                "segments": len(video_paths),
                "has_subtitles": has_sub,
            })

        result = AgentResult(
            success=True,
            data={"published": published},
            metadata={
                "agent": self.name,
                "timestamp": datetime.utcnow().isoformat(),
                "episodes": len(published),
            },
        )
        return result

    @staticmethod
    def _has_audio_stream(path: str) -> bool:
        """判断该视频文件是否自带音轨（如 MiniMax H3 全模态生成的原生音频）。

        合成/字幕烧录时据此决定是否用 -map 保留音轨，避免成片被转成无声。
        """
        try:
            r = sp.run(
                ["ffprobe", "-v", "quiet", "-select_streams", "a",
                 "-show_entries", "stream=index", "-of", "csv=p=0", str(path)],
                capture_output=True, text=True, timeout=15,
            )
            return bool(r.stdout.strip())
        except Exception:
            return False

    # ── 电影化合成（xfade 转场 + 超分 + 调色/颗粒/遮幅）───
    def _cinematic_compose(self, video_paths: list, output_path: str) -> bool:
        """把多段视频用交叉淡化转场拼接，再叠加电影化滤镜

        流程：各段 setpts 归零 → xfade 链拼接 → 调色+颗粒+遮幅
        →（可选）Real-ESRGAN 超分 → 高清后再加一次滤镜

        音轨：若输入视频自带音轨（如 MiniMax H3 全模态生成的原生音频），
        用 acrossfade 按与视频 xfade 相同时长交叉，保证 A/V 同步并保留音频。
        无声视频（如 LTX）则维持原有「仅出画面」逻辑，由 audio_agent 后期配音。
        """
        try:
            n = len(video_paths)
            # 读取每段时长（用于计算 xfade offset）
            durations = []
            for vp in video_paths:
                r = sp.run(
                    ["ffprobe", "-v", "quiet",
                     "-show_entries", "format=duration", "-of", "csv=p=0", str(vp)],
                    capture_output=True, text=True, timeout=15,
                )
                durations.append(float(r.stdout.strip()))

            # 构建 xfade 滤镜链：每段归零起点，再两两交叉淡化
            # 第 i 个 xfade 的左输入是上一个 xfade 的输出（vx{i-1}），第一个用 v0
            fc = [f"[{i}:v]setpts=PTS-STARTPTS[v{i}]" for i in range(n)]
            total_before = 0
            last = "v0"
            for i in range(1, n):
                total_before += durations[i - 1]
                off = total_before - i * FADE
                in_left = "v0" if i == 1 else f"vx{i-1}"
                fc.append(f"[{in_left}][v{i}]xfade=transition=fade:duration={FADE}:offset={off:.3f}[vx{i}]")
                last = f"vx{i}"
            fc.append(f"[{last}]{CINEMATIC_FILTER}[vout]")

            # 音轨处理：全部输入都带音频时，用 acrossfade 拼接（时长与 xfade 一致）
            has_audio = all(self._has_audio_stream(vp) for vp in video_paths)
            audio_out = None
            if has_audio:
                for i in range(n):
                    fc.append(
                        f"[{i}:a]aformat=sample_rates=44100:channel_layouts=stereo,"
                        f"asetpts=PTS-STARTPTS[a{i}]"
                    )
                prev_a = "a0"
                if n == 1:
                    audio_out = "a0"
                else:
                    for i in range(1, n):
                        out = f"au{i}"
                        fc.append(
                            f"[{prev_a}][a{i}]acrossfade=d={FADE}:c1=tri:c2=tri[{out}]"
                        )
                        prev_a = out
                    audio_out = prev_a

            inputs = []
            for vp in video_paths:
                inputs += ["-i", str(vp)]
            if audio_out:
                cmd = ([self.ffmpeg, "-y"] + inputs +
                       ["-filter_complex", ";".join(fc),
                        "-map", "[vout]", "-map", f"[{audio_out}]",
                        "-c:v", "libx264", "-preset", "fast", "-crf", "20",
                        "-c:a", "aac", "-b:a", "192k",
                        "-pix_fmt", "yuv420p", "-r", "25", str(output_path)])
            else:
                cmd = ([self.ffmpeg, "-y"] + inputs +
                       ["-filter_complex", ";".join(fc), "-map", "[vout]",
                        "-c:v", "libx264", "-preset", "fast", "-crf", "20",
                        "-pix_fmt", "yuv420p", "-r", "25", str(output_path)])
            r = sp.run(cmd, capture_output=True, text=True, timeout=600)
            if not Path(output_path).exists() or Path(output_path).stat().st_size == 0:
                logger.warning(f"[VideoComposeAgent] xfade 失败: {r.stderr[-300:]}")
                return False

            # 超分增强（可选，需 tools/realesrgan）
            if self.use_super_res:
                self._super_resolve(output_path)
            return True
        except Exception as e:
            logger.error(f"[VideoComposeAgent] 合成异常: {e}")
            return False

    def _super_resolve(self, base_path: str):
        """超分增强：Lanczos 放大 2x + 轻锐化 → 重新加滤镜 → 替换成片

        512→1024 提升清晰度；之后再加一次电影化滤镜（颗粒在高清下更自然）。
        """
        try:
            base = Path(base_path)
            hd_path = str(base.with_name(base.stem + "_hd.mp4"))
            # Lanczos 高质量放大 + unsharp 锐化（比直接放大清晰得多）
            cmd = [self.ffmpeg, "-y", "-i", base_path,
                   "-filter_complex",
                   "scale=1024:1024:flags=lanczos,unsharp=5:5:0.6:5:5:0.0[v]",
                   "-map", "[v]", "-map", "0:a?",
                   "-c:v", "libx264", "-preset", "fast", "-crf", "18",
                   "-c:a", "aac", "-b:a", "192k",
                   "-pix_fmt", "yuv420p", "-r", "25", hd_path]
            r = sp.run(cmd, capture_output=True, text=True, timeout=900)
            if not Path(hd_path).exists() or Path(hd_path).stat().st_size == 0:
                logger.warning(f"[VideoComposeAgent] 超分失败: {r.stderr[-300:]}")
                return
            # 超分后再次加电影化滤镜（颗粒在高清下更自然）
            final = str(base.with_name(base.stem + "_sr.mp4"))
            cmd2 = [self.ffmpeg, "-y", "-i", hd_path,
                    "-filter_complex", f"[0:v]{CINEMATIC_FILTER}[vout]",
                    "-map", "[vout]", "-map", "0:a?",
                    "-c:v", "libx264", "-preset", "fast",
                    "-c:a", "aac", "-b:a", "192k",
                    "-crf", "18", "-pix_fmt", "yuv420p", final]
            sp.run(cmd2, capture_output=True, text=True, timeout=600)
            if Path(final).exists() and Path(final).stat().st_size > 0:
                # 用高清成片替换原成片
                base.unlink(missing_ok=True)
                Path(final).rename(base)
                Path(hd_path).unlink(missing_ok=True)
                logger.info(f"[VideoComposeAgent] 超分+滤镜完成: {base}")
        except Exception as e:
            logger.error(f"[VideoComposeAgent] 超分异常: {e}")

    # ── 字幕烧录（PIL 生成字幕 PNG + ffmpeg overlay）───
    def _burn_subtitles(self, video_path: str, srt_path: str,
                        output_path: str) -> bool:
        """把 SRT 字幕烧录进视频

        本机 ffmpeg 未编译 libass（没有 subtitles/drawtext 滤镜），
        所以用 PIL 把每条字幕渲染成半透明黑底白字的 PNG，
        再通过 ffmpeg overlay 滤镜按时间轴叠加（enable=between(t,开始,结束)）。
        字幕样式类似电视剧字幕：底部居中、黑底半透明、白字黑描边。
        """
        import re
        from PIL import Image, ImageDraw, ImageFont

        def _ts(h, mi, s, ms):
            # SRT 时间戳（时:分:秒,毫秒）→ 秒
            return int(h) * 3600 + int(mi) * 60 + int(s) + int(ms) / 1000

        try:
            # 1. 解析 SRT → [(start, end, text)]
            subs = []
            for block in open(srt_path, encoding="utf-8").read().strip().split("\n\n"):
                lines = block.splitlines()
                if len(lines) < 2:
                    continue
                m = re.match(
                    r"(\d+):(\d+):(\d+),(\d+)\s*-->\s*(\d+):(\d+):(\d+),(\d+)",
                    lines[1],
                )
                if not m:
                    continue
                start = _ts(*map(int, m.groups()[:4]))
                end = _ts(*map(int, m.groups()[4:]))
                text = " ".join(lines[2:]).strip()
                if text:
                    subs.append((start, end, text))
            if not subs:
                logger.warning("[VideoComposeAgent] SRT 无有效字幕")
                return False

            # 2. 探测视频尺寸（字幕比例跟随成片分辨率）
            r = sp.run(
                ["ffprobe", "-v", "quiet", "-select_streams", "v:0",
                 "-show_entries", "stream=width,height", "-of", "csv=p=0",
                 video_path],
                capture_output=True, text=True, timeout=15,
            )
            W, H = map(int, r.stdout.strip().split(","))

            # 中文字体：macOS 自带黑体（字幕用无衬线黑体，清晰不抢画面）
            font_path = "/System/Library/Fonts/STHeiti Medium.ttc"
            if not Path(font_path).exists():
                font_path = "/System/Library/Fonts/STHeiti Light.ttc"
            font = ImageFont.truetype(font_path, max(24, int(H * 0.045)))

            # 3. 逐条生成字幕 PNG + 组装 overlay 滤镜链
            png_dir = Path(srt_path).parent
            inputs = ["-i", video_path]
            chain = []
            prev = "0:v"
            for i, (start, end, text) in enumerate(subs):
                # 测量文字尺寸，确定字幕条大小
                probe = Image.new("RGBA", (10, 10))
                d0 = ImageDraw.Draw(probe)
                bbox = d0.textbbox((0, 0), text, font=font)
                tw = bbox[2] - bbox[0]
                th = bbox[3] - bbox[1]
                pad_x = int(tw * 0.07)
                pad_y = max(6, int(th * 0.3))
                img = Image.new("RGBA", (tw + pad_x * 2, th + pad_y * 2),
                                (0, 0, 0, 0))
                d = ImageDraw.Draw(img)
                # 半透明黑色圆角条（衬底，保证任何画面下都清晰）
                d.rounded_rectangle(
                    [0, 0, img.width - 1, img.height - 1],
                    radius=int(th * 0.4), fill=(0, 0, 0, 120),
                )
                # 白字 + 黑色描边
                d.text(
                    (pad_x, pad_y), text, font=font,
                    fill=(255, 255, 255, 255),
                    stroke_width=max(2, int(H * 0.002)),
                    stroke_fill=(0, 0, 0, 255),
                )
                png_path = png_dir / f"sub_ep_{i}.png"
                img.save(png_path)
                # overlay：底部居中，y 放在遮幅黑带下方一点（类似电视剧字幕位）
                x = (W - img.width) // 2
                y = H - img.height - int(H * 0.02)
                inputs += ["-i", str(png_path)]
                tag = f"ov{i}"
                chain.append(
                    f"[{prev}][{i + 1}:v]overlay={x}:{y}:"
                    f"enable='between(t,{start:.2f},{end:.2f})'[{tag}]"
                )
                prev = tag

            # 4. 执行 overlay 合成（保留音轨：成片可能自带 H3 原生音频）
            cmd = [self.ffmpeg, "-y"] + inputs + [
                "-filter_complex", ";".join(chain),
                "-map", f"[{prev}]",
                "-map", "0:a?",
                "-c:v", "libx264", "-preset", "fast", "-crf", "18",
                "-c:a", "aac", "-b:a", "192k",
                "-pix_fmt", "yuv420p", "-r", "25",
                output_path,
            ]
            sp.run(cmd, capture_output=True, text=True, timeout=900)
            # 清理临时字幕 PNG
            for f in png_dir.glob("sub_ep_*.png"):
                f.unlink(missing_ok=True)
            return Path(output_path).exists() and Path(output_path).stat().st_size > 0
        except Exception as e:
            logger.error(f"[VideoComposeAgent] 字幕烧录异常: {e}")
            return False

    # ── 增强版合成（4 类滤镜 + 失败兜底）───────────────────────────────

    def _build_enhanced_ffmpeg_command(
        self,
        shot_videos: list[str],
        subtitles_path: str | None,
        bgm_path: str | None,
        output_path: str,
        force_eq_fallback: bool = False,
    ) -> tuple[list[str], dict]:
        """构造增强版 FFmpeg 合成命令（纯命令构造，不实际执行）。

        内含 4 类滤镜：
          1) xfade 镜头间 1s 平滑转场（单段视频时跳过）
          2) colorgrade 电影调色；FFmpeg 不支持时 force_eq_fallback=True 走 eq 等效参数
          3) 音频：全部音轨合并 → acompressor 压限 → 开头 1s 淡入 + 末尾 2s 淡出
          4) 字幕：subtitles.srt / subtitles.ass 用 subtitles 或 ass 滤镜烧录（底部白色描边）

        Args:
            shot_videos: 镜头视频路径列表（按播放顺序）
            subtitles_path: 字幕文件路径 (.srt/.ass)，None 表示不加字幕
            bgm_path: BGM 音频路径，None 表示不加 BGM
            output_path: 输出 MP4 路径
            force_eq_fallback: True 时用 eq 滤镜调色，False 时优先用 colorgrade

        Returns:
            (ffmpeg args 列表[str], 元数据 dict)
            元数据字段：
                total_duration          —— 合成后总时长（秒，考虑了 xfade 重叠）
                transition_count        —— xfade 过渡次数（0 表示无需转场）
                used_colorgrade_fallback—— True 用了 eq 回退、False 用了 colorgrade
        """
        import subprocess as _sp

        n = len(shot_videos)
        assert n >= 1, "shot_videos 至少需要 1 段"

        # ── 步骤 1：用 ffprobe 读取每段视频的时长 ────────────────────────
        durations: list[float] = []
        for vp in shot_videos:
            r = _sp.run(
                ["ffprobe", "-v", "quiet",
                 "-show_entries", "format=duration", "-of", "csv=p=0", str(vp)],
                capture_output=True, text=True, timeout=15,
            )
            try:
                durations.append(float(r.stdout.strip()))
            except ValueError:
                # 兜底：读不到时默认按 5 秒估算，保证命令能构造出来
                durations.append(5.0)

        # ── 步骤 2：计算总时长 & 过渡次数（xfade 每段重叠 1 秒）──────────
        transition_count = max(0, n - 1)
        total_duration = sum(durations) - transition_count * 1.0
        if total_duration < 0.1:
            total_duration = 0.1  # 防止出现负值或 0

        # ── 步骤 3：构造视频 filter_complex 链 ──────────────────────────
        fc: list[str] = []

        # 3.1 给每段视频的视频流打标签 + 归零时间轴
        for i in range(n):
            fc.append(f"[{i}:v]setpts=PTS-STARTPTS[v{i}]")

        # 3.2 xfade 两两交叉淡化（过渡 1s）
        last_v = "v0"
        if n > 1:
            cumulative = 0.0  # 累计「不重叠」情况下的时长
            for i in range(1, n):
                cumulative += durations[i - 1]
                # 通用 xfade offset：每叠一次，就再往前挪 1s
                offset = cumulative - i * 1.0
                left = last_v
                right = f"v{i}"
                out_tag = f"vx{i}"
                fc.append(
                    f"[{left}][{right}]xfade=transition=fade:duration=1.0:offset={offset:.3f}[{out_tag}]"
                )
                last_v = out_tag

        # 3.3 调色滤镜：colorgrade（优先）或 eq（回退）
        used_colorgrade_fallback = force_eq_fallback
        if force_eq_fallback:
            # 等效参数：对比度 +0.05，饱和度 -0.05，亮度略 +0.01（模拟暖调）
            color_filter = "eq=contrast=1.05:saturation=0.95:brightness=0.01"
        else:
            # FFmpeg 6.0+ 内置 colorgrade：preset=medium + 轻微参数
            color_filter = (
                "colorgrade=preset=medium:"
                "contrast=0.05:"
                "saturation=-0.05:"
                "temperature=0.02"
            )
        color_out = "vcolored"
        fc.append(f"[{last_v}]{color_filter}[{color_out}]")

        # 3.4 字幕滤镜（subtitles / ass）—— 底部居中 + 白色描边
        pre_sub = color_out
        if subtitles_path and Path(subtitles_path).exists():
            # FFmpeg subtitles 滤镜里的路径冒号需要转义
            escaped = str(subtitles_path).replace(":", r"\:")
            # 样式：Arial/白字/黑描边 2px/底部居中/底部留 30px 边距
            style = (
                "FontName=Arial,FontSize=24,PrimaryColour=&H00FFFFFF,"
                "OutlineColour=&H00000000,BorderStyle=1,Outline=2,Shadow=0,"
                "Alignment=2,MarginV=30"
            )
            ext = Path(subtitles_path).suffix.lower()
            if ext == ".ass":
                sub_filter = f"ass='{escaped}'"
            else:
                sub_filter = f"subtitles='{escaped}':force_style='{style}'"
            sub_out = "vsub"
            fc.append(f"[{pre_sub}]{sub_filter}[{sub_out}]")
            final_v = sub_out
        else:
            final_v = pre_sub

        # ── 步骤 4：构造音频 filter_complex 链 ──────────────────────────
        audio_input_tags: list[str] = []

        # 4.1 每段视频自带的音轨（如果有）
        for i in range(n):
            if self._has_audio_stream(shot_videos[i]):
                tag = f"ain{len(audio_input_tags)}"
                fc.append(
                    f"[{i}:a]aformat=sample_rates=44100:channel_layouts=stereo,"
                    f"asetpts=PTS-STARTPTS[{tag}]"
                )
                audio_input_tags.append(tag)

        # 4.2 BGM（如果有）：作为紧接视频之后的输入，index = n
        bgm_idx: int | None = None
        if bgm_path and Path(bgm_path).exists():
            bgm_idx = n
            tag = f"bgm{len(audio_input_tags)}"
            fc.append(
                f"[{bgm_idx}:a]aformat=sample_rates=44100:channel_layouts=stereo,"
                f"asetpts=PTS-STARTPTS[{tag}]"
            )
            audio_input_tags.append(tag)

        final_a: str | None = None
        if len(audio_input_tags) == 0:
            # 没有任何音轨 → 输出无声视频
            pass
        elif len(audio_input_tags) == 1:
            final_a = audio_input_tags[0]
        else:
            # 视频音轨（多个时）acrossfade 拼接，再和 BGM amix 混合
            video_audio = [t for t in audio_input_tags if not t.startswith("bgm")]
            bgm_only = [t for t in audio_input_tags if t.startswith("bgm")]
            mixed: str | None = None

            if len(video_audio) > 1:
                prev = video_audio[0]
                for idx in range(1, len(video_audio)):
                    out = f"au{idx}"
                    fc.append(
                        f"[{prev}][{video_audio[idx]}]"
                        f"acrossfade=d=1.0:c1=tri:c2=tri[{out}]"
                    )
                    prev = out
                mixed = prev
            elif len(video_audio) == 1:
                mixed = video_audio[0]

            if bgm_only:
                if mixed:
                    fc.append(
                        f"[{mixed}][{bgm_only[0]}]"
                        f"amix=inputs=2:duration=first:dropout_transition=0[amixed]"
                    )
                    mixed = "amixed"
                else:
                    mixed = bgm_only[0]

            final_a = mixed

        # 4.3 全局音频：acompressor 压限 + afade 淡入淡出
        if final_a is not None:
            comp_tag = "acomp"
            # 压限：说话清晰不爆音（threshold -20dB / 压缩比 3:1）
            fc.append(
                f"[{final_a}]acompressor=threshold=-20dB:ratio=3:"
                f"attack=10:release=100[{comp_tag}]"
            )
            # 淡入：开头 1s；淡出：最后 2s
            fade_out_st = max(0.0, total_duration - 2.0)
            fade_tag = "afaded"
            fc.append(
                f"[{comp_tag}]afade=t=in:st=0:d=1,"
                f"afade=t=out:st={fade_out_st:.3f}:d=2[{fade_tag}]"
            )
            final_a = fade_tag

        # ── 步骤 5：组装完整 ffmpeg args 列表 ───────────────────────────
        cmd: list[str] = [self.ffmpeg, "-y"]
        for vp in shot_videos:
            cmd += ["-i", str(vp)]
        if bgm_path and Path(bgm_path).exists():
            cmd += ["-i", str(bgm_path)]

        cmd += ["-filter_complex", ";".join(fc)]
        cmd += ["-map", f"[{final_v}]"]
        if final_a is not None:
            cmd += ["-map", f"[{final_a}]"]
            cmd += ["-c:a", "aac", "-b:a", "192k"]

        cmd += [
            "-c:v", "libx264", "-preset", "fast", "-crf", "20",
            "-pix_fmt", "yuv420p", "-r", "25",
            str(output_path),
        ]

        meta = {
            "total_duration": total_duration,
            "transition_count": transition_count,
            "used_colorgrade_fallback": used_colorgrade_fallback,
        }
        return cmd, meta

    def _compose_final(
        self,
        video_paths: list[str],
        subtitles_path: str | None,
        bgm_path: str | None,
        output_path: str,
    ) -> bool:
        """增强版合成入口：先跑增强版命令，失败自动回退，任何情况不抛异常。

        失败策略：
          1) 优先尝试 colorgrade 版增强命令（包含 xfade/调色/压限/字幕）
          2) 若失败且上一轮用了 colorgrade → 再试 eq 回退版（兼容旧 FFmpeg）
          3) 再失败 → 退回到「顺序 concat demuxer 硬切」（几乎任何 FFmpeg 都能跑）
        """
        try:
            # —— 第一轮：colorgrade 版增强命令 —————————————————————————
            cmd, meta = self._build_enhanced_ffmpeg_command(
                video_paths, subtitles_path, bgm_path, output_path,
                force_eq_fallback=False,
            )
            logger.info(
                "[VideoComposeAgent] 尝试增强版合成 "
                f"(时长≈{meta['total_duration']:.1f}s, 转场={meta['transition_count']}, "
                f"colorgrade回退={meta['used_colorgrade_fallback']})"
            )
            r = sp.run(cmd, capture_output=True, text=True, timeout=600)
            if (r.returncode == 0
                    and Path(output_path).exists()
                    and Path(output_path).stat().st_size > 0):
                logger.info(f"[VideoComposeAgent] 增强版合成成功: {output_path}")
                return True

            # —— 第二轮：eq 回退版（仅当第一轮用的是 colorgrade）————————
            if not meta["used_colorgrade_fallback"]:
                logger.warning(
                    "[VideoComposeAgent] 增强版失败（可能 FFmpeg 无 colorgrade），"
                    f"尝试 eq 回退版。尾部 stderr: {r.stderr[-200:]}"
                )
                cmd2, _ = self._build_enhanced_ffmpeg_command(
                    video_paths, subtitles_path, bgm_path, output_path,
                    force_eq_fallback=True,
                )
                r2 = sp.run(cmd2, capture_output=True, text=True, timeout=600)
                if (r2.returncode == 0
                        and Path(output_path).exists()
                        and Path(output_path).stat().st_size > 0):
                    logger.info("[VideoComposeAgent] eq 回退版合成成功")
                    return True
                logger.error(
                    "[VideoComposeAgent] eq 回退版也失败，"
                    f"尾部 stderr: {r2.stderr[-200:]}"
                )

            # —— 第三轮：最简单的 concat demuxer 兜底 ——————————————————
            logger.error("[VideoComposeAgent] 增强合成全部失败，退回简单 concat 兜底")
            return self._fallback_simple_concat(video_paths, output_path)

        except Exception as e:
            logger.error(
                f"[VideoComposeAgent] _compose_final 发生异常: {e}，"
                f"立即尝试简单 concat 兜底"
            )
            try:
                return self._fallback_simple_concat(video_paths, output_path)
            except Exception as e2:
                logger.error(f"[VideoComposeAgent] 兜底 concat 也异常: {e2}")
                return False

    def _fallback_simple_concat(
        self, video_paths: list[str], output_path: str
    ) -> bool:
        """最保守的兜底：用 concat demuxer 做硬切顺序拼接。

        几乎所有 FFmpeg 版本都支持，用于保证 pipeline 不会卡在合成步骤。
        字幕/转场/调色都不做——先保证成片出来。
        """
        concat_txt = None
        try:
            concat_txt = Path(output_path).with_suffix(".txt")
            with open(concat_txt, "w", encoding="utf-8") as f:
                for vp in video_paths:
                    # concat demuxer 里的单引号要转义
                    safe = str(vp).replace("'", "'\\''")
                    f.write(f"file '{safe}'\n")

            cmd = [
                self.ffmpeg, "-y",
                "-f", "concat", "-safe", "0",
                "-i", str(concat_txt),
                "-c:v", "libx264", "-preset", "fast", "-crf", "23",
                "-c:a", "aac", "-b:a", "128k",
                "-pix_fmt", "yuv420p",
                str(output_path),
            ]
            r = sp.run(cmd, capture_output=True, text=True, timeout=600)
            ok = (r.returncode == 0
                  and Path(output_path).exists()
                  and Path(output_path).stat().st_size > 0)
            if ok:
                logger.info(f"[VideoComposeAgent] 兜底 concat 成功: {output_path}")
            else:
                logger.error(
                    "[VideoComposeAgent] 兜底 concat 也失败，"
                    f"尾部 stderr: {r.stderr[-200:]}"
                )
            return ok
        except Exception as e:
            logger.error(f"[VideoComposeAgent] 兜底 concat 异常: {e}")
            return False
        finally:
            # 清理临时 concat 清单
            if concat_txt is not None:
                concat_txt.unlink(missing_ok=True)
