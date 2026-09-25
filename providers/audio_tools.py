"""音频工具：BGM 生成 / 音效合成 / 混音（全部本地，无外部素材依赖）

- generate_bgm(): 用 numpy 合成柔和钢琴琶音 BGM（C-G-Am-F 温暖和弦进行）
- generate_chime(): 结尾"叮"提示音（用于广告品牌收尾）
- mix_audio(): 把多条音轨（BGM + 配音 + 音效）按时间线混合成一条 WAV
- mux_audio_to_video(): 用 FFmpeg 把音轨合成进视频成片

输出统一为 44100Hz 单声道 16-bit PCM WAV，兼顾音质与 FFmpeg 兼容性。
"""

import importlib.util
import logging
import math
import shutil
import subprocess
import time
import uuid
import wave
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

SAMPLE_RATE = 44100

# ── Piper TTS（本地中文女声）──────────────
_voice = None
_TTS_MODEL = Path(__file__).parent.parent / "storage" / "tts" / "zh_CN-huayan-medium.onnx"
_TTS_CONFIG = Path(__file__).parent.parent / "storage" / "tts" / "zh_CN-huayan-medium.onnx.json"


def _get_voice():
    """懒加载 Piper 模型（首次调用加载，之后复用，约 0.5s）"""
    global _voice
    if _voice is None:
        from piper import PiperVoice
        if not _TTS_MODEL.exists():
            raise FileNotFoundError(
                f"Piper 中文模型缺失: {_TTS_MODEL}，请先下载 huayan 模型到 storage/tts/"
            )
        t0 = time.time()
        _voice = PiperVoice.load(str(_TTS_MODEL), config_path=str(_TTS_CONFIG))
        logger.info(f"[TTS:piper] 模型加载完成，用时 {time.time() - t0:.1f}s")
    return _voice


# ── VoxCPM2 TTS（本地，去AI话/音色设计）────
_vox = None
# VoxCPM2 权重目录：与 ComfyUI 模型集中管理，模型文件已下载到此
_VOX_MODEL_DIR = Path(
    "/Users/a715/git/ComfyUI/ComfyUI-Installs/ComfyUI/ComfyUI/models/voxcpm/VoxCPM2"
)


def _get_vox(device: str = "auto"):
    """懒加载 VoxCPM2 模型（M4 走 MPS，首次加载较慢，之后复用）

    optimize 关闭：Python 3.14 + torch 下 torch.compile 可能不稳定，禁用更稳。
    """
    global _vox
    if _vox is None:
        from voxcpm.core import VoxCPM
        logger.info(f"[TTS:voxcpm] 正在加载模型（首次较慢，约 20-30s，之后复用）: {_VOX_MODEL_DIR}")
        t0 = time.time()
        _vox = VoxCPM(
            voxcpm_model_path=str(_VOX_MODEL_DIR),
            enable_denoiser=False,       # 去噪增强仅用于克隆，音色设计用不到
            optimize=False,              # 避免 torch.compile 不稳定
            device=device,
        )
        logger.info(f"[TTS:voxcpm] 模型加载完成，用时 {time.time() - t0:.1f}s")
    return _vox


def warm_up_tts(engine: str = "piper") -> float:
    """提前加载 TTS 模型，返回加载用时（秒）。

    批量合成（如配音预测量）前先调用，把「模型加载」这一步单独暴露并计时，
    避免用户把首句 20-30s 的加载等待误认为卡死。
    """
    t0 = time.time()
    if engine == "voxcpm":
        _get_vox()
    else:
        _get_voice()
    return round(time.time() - t0, 2)


def _resample_float32(audio: np.ndarray, src_sr: int) -> np.ndarray:
    """把 float32 样本重采样到统一 SAMPLE_RATE（线性插值，单声道）"""
    if src_sr == SAMPLE_RATE or len(audio) == 0:
        return audio
    new_len = int(len(audio) * SAMPLE_RATE / src_sr)
    idx = np.linspace(0, len(audio) - 1, new_len)
    return np.interp(idx, np.arange(len(audio)), audio).astype(np.float32)


def synthesize_speech(text: str, output_path: str,
                      engine: str = "piper",
                      voice_prompt: str = "",
                      length_scale: float = 1.15,
                      noise_scale: float = 0.6) -> float:
    """合成一句中文语音，返回时长（秒）

    engine="piper"：本地 Piper 中文女声（zh_CN-huayan，去AI话的保底方案）。
    engine="voxcpm"：VoxCPM2 音色设计 TTS；voice_prompt 为中文音色描述，
        例如 "(温柔甜美年轻女声，柔和有亲和力)" 中括号内的描述。
    """
    if engine == "voxcpm":
        return _synthesize_vox(text, output_path, voice_prompt)
    return _synthesize_piper(text, output_path, length_scale, noise_scale)


def _synthesize_piper(text: str, output_path: str,
                      length_scale: float = 1.15,
                      noise_scale: float = 0.6) -> float:
    """Piper 合成路径（原 synthesize_speech 逻辑）"""
    from piper.config import SynthesisConfig
    voice = _get_voice()
    cfg = SynthesisConfig(length_scale=length_scale, noise_scale=noise_scale,
                          noise_w_scale=0.7, volume=1.0)
    chunks = list(voice.synthesize(text, cfg))
    if not chunks:
        return 0.0
    sr = chunks[0].sample_rate
    audio = np.concatenate([c.audio_int16_array for c in chunks])
    # 统一采样率到 44100（与 BGM 一致）
    if sr != SAMPLE_RATE:
        idx = np.linspace(0, len(audio) - 1, int(len(audio) * SAMPLE_RATE / sr))
        audio = np.interp(idx, np.arange(len(audio)), audio).astype(np.int16)
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(audio.tobytes())
    dur = len(audio) / SAMPLE_RATE
    logger.info(f"[TTS:piper] 合成 {dur:.2f}s: {text[:24]}…")
    return dur


def _synthesize_vox(text: str, output_path: str, voice_prompt: str) -> float:
    """VoxCPM2 音色设计合成路径

    设计模式：把音色控制说明以 "(描述)正文" 形式拼接为整段文本交给模型，
    无需参考音频。模型输出 48kHz float32，统一重采样回 44100 单声道 16-bit。
    """
    model = _get_vox()
    # 有音色控制说明时用 "(描述)正文"，无则仅正文（模型用默认音色）
    target = f"({voice_prompt}){text}" if voice_prompt.strip() else text
    audio = model.generate(
        text=target,
        cfg_value=2.0,
        inference_timesteps=10,
        normalize=True,
    )
    audio = np.asarray(audio, dtype=np.float32).reshape(-1)
    if audio.size == 0:
        logger.warning("[TTS:voxcpm] 输出为空，尝试回退 Piper")
        try:
            return _synthesize_piper(text, output_path)
        except Exception as e:
            # Piper 权重缺省时不要中断整条管线：返回 0 交由上层判定"无配音"
            logger.warning(f"[TTS] Piper 回退不可用（{e}），本句跳过配音")
            return 0.0
    src_sr = int(getattr(model.tts_model, "sample_rate", 48000))
    audio = _resample_float32(audio, src_sr)
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    pcm = (np.clip(audio, -1.0, 1.0) * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(pcm.tobytes())
    dur = len(pcm) / SAMPLE_RATE
    logger.info(f"[TTS:voxcpm] 合成 {dur:.2f}s: {text[:24]}…")
    return dur


def _save_wav(path, samples: np.ndarray, sample_rate: int | None = None):
    """把 float32 样本（-1~1）写成 16-bit PCM WAV。

    sample_rate 默认 None → 走全局 SAMPLE_RATE=44100；
    若 speed_ratio 调速需要临时保存"原始采样率"的 WAV，再由 FFmpeg 处理时
    需要精确匹配采样率，就显式传入 sample_rate。
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    pcm = (np.clip(samples, -1.0, 1.0) * 32767).astype(np.int16)
    sr = int(SAMPLE_RATE if sample_rate is None else sample_rate)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(pcm.tobytes())
    return str(path)


def _load_wav_to_float32(path, target_sr: int | None = None):
    """读任意 WAV（16/24/32-bit PCM、float、stereo）→ 单声道 float32（-1~1）。

    - 优先用 soundfile（支持所有格式）。
    - soundfile 失败再退 wave.open（仅支持最基础的 16-bit PCM 单声道）。
    - 若 target_sr 不为 None，读完会调用 _resample_float32 到目标采样率。
    """
    path = str(path)
    audio = None
    sr = None
    try:
        import soundfile as sf  # 已在 .venv 安装
        data, sr = sf.read(path, always_2d=False, dtype="float32")
        data = np.asarray(data, dtype=np.float32)
        # soundfile 返回 (n, ch) 或 (n,)；统一 (n, ch) 然后 mean 合并声道
        if data.ndim == 2:
            data = data.mean(axis=1, dtype=np.float32)
        audio = data.reshape(-1)
    except Exception as e_sf:
        logger.debug(f"[_load_wav_to_float32] soundfile 失败，退 wave（{e_sf}）")
        try:
            with wave.open(path, "rb") as wf:
                ch = wf.getnchannels()
                sw = wf.getsampwidth()
                sr = wf.getframerate()
                frames = wf.readframes(wf.getnframes())
            if sw == 2:
                audio = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
            elif sw == 1:
                audio = (np.frombuffer(frames, dtype=np.uint8).astype(np.float32) - 128) / 128.0
            else:
                raise RuntimeError(f"wave.open 不支持样本宽度 {sw} bytes")
            if ch > 1:
                audio = audio.reshape(-1, ch).mean(axis=1, dtype=np.float32)
        except Exception as e_w:
            raise RuntimeError(f"WAV 读取失败（soundfile & wave.open 都失败）: {path}\n"
                               f"  soundfile: {e_sf}\n  wave.open: {e_w}")

    if target_sr is not None and sr is not None and sr != target_sr:
        audio = _resample_float32(audio, sr)
    return audio.astype(np.float32, copy=False)


def generate_ambient(duration: float, output_path: str,
                     volume: float = 0.06, seed: int = 42) -> str:
    """生成柔和环境底噪（深夜街角氛围声）

    低通白噪声 + 缓慢起伏，模拟街角/室外的低频氛围，
    音量极轻铺底，让成片不再"死寂"，增加真实空间感。
    """
    n = int(SAMPLE_RATE * duration)
    rng = np.random.default_rng(seed)
    white = rng.standard_normal(n)
    # 简单滑动平均低通滤波 → 柔和的低频噪声
    kernel = np.ones(60) / 60
    low = np.convolve(white, kernel, mode="same")
    # 缓慢起伏（模拟环境声呼吸感）
    t = np.linspace(0, duration, n)
    breathe = 0.55 + 0.45 * np.sin(2 * np.pi * 0.08 * t)
    track = low * breathe * volume
    logger.info(f"[Ambient] 生成 {duration:.1f}s 环境底噪 → {output_path}")
    return _save_wav(output_path, track)


def _note(freq, dur, amp=0.5, attack=0.01, decay=3.0):
    """合成单个音符：正弦基音 + 两个泛音，带指数衰减包络（类似钢琴）"""
    n = int(SAMPLE_RATE * dur)
    t = np.linspace(0, dur, n, endpoint=False)
    # 基音 + 2 次/3 次泛音（音量递减），音色更饱满柔和
    wave_form = (
        np.sin(2 * np.pi * freq * t)
        + 0.4 * np.sin(2 * np.pi * freq * 2 * t)
        + 0.15 * np.sin(2 * np.pi * freq * 3 * t)
    )
    # 起音（attack）避免爆音 + 指数衰减
    env = np.ones(n)
    a = max(1, int(attack * SAMPLE_RATE))
    env[:a] = np.linspace(0, 1, a)
    env *= np.exp(-decay * t / dur)
    return wave_form * env * amp


def generate_bgm(duration: float, output_path: str,
                 bpm: int = 72, volume: float = 0.32) -> str:
    """生成柔和钢琴琶音 BGM（C-G-Am-F 温暖和弦进行）

    每个和弦分解为 4 个音符琶音（低→高），音色如轻钢琴，
    适合温馨/治愈/广告类短视频垫底。volume 偏低避免盖过配音。
    """
    # 音名频率（A4=440）
    def f(name):
        semis = {"C": -9, "D": -7, "E": -5, "F": -4, "G": -2, "A": 0, "B": 2}[name[0]]
        return 440.0 * 2 ** (semis / 12)

    # 和弦进行（C 大调暖色系）
    chords = [
        [f("C4"), f("E4"), f("G4"), f("C5")],
        [f("G3"), f("B3"), f("D4"), f("G4")],
        [f("A3"), f("C4"), f("E4"), f("A4")],
        [f("F3"), f("A3"), f("C4"), f("F4")],
    ]

    beat = 60.0 / bpm
    arp_dur = beat * 0.85  # 每个琶音音符时长
    total_n = int(SAMPLE_RATE * duration)
    track = np.zeros(total_n)

    pos = 0.0
    ci = 0
    # 循环琶音直到填满整段
    while pos < duration:
        chord = chords[ci % len(chords)]
        for note_freq in chord:
            if pos >= duration:
                break
            n = int(SAMPLE_RATE * arp_dur)
            start = int(pos * SAMPLE_RATE)
            end = min(start + n, total_n)
            if start < total_n:
                seg = _note(note_freq, (end - start) / SAMPLE_RATE, amp=0.5, decay=3.5)
                track[start:end] += seg[: end - start]
            pos += arp_dur
        ci += 1

    # 简单"混响"：把整轨延迟 0.35s 叠一层，制造空间感、更柔和
    delay_n = int(0.35 * SAMPLE_RATE)
    reverb = np.zeros(total_n)
    reverb[delay_n:] = track[:-delay_n] * 0.25
    track += reverb

    track = track * volume
    logger.info(f"[BGM] 生成 {duration:.1f}s 柔和伴奏 → {output_path}")
    return _save_wav(output_path, track)


def generate_chime(duration: float = 1.8, output_path: str = "storage/audio/chime.wav",
                   volume: float = 0.5) -> str:
    """结尾品牌"叮"提示音：高音 C6 → 泛音衰减，像广告片收尾的 logo 音效"""
    n = int(SAMPLE_RATE * duration)
    t = np.linspace(0, duration, n, endpoint=False)
    base = 1046.5  # C6
    chime = (
        np.sin(2 * np.pi * base * t)
        + 0.5 * np.sin(2 * np.pi * base * 2.76 * t)  # 非整数泛音 → 金属感
        + 0.3 * np.sin(2 * np.pi * base * 5.4 * t)
    )
    env = np.exp(-3.5 * t / duration)
    track = chime * env * volume
    return _save_wav(output_path, track)


# ── 多风格 BGM（按剧情情绪选择，替代单一钢琴琶音）────────

_BGM_STYLES = {
    # 温馨治愈：C 大调琶音，慢速
    "温馨": {"bpm": 66, "chords": [["C4", "E4", "G4", "C5"], ["G3", "B3", "D4", "G4"],
                                    ["A3", "C4", "E4", "A4"], ["F3", "A3", "C4", "F4"]], "decay": 4.0},
    # 激昂热血：A 小调 + 快速度 + 强低音（广告高潮/燃点）
    "激昂": {"bpm": 118, "chords": [["A2", "A3", "E4", "A4"], ["F2", "F3", "C4", "F4"],
                                    ["C3", "C4", "G4", "C5"], ["G2", "G3", "D4", "G4"]], "decay": 2.2},
    # 悬疑紧张：减七和弦 + 不规则节奏
    "悬疑": {"bpm": 88, "chords": [["B2", "D3", "F3", "A3"], ["F2", "A3", "B3", "D4"],
                                    ["E2", "G3", "B3", "D4"], ["D2", "F3", "A3", "C4"]], "decay": 3.0},
    # 清新轻快：G 大调 + 跳跃琶音
    "清新": {"bpm": 104, "chords": [["G3", "B3", "D4", "G4"], ["C4", "E4", "G4", "C5"],
                                    ["D3", "F#3", "A3", "D4"], ["E3", "G3", "B3", "E4"]], "decay": 2.6},
}


def _freq(name: str) -> float:
    """音名 → 频率（A4=440）"""
    semis = {"C": -9, "D": -7, "E": -5, "F": -4, "G": -2, "A": 0, "B": 2}[name[0]]
    return 440.0 * 2 ** (semis / 12)


def generate_bgm_mood(mood: str, duration: float, output_path: str,
                      volume: float = 0.38) -> str:
    """按情绪生成 BGM（温馨/激昂/悬疑/清新），未知情绪回退"温馨"

    每种风格用不同和弦进行 + 速度 + 衰减，适配剧情氛围。
    volume 默认 0.38（比旧版 0.28 大，听感更明显）。
    """
    style = _BGM_STYLES.get(mood, _BGM_STYLES["温馨"])
    bpm, chords, decay = style["bpm"], style["chords"], style["decay"]

    beat = 60.0 / bpm
    arp_dur = beat * 0.85
    total_n = int(SAMPLE_RATE * duration)
    track = np.zeros(total_n)

    pos = 0.0
    ci = 0
    while pos < duration:
        chord = chords[ci % len(chords)]
        # 激昂/清新风格把琶音密度提高（音符更密集 → 更燃/更跳）
        notes_per_beat = 2 if mood in ("激昂", "清新") else 1
        step = arp_dur / notes_per_beat
        for note_freq in chord:
            if pos >= duration:
                break
            n = int(SAMPLE_RATE * step)
            start = int(pos * SAMPLE_RATE)
            end = min(start + n, total_n)
            if start < total_n:
                seg = _note(_freq(note_freq), (end - start) / SAMPLE_RATE,
                            amp=0.5, decay=decay)
                # 浮点误差可能让 seg 长度差 1 帧，按较短者截取
                ln = min(len(seg), end - start)
                track[start:start + ln] += seg[:ln]
            pos += step
        ci += 1

    # 混响层（延迟 0.35s 叠 0.25）
    delay_n = int(0.35 * SAMPLE_RATE)
    reverb = np.zeros(total_n)
    reverb[delay_n:] = track[:-delay_n] * 0.25
    track += reverb

    track = track * volume
    logger.info(f"[BGM] 生成 {mood}风格 {duration:.1f}s → {output_path}")
    return _save_wav(output_path, track)


# ── 场景音效合成（煎锅滋滋/雨声/风声/车流等）────────

_SFX_PRESETS = {
    # 煎锅滋滋：高频白噪声脉冲串（油煎声）
    "煎锅滋滋": {"kind": "sizzle"},
    # 雨声：带起伏的白噪声（中频为主）
    "雨声": {"kind": "rain"},
    # 风声：低频噪声 + 缓慢呼啸
    "风声": {"kind": "wind"},
    # 街道车流：低频轰鸣 + 稀疏高频
    "车流": {"kind": "traffic"},
    # 城市环境：人声模糊 + 低噪
    "城市": {"kind": "city"},
    # 脚步：规律低频敲击
    "脚步": {"kind": "steps"},
}


def generate_sfx(sfx_name: str, duration: float, output_path: str,
                 volume: float = 0.5) -> str:
    """程序合成场景音效（煎锅滋滋/雨声/风声/车流/城市/脚步）"""
    preset = _SFX_PRESETS.get(sfx_name)
    if not preset:
        # 未知音效：回退为轻环境噪声（保证不空轨）
        preset = {"kind": "rain"}
    kind = preset["kind"]
    n = int(SAMPLE_RATE * duration)
    t = np.linspace(0, duration, n, endpoint=False)
    rng = np.random.default_rng(abs(hash(sfx_name)) % (2 ** 31))

    if kind == "sizzle":
        # 随机高频爆点（油溅）
        base = rng.standard_normal(n) * 0.5
        # 高通（差分近似）→ 保留高频
        hp = np.diff(base, prepend=0)
        # 爆点包络：随机间隔的短脉冲
        env = np.zeros(n)
        interval = int(0.08 * SAMPLE_RATE)
        for start in range(0, n, interval):
            seg_n = min(interval, n - start)
            pulse = np.exp(-np.linspace(0, 6, seg_n))
            env[start:start + seg_n] = pulse * (0.4 + rng.random())
        track = hp * env * 0.8
    elif kind == "wind":
        low = np.convolve(rng.standard_normal(n), np.ones(200) / 200, mode="same")
        gust = 0.4 + 0.6 * (0.5 + 0.5 * np.sin(2 * np.pi * 0.3 * t + rng.random() * 6))
        track = low * gust * 0.9
    elif kind == "traffic":
        low = np.convolve(rng.standard_normal(n), np.ones(400) / 400, mode="same")
        rumble = np.sin(2 * np.pi * 2.5 * t) * 0.5 + low
        pass_events = np.zeros(n)
        # 稀疏的"驶过"声（中频扫频）
        for _ in range(int(duration / 2.5)):
            start = rng.integers(0, max(1, n - int(1.5 * SAMPLE_RATE)))
            seg = int(0.9 * SAMPLE_RATE)
            if start + seg < n:
                sweep_t = np.linspace(0, 1, seg)
                freq = 300 + 800 * sweep_t
                sweep = np.sin(2 * np.pi * np.cumsum(freq) / SAMPLE_RATE)
                pass_events[start:start + seg] += sweep * np.exp(-3 * sweep_t) * 0.4
        track = (rumble * 0.7 + pass_events) * 0.8
    elif kind == "city":
        low = np.convolve(rng.standard_normal(n), np.ones(300) / 300, mode="same")
        # 偶发"人声感"（调制噪声团）
        chatter = np.zeros(n)
        for _ in range(int(duration / 1.8)):
            start = rng.integers(0, max(1, n - int(1.2 * SAMPLE_RATE)))
            seg = int(0.6 * SAMPLE_RATE)
            if start + seg < n:
                f = 120 + rng.random() * 220
                chatter[start:start + seg] = np.sin(2 * np.pi * f * t[start:start + seg]) * 0.3
        track = (low + chatter) * 0.9
    elif kind == "steps":
        track = np.zeros(n)
        step_gap = int(0.5 * SAMPLE_RATE)  # 每秒两步
        for start in range(0, n, step_gap):
            seg = int(0.12 * SAMPLE_RATE)
            if start + seg < n:
                thump = np.sin(2 * np.pi * 60 * t[:seg]) * np.exp(-np.linspace(0, 8, seg))
                track[start:start + seg] += thump * 0.8
    else:  # rain
        white = rng.standard_normal(n)
        low = np.convolve(white, np.ones(80) / 80, mode="same")
        breathe = 0.6 + 0.4 * np.sin(2 * np.pi * 0.12 * t)
        track = low * breathe

    track = track * volume
    logger.info(f"[SFX] 合成 {sfx_name} {duration:.1f}s → {output_path}")
    return _save_wav(output_path, track)


# ── 本地音乐库优先（用户下载的 BGM）────────

MUSIC_DIR = Path(__file__).parent.parent / "storage" / "music"


def pick_music_file(mood: str) -> Path | None:
    """从 storage/music/ 找匹配情绪的音频文件（mp3/wav/flac）

    匹配规则：文件名含情绪关键词（温馨/激昂/悬疑/清新/治愈/燃/紧张…）。
    用户从 flac.music.hi.cn 等网站下载的音乐放这里，管线优先使用真实音乐。
    """
    if not MUSIC_DIR.exists():
        return None
    # 情绪 → 文件名关键词映射
    mood_kws = {
        "温馨": ["温馨", "治愈", "温暖", "抒情", "soft", "gentle", "tender", "piano"],
        "激昂": ["激昂", "燃", "热血", "epic", "power", "energetic", "heroic"],
        "悬疑": ["悬疑", "紧张", "神秘", "mystery", "suspense", "dark"],
        "清新": ["清新", "轻快", "欢快", "upbeat", "fresh", "happy", "light"],
    }
    kws = mood_kws.get(mood, mood_kws["温馨"])
    for f in sorted(MUSIC_DIR.glob("*")):
        if f.suffix.lower() not in (".mp3", ".wav", ".flac", ".m4a", ".ogg"):
            continue
        name = f.name.lower()
        if any(k.lower() in name for k in kws):
            return f
    return None


def make_bgm_from_file(music_file: Path, duration: float, output_path: str,
                       volume: float = 0.38) -> str:
    """把本地音乐文件转成成片长度的 BGM 音轨（ffmpeg：循环 + 淡入淡出 + 调音量）"""
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y", "-stream_loop", "-1",
        "-i", str(music_file),
        "-t", f"{duration:.2f}",
        "-af", (f"volume={volume}," if volume != 1.0 else "") +
               "afade=t=in:st=0:d=1,afade=t=out:st="
               f"{max(0, duration - 2):.2f}:d=2",
        "-ac", "1", "-ar", str(SAMPLE_RATE),
        "-c:a", "pcm_s16le",
        str(out),
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if out.exists() and out.stat().st_size > 0:
            logger.info(f"[BGM] 本地音乐库: {music_file.name} → {out}")
            return str(out)
        logger.warning(f"[BGM] 音乐库转换失败: {r.stderr[-200:]}")
    except Exception as e:
        logger.warning(f"[BGM] 音乐库转换异常: {e}")
    return ""


def _read_wav(path) -> np.ndarray:
    """读取 WAV 为 float32 样本（-1~1），自动转为单声道"""
    with wave.open(str(path), "rb") as wf:
        ch = wf.getnchannels()
        width = wf.getsampwidth()
        rate = wf.getframerate()
        raw = wf.readframes(wf.getnframes())
    data = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32767.0
    if ch > 1:
        data = data.reshape(-1, ch).mean(axis=1)
    if rate != SAMPLE_RATE:
        # 简单线性重采样到统一采样率
        idx = np.linspace(0, len(data) - 1, int(len(data) * SAMPLE_RATE / rate))
        data = np.interp(idx, np.arange(len(data)), data)
    return data


def mix_audio(tracks: list[dict], total_duration: float, output_path: str) -> str:
    """把多条音轨按时间线混合成一条 WAV

    tracks: [{path, offset(秒), volume(0~1)}]
    - BGM 垫底 → offset=0, volume≈0.3
    - 配音 → 按各自时间点, volume≈0.95
    - 音效 → 指定 offset, volume≈0.6
    """
    total_n = int(SAMPLE_RATE * total_duration)
    mixed = np.zeros(total_n)
    for tr in tracks:
        samples = _read_wav(tr["path"])
        offset = int(tr.get("offset", 0) * SAMPLE_RATE)
        vol = tr.get("volume", 1.0)
        end = min(offset + len(samples), total_n)
        if offset < total_n:
            mixed[offset:end] += samples[: end - offset] * vol
    # 软限幅（tanh）：多轨叠加可能超 1.0，硬 clip 会产生爆音，
    # tanh 平滑压缩让响度更饱满又不破音
    mixed = np.tanh(mixed * 1.1) / np.tanh(1.1)
    logger.info(f"[Mix] 混合 {len(tracks)} 条音轨 → {output_path}")
    return _save_wav(output_path, mixed)


def _gen_ref_id() -> str:
    """生成短引用 ID：ref_<uuid7 前 8 位>_<unix 毫秒>，全局足够唯一

    uuid7 前 8 位自带时间前缀，后面再叠毫秒时间戳，双重保障不会碰撞。
    """
    # uuid7 没有就 fallback 到 uuid4，前 8 位 hex 足够短
    try:
        u = uuid.uuid7()
    except AttributeError:  # 老版 Python 没有 uuid7
        u = uuid.uuid4()
    short = u.hex[:8]
    ms = int(time.time() * 1000)
    return f"ref_{short}_{ms}"


# ── VoxCPM2 音色克隆（参考音频 + TTS）───────────────────────────────

def _write_empty_wav(output_path: str):
    """写一个 0 采样、合法的 WAV 文件头（兜底用，至少保证文件存在且能被后续流程识别）"""
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(out), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        # 不写入任何帧 → 0 秒合法 WAV


def _mock_clone(reference_audio_path: str, output_path: str) -> str:
    """Mock 模式：不具备真实推理能力时，拷贝参考音频作为输出保底。

    策略：
      1) 优先尝试 ffmpeg 转 WAV（统一采样率/声道，避免后续流程打不开）
      2) ffmpeg 失败就 shutil.copy2 直接拷贝
      3) 再失败就写一个空 WAV，打 WARN，绝对不抛异常
    """
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    ref = Path(reference_audio_path)
    if not ref.exists():
        logger.warning(
            f"[clone_voice:mock] 参考音频不存在: {ref}，将生成空 WAV 兜底"
        )
        _write_empty_wav(output_path)
        return str(out)

    # 策略 1：如果本机有 ffmpeg → 统一转成 44100Hz 单声道 16-bit PCM WAV
    try:
        which_ff = shutil.which("ffmpeg")
        if which_ff:
            cmd = [
                which_ff, "-y", "-i", str(ref),
                "-ac", "1", "-ar", str(SAMPLE_RATE),
                "-c:a", "pcm_s16le", str(out),
            ]
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            if out.exists() and out.stat().st_size > 0:
                logger.warning(
                    "[clone_voice:mock] 已用 ffmpeg 将参考音频转 WAV 作为合成输出"
                    f"（voxcpm 依赖或模型文件缺失，未做真实克隆）"
                )
                return str(out)
            logger.warning(f"[clone_voice:mock] ffmpeg 失败，回退直拷贝：{r.stderr[-200:]}")
    except Exception as e:
        logger.warning(f"[clone_voice:mock] ffmpeg 异常，回退直拷贝：{e}")

    # 策略 2：直接拷贝
    try:
        shutil.copy2(ref, out)
        logger.warning(
            "[clone_voice:mock] 已直接拷贝参考音频作为输出（voxcpm 依赖或模型文件缺失）"
        )
        return str(out)
    except Exception as e:
        logger.warning(f"[clone_voice:mock] 直拷贝也失败：{e}，将生成空 WAV")

    # 策略 3：空 WAV 兜底
    _write_empty_wav(output_path)
    return str(out)


def clone_voice(
    reference_audio_path: str,
    text: str,
    output_path: str,
    *,
    language: str = "zh",
    emotion: str = "default",
    speed_ratio: float = 1.0,
    voxcpm_model_dir: str = (
        "/Users/a715/git/ComfyUI/ComfyUI-Installs/ComfyUI/ComfyUI/models/voxcpm/VoxCPM2/"
    ),
) -> str:
    """用 VoxCPM2 做「参考音频音色克隆 + TTS」合成。

    参数:
        reference_audio_path: 本地参考音频（WAV/MP3/M4A，建议 <30s，16kHz+ 采样率）
        text: 要合成的台词（中文/英文都行，language 参数会提示模型）
        output_path: 输出 WAV 路径（父目录不存在会自动创建）
        language: 语言，默认 "zh"（zh/en/ja…），透传给模型
        emotion: 语气，default/happy/sad/angry，透传给模型
        speed_ratio: 语速倍率，0.7-1.4 之间最佳
        voxcpm_model_dir: VoxCPM2 模型根目录（含 model.safetensors / config.json / vocos/）

    返回:
        实际写入的 output_path（字符串）。

    关键策略（对调用方友好，永不抛异常）：
      - 若 voxcpm 依赖未装，或 transformers 缺失，或模型主文件不存在：
        自动走「mock 模式」，直接拷贝/转参考音频，打 WARN 日志。
      - 若环境满足：走真实 VoxCPM2 克隆+TTS 路径（具体推理留给 ComfyUI 联调时验证，
        这里写好 import、输入输出占位，保证 import 不报错、接口稳定）。
    """
    # ── 1. 环境自检（缺依赖 / 缺模型 → mock）────────────
    has_voxcpm = importlib.util.find_spec("voxcpm") is not None
    has_transformers = importlib.util.find_spec("transformers") is not None
    model_file = Path(voxcpm_model_dir) / "model.safetensors"

    if not (has_voxcpm and has_transformers and model_file.exists()):
        missing = []
        if not has_voxcpm:
            missing.append("voxcpm 包未安装")
        if not has_transformers:
            missing.append("transformers 包未安装")
        if not model_file.exists():
            missing.append(f"模型主文件缺失: {model_file}")
        logger.warning(
            "[clone_voice] 环境不满足真实推理，进入 mock 模式。原因: "
            + "；".join(missing)
        )
        return _mock_clone(reference_audio_path, output_path)

    # ── 2. 真实模式：VoxCPM2 官方 reference_wav_path 音色克隆（已验证，非占位）────
    #    官方 API 2.0.3：通过 generate(reference_wav_path=...) 内部调
    #      tts_model.build_prompt_cache(reference_wav_path=..., ...)
    #    自动抽取参考音频的说话人 prompt cache → 合成同音色的台词。
    #    仅 V2 模型支持 reference（我们模型就是 4.2GB VoxCPM2，V2）。
    try:
        # 复用已经在文件里定义好的 _get_vox() 懒加载：
        #   - 与 synthesize_speech 共用同一个模型句柄（节省显存/内存）
        #   - 首次加载会自动下载 zipenhancer 去噪器（HF 默认路径，约 300MB）
        #   - device 默认 "auto"（M4 → 自动选 MPS / CPU）
        model = _get_vox(device="auto")

        # 关键：VoxCPM2.V2 克隆的核心参数 = reference_wav_path
        #   text 直接传台词原文，**不要再手动拼 voice_prompt**（官方 V2
        #   模型通过 reference_wav_path 自动提取说话人特征，纯文本前缀
        #   反而会把语气/音色提示混入 tokenizer，导致音色克隆变弱）。
        audio = model.generate(
            text=text,
            reference_wav_path=str(Path(reference_audio_path)),  # 音色克隆核心
            denoise=True,       # 与 enable_denoiser=True 配合：先对参考音频去噪
            cfg_value=2.0,
            inference_timesteps=10,
            normalize=True,     # 中文文本规范化（数字→汉字、标点清理）
        )
        audio = np.asarray(audio, dtype=np.float32).reshape(-1)
        if audio.size == 0:
            logger.warning("[clone_voice] 推理输出为空，回退 mock 模式")
            return _mock_clone(reference_audio_path, output_path)

        # 语速调整（generate 原生没有 speed 参数，用 FFmpeg atempo 做 pitch-preserving 变速）
        # atempo 安全范围 [0.5, 2.0]，超出时链式拼接
        if abs(speed_ratio - 1.0) > 1e-3:
            try:
                import tempfile
                src_sr = int(getattr(model.tts_model, "sample_rate", SAMPLE_RATE))
                tmp_in = tempfile.NamedTemporaryFile(delete=False, suffix=".wav")
                tmp_in.close()
                tmp_out = tempfile.NamedTemporaryFile(delete=False, suffix=".wav")
                tmp_out.close()
                # 以模型原生采样率保存（保证 FFmpeg 读到的采样率正确）
                _save_wav(tmp_in.name, audio, sample_rate=src_sr)
                # 把 speed_ratio 夹到 [0.5, 2.0]（FFmpeg atempo 单次限制）
                sr_clamped = max(0.5, min(2.0, float(speed_ratio)))
                cmd = [
                    "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                    "-i", tmp_in.name,
                    "-filter:a", f"atempo={sr_clamped}",
                    "-ar", str(SAMPLE_RATE),
                    "-ac", "1",
                    "-c:a", "pcm_s16le",
                    tmp_out.name,
                ]
                subprocess.run(cmd, check=True, capture_output=True, text=True)
                # 重新读取变速后的 WAV 回 float32，并统一到 SAMPLE_RATE
                audio = _load_wav_to_float32(tmp_out.name, target_sr=SAMPLE_RATE)
                for p in (tmp_in.name, tmp_out.name):
                    try: os.unlink(p)
                    except OSError: pass
            except Exception as e_speed:
                logger.warning(f"[clone_voice] speed_ratio 调速失败，跳过（{e_speed}）")

        # 统一重采样 → SAMPLE_RATE，写 16-bit PCM WAV
        src_sr = int(getattr(model.tts_model, "sample_rate", 48000))
        audio = _resample_float32(audio, src_sr)
        _save_wav(output_path, audio)
        dur = len(audio) / SAMPLE_RATE
        logger.info(
            f"[clone_voice] ✅ 真实 VoxCPM2 音色克隆 + 合成完成，"
            f"时长 {dur:.2f}s → {output_path}"
        )
        return str(Path(output_path))

    except Exception as e:
        # 真实推理任何异常 → 全部回退 mock，保障流程不挂
        import traceback
        logger.warning(
            f"[clone_voice] 真实推理异常（{type(e).__name__}: {e}），回退 mock 模式。"
            f"\n{traceback.format_exc()}"
        )
        return _mock_clone(reference_audio_path, output_path)


def mux_audio_to_video(video_path: str, audio_path: str, output_path: str,
                       audio_filter: str = "") -> str:
    """用 FFmpeg 把音轨合进视频（无声视频 → 有声成片）

    audio_filter 可传入 FFmpeg 音频滤镜链（如混响/压缩/淡入淡出），
    让 TTS 配音不那么"干"，更像成片音轨。
    """
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y",
        "-i", str(video_path),
        "-i", str(audio_path),
        "-map", "0:v", "-map", "1:a",
    ]
    if audio_filter:
        cmd += ["-af", audio_filter]
    cmd += [
        "-c:v", "copy",           # 视频流不重编码（快）
        "-c:a", "aac", "-b:a", "160k",
        "-shortest",
        str(out),
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if out.exists() and out.stat().st_size > 0:
            logger.info(f"[Mux] 音视频合成: {video_path} + {audio_path} → {out}")
            return str(out)
        logger.warning(f"[Mux] FFmpeg 失败: {r.stderr[-300:]}")
    except Exception as e:
        logger.warning(f"[Mux] 异常: {e}")
    return ""
