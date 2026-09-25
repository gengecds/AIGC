"""Agent 7a - 视频合成（从 ComposeAgent 拆分出）

视频分段 + SRT字幕 → FFmpeg合成 → 输出完整 MP4

v2（电影化后期）：
- xfade 交叉淡化转场（替代硬切）
- 超分增强（`AIGC_SUPER_RES=1` 默认开：Lanczos 2x 放大 + 锐化，见 `_super_resolve`）
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

# xfade 交叉淡化转场时长（秒）。非末段要多留这么久和下一段重叠，video_agent
# 按"分镜时长 + 本值"决定生成帧数，改这里两边会自动对齐。
XFADE_TRANSITION = 1.0

# 电影化滤镜参数（暖调 + 胶片颗粒 + 遮幅黑边）
CINEMATIC_FILTER = (
    "eq=contrast=1.06:saturation=1.15:gamma=0.94:brightness=0.005,"
    "noise=alls=5:allf=t,"
    "drawbox=y=0:w=iw:h=ih*0.07:color=black:t=fill,"
    "drawbox=y=ih*0.93:w=iw:h=ih*0.07:color=black:t=fill"
)


def _probe_duration(path, stream: str = "v:0") -> float:
    """读取指定流（"v:0" 视频 / "a:0" 音频）的时长（秒），而不是容器时长。

    LTX 等 provider 的输出自带 AAC 音轨，容器时长(format=duration)通常比视频流长约
    0.2~0.3s。若拿容器时长当视频变速基准，算出的倍率偏小，每段会被裁短，xfade 处就会
    露出上一段的冻结末帧；同理音频变速必须用音频流自己的时长做基准。依次尝试：
      1) stream=duration（最准）
      2) nb_frames / avg_frame_rate（仅视频流，无 duration 元数据时）
      3) format=duration（兜底）
    """
    def _ffprobe(entries: str, use_stream: bool) -> str:
        cmd = ["ffprobe", "-v", "quiet"]
        if use_stream:
            cmd += ["-select_streams", stream]
        cmd += ["-show_entries", entries, "-of", "csv=p=0", str(path)]
        try:
            r = sp.run(cmd, capture_output=True, text=True, timeout=15)
        except Exception:
            return ""
        return (r.stdout or "").strip()

    raw = _ffprobe("stream=duration", True)
    try:
        v = float(raw.splitlines()[0])
        if v > 0.1:
            return v
    except (ValueError, IndexError):
        pass

    if stream.startswith("v"):
        lines = _ffprobe("stream=nb_frames,avg_frame_rate", True).splitlines()
        if len(lines) >= 2 and lines[0] not in ("", "N/A"):
            try:
                num, _, den = lines[1].partition("/")
                fps = float(num) / float(den) if float(den) else 0.0
                if float(lines[0]) > 0 and fps > 0:
                    return float(lines[0]) / fps
            except (ValueError, ZeroDivisionError):
                pass

    raw = _ffprobe("format=duration", False)
    try:
        v = float(raw.splitlines()[0])
        if v > 0.1:
            return v
    except (ValueError, IndexError):
        pass

    # 兜底：读不到时默认按 5 秒估算，保证命令能构造出来
    return 5.0


class VideoComposeAgent(Agent):
    """Agent 7a：视频合成（电影化后期）"""

    name = "video_compose_agent"

    def __init__(self, ffmpeg_path: str = "ffmpeg"):
        super().__init__(name="video_compose_agent")
        self.ffmpeg = ffmpeg_path
        # 超分工具（FFmpeg lanczos 2x 放大 + 锐化，替代不兼容的 realesrgan）
        self.use_super_res = os.environ.get("AIGC_SUPER_RES", "1") == "1"
        # colorgrade 滤镜 FFmpeg 6.0+ 才有；本机 9.0 也未必编译。启动探测一次，
        # 不支持就全程走 eq 回退，避免每集合成都要先白失败一轮才降级。
        self.use_colorgrade = self._ffmpeg_has_filter("colorgrade")
        if not self.use_colorgrade:
            logger.info("[VideoComposeAgent] FFmpeg 无 colorgrade，调色用 eq 等效参数")

    @staticmethod
    def _ffmpeg_has_filter(name: str) -> bool:
        """探测本机 ffmpeg 是否支持某个滤镜（如 colorgrade）。"""
        try:
            r = sp.run(
                ["ffmpeg", "-hide_banner", "-filters"],
                capture_output=True, text=True, timeout=10,
            )
            return f" {name} " in f" {r.stdout} " or f"\n{name}" in r.stdout
        except Exception:
            return False

    async def run(self, videos_result, subtitle_result,
                  output_dir: str = "storage/output",
                  storyboard_result=None) -> AgentResult:
        logger.info("[VideoComposeAgent] 合成视频")

        def _get_data(obj):
            if hasattr(obj, 'data'):
                return obj.data or {}
            return obj.get("data", {})

        videos = _get_data(videos_result).get("videos", {})
        subs = _get_data(subtitle_result).get("subtitles", [])
        # 分镜每镜时长：{episode_number: {shot_id: duration}}。
        # 成片按此拉伸/补齐，保证镜头时长与分镜（及 SRT）时间轴一致。
        shot_durations = self._shot_durations(_get_data(storyboard_result))
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
            target_durations = []  # 与 video_paths 一一对应的分镜目标时长（秒）
            ep_shot_durs = shot_durations.get(str(ep_num), {})
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
                    target_durations.append(ep_shot_durs.get(str(shot_id)))

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
                duration = sum(t for t in target_durations if t) or len(video_paths) * 5
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
                # 真实视频：增强版合成（xfade 转场 + 调色 + 音频压限）
                has_srt = Path(srt_path).exists() and Path(srt_path).stat().st_size > 0
                sub_path = srt_path if has_srt else None
                # 后续 pipeline 支持 BGM 时，把实际 bgm_path 传进来（当前占位 None）
                ok = await asyncio.to_thread(
                    self._compose_final, video_paths, sub_path, None, str(final_path),
                    target_durations,
                )
                if not ok:
                    logger.error("[VideoComposeAgent] 增强合成失败，跳过本集")
                    continue
                # 超分增强（AIGC_SUPER_RES=1 时启用）：放在烧字幕之前——字幕 PNG 按
                # 成片分辨率渲染后叠加，才不会被放大/颗粒二次处理糊掉文字。
                if self.use_super_res:
                    await asyncio.to_thread(self._super_resolve, str(final_path))
                # 字幕单独烧录：本机 ffmpeg 无 libass，不能走 subtitles 滤镜，
                # 增强滤镜链已不烧字幕；这里用 PIL 生成字幕 PNG + overlay 叠加。
                # 注意 _burn_subtitles 输入/输出不能同路径（ffmpeg 会自覆盖报错），
                # 先烧到临时文件，成功后再原子替换成片。
                if sub_path:
                    tmp_burned = output_path / f"ep_{ep_num}_sub_tmp.mp4"
                    burned = await asyncio.to_thread(
                        self._burn_subtitles, str(final_path), sub_path, str(tmp_burned)
                    )
                    if burned:
                        tmp_burned.replace(final_path)
                        has_sub = True
                    else:
                        tmp_burned.unlink(missing_ok=True)
                        logger.warning("[VideoComposeAgent] PIL 字幕烧录失败，保留无字幕成片")
                        has_sub = False
                else:
                    has_sub = False

            final_used = str(final_path)
            if has_sub:
                logger.info(f"[VideoComposeAgent] 合成完成（含烧录字幕）: {final_path}")
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
    def _shot_durations(storyboard: dict | None) -> dict[str, dict[str, float]]:
        """从分镜结果提取 {episode_number(str): {shot_id(str): duration}}。

        兼容传入的是 AgentResult.data 内层，或整包 dict。
        """
        out: dict[str, dict[str, float]] = {}
        if not isinstance(storyboard, dict):
            return out
        data = storyboard.get("data", storyboard) or {}
        for ep in data.get("episodes", []) or []:
            per: dict[str, float] = {}
            for shot in ep.get("shots", []) or []:
                try:
                    per[str(shot.get("shot_id"))] = float(shot.get("duration") or 0)
                except (TypeError, ValueError):
                    continue
            out[str(ep.get("episode_number", 1))] = per
        return out

    @staticmethod
    def _atempo_chain(factor: float) -> list[str]:
        """把变速系数拆成若干 0.5~2.0 的 atempo 串联（单次 atempo 有范围限制）。"""
        steps: list[str] = []
        f = factor
        while f > 2.0:
            steps.append("atempo=2.0")
            f /= 2.0
        while f < 0.5:
            steps.append("atempo=0.5")
            f /= 0.5
        steps.append(f"atempo={f:.4f}")
        return steps

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

    def _super_resolve(self, base_path: str) -> bool:
        """超分增强：Lanczos 2x 放大 + unsharp 锐化 + 电影化滤镜，原地替换成片。

        倍率用 iw*2:ih*2 而不是固定尺寸：出片是竖屏（LTX 默认 576x1024），写死尺寸
        会把画面拉成方块。放大/锐化/调色合并成一次编码（原来分两次），少一次中间
        文件、少一代画质损失。失败时保留原成片不动。
        """
        base = Path(base_path)
        tmp = base.with_name(base.stem + "_sr.mp4")
        try:
            cmd = [self.ffmpeg, "-y", "-i", base_path,
                   "-filter_complex",
                   f"[0:v]scale=iw*2:ih*2:flags=lanczos,"
                   f"unsharp=5:5:0.6:5:5:0.0,{CINEMATIC_FILTER}[v]",
                   "-map", "[v]", "-map", "0:a?",
                   "-c:v", "libx264", "-preset", "fast", "-crf", "18",
                   "-c:a", "aac", "-b:a", "192k",
                   "-pix_fmt", "yuv420p", "-r", "25", str(tmp)]
            r = sp.run(cmd, capture_output=True, text=True, timeout=1800)
            if not tmp.exists() or tmp.stat().st_size == 0:
                logger.warning(f"[VideoComposeAgent] 超分失败，保留原成片: {r.stderr[-300:]}")
                tmp.unlink(missing_ok=True)
                return False
            tmp.replace(base)
            logger.info(f"[VideoComposeAgent] 超分+滤镜完成（2x）: {base}")
            return True
        except Exception as e:
            logger.error(f"[VideoComposeAgent] 超分异常，保留原成片: {e}")
            tmp.unlink(missing_ok=True)
            return False

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
        target_durations: list[float] | None = None,
    ) -> tuple[list[str], dict]:
        """构造增强版 FFmpeg 合成命令（纯命令构造，不实际执行）。

        内含 4 类滤镜：
          1) 每段按分镜目标时长拉伸/补齐 + xfade 镜头间 1s 平滑转场（单段跳过）
          2) colorgrade 电影调色；FFmpeg 不支持时 force_eq_fallback=True 走 eq 等效参数
          3) 音频：全部音轨合并 → acompressor 压限 → 开头 1s 淡入 + 末尾 2s 淡出
          4) 字幕：subtitles.srt / subtitles.ass 用 subtitles 或 ass 滤镜烧录（底部白色描边）

        Args:
            shot_videos: 镜头视频路径列表（按播放顺序）
            subtitles_path: 字幕文件路径 (.srt/.ass)，None 表示不加字幕
            bgm_path: BGM 音频路径，None 表示不加 BGM
            output_path: 输出 MP4 路径
            force_eq_fallback: True 时用 eq 滤镜调色，False 时优先用 colorgrade
            target_durations: 与 shot_videos 一一对应的目标时长（分镜时长，秒）；
                提供后成片总时长≈Σ分镜时长，且每个镜头起始点与分镜/字幕时间轴一致

        Returns:
            (ffmpeg args 列表[str], 元数据 dict)
            元数据字段：
                total_duration          —— 合成后总时长（秒，考虑了 xfade 重叠）
                transition_count        —— xfade 过渡次数（0 表示无需转场）
                used_colorgrade_fallback—— True 用了 eq 回退、False 用了 colorgrade
        """
        n = len(shot_videos)
        assert n >= 1, "shot_videos 至少需要 1 段"

        # ── 步骤 1：读取每段视频的**视频流**时长（秒）────────────────────
        # 用视频流时长而非容器时长：容器还含 LTX 自带的 AAC 音轨，会长 0.2~0.3s，
        # 拿来当变速基准会让每段变短、xfade 处露出冻结末帧。
        durations: list[float] = [_probe_duration(vp) for vp in shot_videos]

        # ── 步骤 2：对齐分镜目标时长 + 计算总时长/过渡次数 ───────────────
        TRANSITION = XFADE_TRANSITION   # xfade 转场时长（秒）
        MIN_SPEED = 0.5    # 最快播放倍率（避免夸张快放）
        MAX_SLOW = 2.0     # 最慢播放倍率（避免夸张慢放）

        # 分镜时长是「镜头净可见时长」，但 xfade 每段会与下一段重叠 TRANSITION 秒。
        # 给非末段多留 TRANSITION 秒，则：
        #   总时长 = Σ(分镜时长 + TRANSITION) - (n-1)*TRANSITION = Σ分镜时长
        #   第 i 个镜头起点 = Σ前 i 个分镜时长（与 SRT 时间轴严格一致）
        targets: list[float] = []
        for i in range(n):
            t = None
            if target_durations and i < len(target_durations):
                try:
                    t = float(target_durations[i]) if target_durations[i] else None
                except (TypeError, ValueError):
                    t = None
            if t and t > 0.1:
                targets.append(t + (TRANSITION if i < n - 1 else 0.0))
            else:
                # 无分镜时长（如缺分镜结果）→ 沿用片段真实时长，行为与改造前一致
                targets.append(durations[i])

        transition_count = max(0, n - 1)
        total_duration = sum(targets) - transition_count * TRANSITION
        if total_duration < 0.1:
            total_duration = 0.1  # 防止出现负值或 0

        # ── 步骤 3：构造视频 filter_complex 链 ──────────────────────────
        fc: list[str] = []
        ratios: list[float] = []      # 每段变速倍率（>1 慢放 / <1 快放）
        trim_tos: list[float | None] = []  # 需要裁剪到的时长
        pads: list[float] = []        # 需要冻结末帧补齐的时长

        # 3.1 每段归零时间轴 + 按分镜时长拉伸/补齐
        for i in range(n):
            real = durations[i] if durations[i] > 0.1 else 0.1
            target = targets[i]
            ratio = target / real
            trim_to: float | None = None
            pad = 0.0
            if ratio < MIN_SPEED:
                ratio, trim_to = MIN_SPEED, target
            elif ratio > MAX_SLOW:
                ratio, pad = MAX_SLOW, max(0.0, target - real * MAX_SLOW)
            elif ratio > 1.0:
                pad = max(0.0, target - real * ratio)
            ratios.append(ratio)
            trim_tos.append(trim_to)
            pads.append(pad)

            seg = f"[{i}:v]setpts=PTS-STARTPTS"
            if abs(ratio - 1.0) > 1e-3:
                seg += f",setpts=PTS*{ratio:.6f}"
            if trim_to is not None:
                seg += f",trim=duration={trim_to:.3f},setpts=PTS-STARTPTS"
            if pad > 0.02:
                seg += f",tpad=stop_mode=clone:stop_duration={pad:.3f}"
            # ★ CFR 归一化：setpts=PTS*ratio 会让帧间隔不再均匀，这种非均匀 PTS 直接喂给
            # xfade（按时间戳做 alpha 混合）会在转场处插入 0.4~1.16s 的「保持帧」，
            # 表现为画面冻结。插入 fps=25 把每段重采样成恒定帧率即可消除。
            seg += ",fps=25"
            fc.append(f"{seg}[v{i}]")

        # 3.2 xfade 两两交叉淡化（过渡 TRANSITION 秒，offset 按目标时长推算）
        last_v = "v0"
        if n > 1:
            cumulative = 0.0  # 累计「不重叠」情况下的时长
            for i in range(1, n):
                cumulative += targets[i - 1]
                # 通用 xfade offset：每叠一次，就再往前挪 TRANSITION 秒
                offset = cumulative - i * TRANSITION
                left = last_v
                right = f"v{i}"
                out_tag = f"vx{i}"
                fc.append(
                    f"[{left}][{right}]xfade=transition=fade:duration={TRANSITION}:offset={offset:.3f}[{out_tag}]"
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

        # 字幕不在滤镜链里烧录（subtitles 滤镜依赖 libass，本机 ffmpeg 未编译，
        # 且 force_style 里的逗号会被 filter 解析器拆开导致整链失败）。
        # 改为合成完成后单独用 PIL overlay 方式烧录（见 run() → _burn_subtitles）。
        final_v = color_out

        # ── 步骤 4：构造音频 filter_complex 链 ──────────────────────────
        audio_input_tags: list[str] = []

        # 4.1 每段视频自带的音轨（如果有）
        for i in range(n):
            if self._has_audio_stream(shot_videos[i]):
                tag = f"ain{len(audio_input_tags)}"
                afx = ["aformat=sample_rates=44100:channel_layouts=stereo",
                       "asetpts=PTS-STARTPTS"]
                # 视频端走了拉伸/裁剪，音频必须同步变速/裁补，否则音画不同步。
                # 倍率用**音频流自己的时长**做基准（它通常比视频流长 0.2~0.3s），
                # 用视频倍率会让每段音频比画面长，末尾累积出大段「黑屏只有声音」。
                a_real = _probe_duration(shot_videos[i], "a:0")
                a_ratio = (targets[i] / a_real) if a_real > 0.1 else ratios[i]
                if abs(a_ratio - 1.0) > 1e-3:
                    afx += self._atempo_chain(1.0 / a_ratio)
                if trim_tos[i] is not None:
                    afx += [f"atrim=duration={trim_tos[i]:.3f}", "asetpts=PTS-STARTPTS"]
                if pads[i] > 0.02:
                    afx.append(f"apad=pad_dur={pads[i]:.3f}")
                fc.append(f"[{i}:a]" + ",".join(afx) + f"[{tag}]")
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
        target_durations: list[float] | None = None,
    ) -> bool:
        """增强版合成入口：先跑增强版命令，失败自动回退，任何情况不抛异常。

        失败策略：
          1) 优先尝试 colorgrade 版增强命令（包含 xfade/调色/压限/字幕）
          2) 若失败且上一轮用了 colorgrade → 再试 eq 回退版（兼容旧 FFmpeg）
          3) 再失败 → 退回到「顺序 concat demuxer 硬切」（几乎任何 FFmpeg 都能跑）
        """
        try:
            # —— 第一轮：colorgrade 版增强命令（本机不支持则直接 eq 版）———
            cmd, meta = self._build_enhanced_ffmpeg_command(
                video_paths, subtitles_path, bgm_path, output_path,
                force_eq_fallback=not self.use_colorgrade,
                target_durations=target_durations,
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
                    target_durations=target_durations,
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
