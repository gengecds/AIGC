"""模型能力注册表 —— 管线据此动态增减节点。

背景：项目要「适配所有模型」，而非只迁就眼前某个模型。
不同视频模型的能力不同，例如：
  - LTX-Video：只出画面（无声），需要 audio_agent 后期配音+BGM；
  - MiniMax H3（已废弃删除）：全模态，无需 audio_agent。

未来若出现「节点更少」的模型（一次生成视频即含全部要素），
只需在该注册表新增一条能力描述，管线即可自动适配，无需改调度器逻辑。

约定：
  每个视频引擎以 video_model_type 为键声明能力字典，支持以下键：
    native_audio   模型是否自带原生音频（True 时管线跳过 audio_agent）
    requires_audio_node  是否仍需 audio_agent 后期合成音频（缺省取 not native_audio）
    pipeline_skip  该引擎下应跳过的节点名（预留扩展，默认使用 native_audio 推导）
    pipeline_extra 该引擎额外需要的节点名（预留扩展，默认空）
"""

from typing import Optional


_VIDEO_CAPS: dict[str, dict] = {
    # LTX-2.3 MLX：本地原生引擎（当前唯一在用引擎，见 config.yaml engine.video_engine）；
    # 虽能带氛围音轨，但对白仍需 audio_agent 配音，故走后期合成，保证字幕/对白流程不变
    "ltx_mlx": {
        "native_audio": False,
        "requires_audio_node": True,
    },
}


def video_caps(video_model_type: Optional[str] = None, name: Optional[str] = None) -> dict:
    """返回某个视频引擎的能力清单。

    :param video_model_type: 引擎标识（ltx / minimax_h3...），缺省时从当前风格解析。
    :param name: 风格名（仅当未传 video_model_type 时使用）。
    """
    if not video_model_type:
        from config.style_resolver import video_model_type_for_style
        video_model_type = video_model_type_for_style(name=name) or "ltx"
    return dict(_VIDEO_CAPS.get(video_model_type or "ltx", {}))


def native_audio(video_model_type: Optional[str] = None, name: Optional[str] = None) -> bool:
    """当前/指定视频模型是否自带原生音频。"""
    return bool(video_caps(video_model_type, name).get("native_audio", False))


def requires_audio_node(video_model_type: Optional[str] = None, name: Optional[str] = None) -> bool:
    """当前/指定视频模型下，管线是否仍需 audio_agent 后期合成音频。"""
    caps = video_caps(video_model_type, name)
    if "requires_audio_node" in caps:
        return bool(caps["requires_audio_node"])
    return not bool(caps.get("native_audio", False))


def pipeline_skip(video_model_type: Optional[str] = None, name: Optional[str] = None) -> set[str]:
    """按模型能力，管线应跳过的节点名集合（预留扩展）。"""
    caps = video_caps(video_model_type, name)
    skip = set(caps.get("pipeline_skip", []) or [])
    # 自带音频的模型，跳过「后期音频合成」节点
    if not requires_audio_node(video_model_type, name):
        skip.add("audio_agent")
    return skip


def pipeline_extra(video_model_type: Optional[str] = None, name: Optional[str] = None) -> list[str]:
    """按模型能力，管线额外需要执行的节点名（预留扩展）。"""
    caps = video_caps(video_model_type, name)
    return list(caps.get("pipeline_extra", []) or [])
