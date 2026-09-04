"""Agent 8 - 音频合成（配音 + 背景音乐 + 音效）

把无声成片变成有声成片：
1. 用 Piper TTS（本地中文女声）把剧本对白合成配音
2. 用 numpy 程序合成柔和钢琴 BGM 垫底
3. 结尾叠加品牌"叮"提示音
4. FFmpeg 混音 → 音轨合进成片

输入：compose 成片 + 剧本（对白）
输出：带音频的成片 + 音轨文件
"""

import logging
import asyncio
import subprocess
from datetime import datetime
from pathlib import Path

from agents.base import Agent, AgentResult

logger = logging.getLogger(__name__)


class AudioAgent(Agent):
    """Agent 8：配音 + BGM + 音效合成"""

    name = "audio_agent"

    def __init__(self):
        super().__init__(name="audio_agent")

    async def run(self, compose_result, script_result, subtitle_result=None,
                  plan: dict | None = None) -> AgentResult:
        # 音频合成全程是 CPU/子进程密集的同步代码（Piper TTS、numpy 合成、FFmpeg 混音），
        # 放到线程池执行，避免阻塞后端事件循环（否则视频生成期间 HTTP 无响应）
        return await asyncio.to_thread(
            self._run_sync, compose_result, script_result, subtitle_result, plan
        )

    def _run_sync(self, compose_result, script_result, subtitle_result=None,
                  plan: dict | None = None) -> AgentResult:
        logger.info("[AudioAgent] 开始音频合成（配音+BGM+音效）")

        # 1. 定位成片
        video_path = self._find_final_video(compose_result)
        if not video_path or not Path(video_path).exists():
            return AgentResult(success=False, error="找不到合成成片，无法加音频")

        # 2. 成片时长
        duration = self._get_duration(video_path)
        if duration <= 0:
            return AgentResult(success=False, error="无法读取成片时长")

        # 3. 提取对白文本
        dialogues = self._extract_dialogues(script_result)
        if not dialogues:
            dialogues = self._extract_from_subtitle(subtitle_result)
        logger.info(f"[AudioAgent] 提取到 {len(dialogues)} 句对白，成片 {duration:.1f}s")

        # 4. 合成配音（每句 TTS）
        from providers import audio_tools
        audio_dir = Path("storage/audio")
        audio_dir.mkdir(parents=True, exist_ok=True)

        # TTS 引擎与音色：优先用「当前激活风格」的 tts_engine / voice_prompt（可在前端步骤面板选择），
        # 风格没配置时回退到 config.yaml 的 tts 段（tts.engine = piper|voxcpm，voice_prompt 为音色描述）
        try:
            from config.style_resolver import tts_for_style
            tts_engine, voice_prompt = tts_for_style()
        except Exception:
            try:
                from config.settings import settings
                tts_cfg = settings.tts
                tts_engine = getattr(tts_cfg, "engine", "piper") or "piper"
                voice_prompt = getattr(tts_cfg, "voice_prompt", "") or ""
            except Exception:
                tts_engine, voice_prompt = "piper", ""

        voice_tracks = []
        for i, text in enumerate(dialogues):
            wav = audio_dir / f"voice_{i:02d}.wav"
            dur = audio_tools.synthesize_speech(
                text, str(wav), engine=tts_engine, voice_prompt=voice_prompt
            )
            if dur > 0:
                voice_tracks.append({"path": str(wav), "dur": dur, "text": text})

        if not voice_tracks:
            return AgentResult(success=False, error="配音生成失败（TTS 无输出）")

        # 5. 时间分配：把配音铺到成片时间轴上
        total_voice = sum(t["dur"] for t in voice_tracks)
        n = len(voice_tracks)
        if total_voice >= duration * 0.9:
            # 配音总时长接近/超过成片：按成片均匀分配起点（避免全部挤在开头）
            slot = duration / n
            for i, t in enumerate(voice_tracks):
                t["offset"] = min(i * slot, max(0, duration - t["dur"]))
        else:
            # 配音较短：句与句之间留均匀空隙
            gap = (duration - total_voice) / max(1, n - 1) if n > 1 else 0
            offset = 0.0
            for t in voice_tracks:
                t["offset"] = min(offset, max(0, duration - t["dur"]))
                offset += t["dur"] + max(0, gap)

        # 6. 生成 BGM（按剧情情绪：优先本地音乐库，其次程序合成多风格）+ 环境底噪
        #    从研究方案读取情绪与场景声效，缺省回退"温馨"
        mood = (plan or {}).get("bgm_mood", "温馨") if plan else "温馨"
        scene_sounds = (plan or {}).get("scene_sounds", []) if plan else []

        bgm_wav = audio_dir / "bgm.wav"
        # 优先使用 storage/music/ 里用户下载的真实音乐（文件名匹配情绪）
        music_file = audio_tools.pick_music_file(mood)
        if music_file:
            bgm_ok = audio_tools.make_bgm_from_file(music_file, duration, str(bgm_wav), volume=0.4)
        else:
            bgm_ok = audio_tools.generate_bgm_mood(mood, duration, str(bgm_wav), volume=0.38)
        if not bgm_ok:
            # 兜底：单风格程序合成
            bgm_ok = audio_tools.generate_bgm(duration, str(bgm_wav), bpm=70, volume=0.32)

        ambient_wav = audio_dir / "ambient.wav"
        audio_tools.generate_ambient(duration, str(ambient_wav), volume=0.12)

        # 7. 场景音效：按分镜时间轴插入（滋滋声/雨声/风声/车流等）
        #    每个音效按剧情时段铺开：前 1/4、中段、后 1/4 各一个窗口
        sfx_tracks = []
        if scene_sounds:
            audio_tools.generate_sfx(str(scene_sounds[0]), duration, str(audio_dir / "sfx_main.wav"), volume=0.5)
            sfx_tracks.append({"path": str(audio_dir / "sfx_main.wav"), "offset": 0, "volume": 0.5})
            if len(scene_sounds) > 1:
                audio_tools.generate_sfx(str(scene_sounds[1]), duration * 0.5, str(audio_dir / "sfx_mid.wav"), volume=0.4)
                sfx_tracks.append({"path": str(audio_dir / "sfx_mid.wav"), "offset": duration * 0.25, "volume": 0.4})

        # 8. 结尾提示音（成片足够长时）
        chime_track = None
        if duration > 4.0:
            chime_wav = audio_dir / "chime.wav"
            audio_tools.generate_chime(1.6, str(chime_wav), volume=0.5)
            chime_track = {"path": str(chime_wav), "offset": max(0, duration - 2.0), "volume": 0.55}

        # 9. 混合所有音轨（环境音打底 + BGM + 场景音效 + 配音 + 提示音）
        #    音量整体偏大（BGM 0.5/配音 1.3/环境 0.15），配合 mix 软限幅防爆音
        mix_tracks = [
            {"path": str(ambient_wav), "offset": 0, "volume": 0.15},
            {"path": str(bgm_wav), "offset": 0, "volume": 0.50},
        ]
        mix_tracks += sfx_tracks
        mix_tracks += [{"path": t["path"], "offset": t["offset"], "volume": 1.3} for t in voice_tracks]
        if chime_track:
            mix_tracks.append(chime_track)

        mixed_wav = audio_dir / "audio_track.wav"
        audio_tools.mix_audio(mix_tracks, duration, str(mixed_wav))

        # 9. 音轨合进成片（音频增强：整体增益 + 压缩+轻混响+首尾淡入淡出，去"干TTS"味）
        final_out = str(Path(video_path).with_name(
            Path(video_path).stem + "_audio.mp4"
        ))
        af = (
            "volume=1.5,"
            "acompressor=threshold=-16dB:ratio=2.5:attack=8:release=120,"
            "aecho=0.6:0.4:50|90:0.18|0.12,"
            f"afade=t=in:st=0:d=0.25,afade=t=out:st={max(0, duration - 0.6):.2f}:d=0.6"
        )
        result_path = audio_tools.mux_audio_to_video(video_path, str(mixed_wav), final_out, audio_filter=af)

        if not result_path:
            return AgentResult(success=False, error="音视频合成失败（FFmpeg）")

        return AgentResult(
            success=True,
            data={
                "final_video": result_path,
                "audio_track": str(mixed_wav),
                "voices": [{"text": t["text"], "offset": round(t["offset"], 2),
                            "duration": round(t["dur"], 2)} for t in voice_tracks],
                "bgm": str(bgm_wav),
                "duration": round(duration, 2),
            },
            metadata={
                "agent": self.name,
                "timestamp": datetime.utcnow().isoformat(),
                "dialogues": len(voice_tracks),
                "video_duration": round(duration, 2),
            },
        )

    # ── 辅助方法 ──────────────────────────

    def _find_final_video(self, compose_result) -> str:
        """从 compose 结果里找成片 mp4 路径（兼容多种结构）"""
        data = compose_result.data if hasattr(compose_result, "data") else {}
        data = data or {}

        def _walk(obj, found):
            if isinstance(obj, dict):
                for k, v in obj.items():
                    if isinstance(v, str) and v.endswith((".mp4", ".webm", ".mov")):
                        found.append(v)
                    else:
                        _walk(v, found)
            elif isinstance(obj, list):
                for it in obj:
                    _walk(it, found)

        found = []
        _walk(data, found)
        # 优先 _audio 结尾的或 final 的
        for f in found:
            if "audio" in f:
                return f
        for f in found:
            if "final" in f:
                return f
        return found[0] if found else ""

    def _get_duration(self, video_path: str) -> float:
        """ffprobe 读取视频时长（秒）"""
        try:
            r = subprocess.run(
                ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
                 "-of", "csv=p=0", str(video_path)],
                capture_output=True, text=True, timeout=20,
            )
            return float(r.stdout.strip())
        except Exception:
            return 0.0

    def _extract_dialogues(self, script_result) -> list[str]:
        """从剧本提取对白文本（兼容多种字段）"""
        data = script_result.data if hasattr(script_result, "data") else {}
        data = data or {}
        texts = []

        def _collect(obj):
            if isinstance(obj, dict):
                # 直接对白字段
                for key in ("text", "content", "line", "dialogue"):
                    if key in obj and isinstance(obj[key], str) and obj[key].strip():
                        texts.append(obj[key].strip())
                        return
                # 递归
                for v in obj.values():
                    _collect(v)
            elif isinstance(obj, list):
                for it in obj:
                    _collect(it)

        _collect(data)
        # 去重保序
        seen = set()
        result = []
        for t in texts:
            if t not in seen:
                seen.add(t)
                result.append(t)
        return result

    def _extract_from_subtitle(self, subtitle_result) -> list[str]:
        """从 SRT 字幕提取纯文本（去掉时间戳）"""
        data = subtitle_result.data if hasattr(subtitle_result, "data") else {}
        data = data or {}
        texts = []
        for v in data.values() if isinstance(data, dict) else []:
            if isinstance(v, str):
                for line in v.split("\n"):
                    line = line.strip()
                    if line and "-->" not in line and not line.isdigit():
                        texts.append(line)
        return texts
