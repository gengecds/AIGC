"""配置中心 — config.yaml + 环境变量覆盖

用法:
    from config.settings import settings
    settings.engine.image_provider  # → "mock"
    settings.comfyui.server_port    # → 18188
"""

import os
from pathlib import Path
from typing import Any, Dict, Optional

import yaml


def _deep_merge(base: dict, override: dict) -> dict:
    """递归合并字典"""
    result = base.copy()
    for k, v in override.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result


class Settings:
    """配置对象，支持属性式访问"""

    def __init__(self, config_path: Optional[str] = None):
        self._data = self._load(config_path)
        self._apply_env_overrides()

    def _load(self, config_path: Optional[str] = None) -> dict:
        path = Path(config_path or (Path(__file__).parent / "config.yaml"))
        if not path.exists():
            return {}
        with open(path) as f:
            return yaml.safe_load(f) or {}

    def _apply_env_overrides(self):
        """环境变量覆盖"""
        mapping = {
            "DEEPSEEK_API_KEY": ("deepseek", "api_key"),
            "TONGYI_API_KEY": ("tongyi", "api_key"),
            "GPU_HOST": ("comfyui", "gpu_host"),
            "GPU_PORT": ("comfyui", "gpu_port"),
            "GPU_PASS": ("comfyui", "gpu_pass"),
        }
        for env_key, (section, field) in mapping.items():
            val = os.environ.get(env_key)
            if val:
                if section not in self._data:
                    self._data[section] = {}
                self._data[section][field] = val

        # 引擎覆盖
        img_prov = os.environ.get("IMAGE_PROVIDER")
        if img_prov:
            self._data.setdefault("engine", {})["image_provider"] = img_prov
        vid_prov = os.environ.get("VIDEO_PROVIDER")
        if vid_prov:
            self._data.setdefault("engine", {})["video_provider"] = vid_prov
        llm_prov = os.environ.get("LLM_PROVIDER")
        if llm_prov:
            self._data.setdefault("engine", {})["llm_provider"] = llm_prov

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            return super().__getattribute__(name)
        val = self._data.get(name, {})
        if isinstance(val, dict):
            return Settings._DictProxy(val)
        return val

    class _DictProxy:
        """字典属性代理，支持 foo.bar.baz 式访问"""
        def __init__(self, data: dict):
            self._data = data
        def __getattr__(self, name: str) -> Any:
            if name.startswith("_"):
                return object.__getattribute__(self, name)
            val = self._data.get(name, {})
            if isinstance(val, dict):
                return Settings._DictProxy(val)
            return val
        def __repr__(self):
            return repr(self._data)
        def get(self, key, default=None):
            return self._data.get(key, default)
        def keys(self):
            return self._data.keys()
        def values(self):
            return self._data.values()
        def items(self):
            return self._data.items()


# 全局单例
settings = Settings()
