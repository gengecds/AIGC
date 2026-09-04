"""LLM Provider 合集（继承 LLMProvider 抽象基类）

包含两种 LLM 接入方式（根据 config.yaml 的 engine.llm_provider 自动切换）：
  1) OllamaProvider  — 纯本地 qwen3:8b，0 成本、断网可用、质量中
  2) DeepSeekProvider — 云端 DeepSeek API，付费、需网络、剧本/分镜质量更高、长上下文稳定
"""

import logging
import os
from typing import Optional

import httpx

from config.settings import settings
from providers.base import LLMProvider

logger = logging.getLogger(__name__)


class OllamaProvider(LLMProvider):
    """Ollama 本地模型"""

    def __init__(self, host: str = "127.0.0.1", port: int = 11434,
                 model: str | None = None):
        # 未显式指定模型时，按「当前激活风格」对应的 llm_model 选择（config 的 styles 表），
        # 取不到才回退到 config.yaml 的 ollama.model（如 qwen3:8b）。
        from config.settings import settings
        from config.style_resolver import llm_model_for_style
        self.base_url = f"http://{host}:{port}"
        self.default_model = model or llm_model_for_style(
            default=getattr(settings.ollama, "model", "qwen3:8b")
        )

    async def generate(self, prompt: str,
                       system_prompt: Optional[str] = None,
                       model: Optional[str] = None,
                       **kwargs) -> str:
        return await self.chat(
            messages=[
                {"role": "system", "content": system_prompt or ""},
                {"role": "user", "content": prompt},
            ],
            model=model or self.default_model,
            **kwargs,
        )

    async def chat(self, messages: list,
                   model: Optional[str] = None,
                   **kwargs) -> str:
        use_model = model or self.default_model
        # json_mode 时用较低 temperature，让结构化 JSON 输出更确定、更少随机早停
        # （qwen3 在 json 模式下偶发只输出半个 JSON 就 EOS，降温 + 上层重试可显著缓解）
        is_json = bool(kwargs.get("json_mode"))
        payload = {
            "model": use_model,
            "messages": messages,
            "stream": False,
            # 限制单次输出长度，避免 LLM 无限生成/截断导致 JSON 不完整
            # 剧本/分镜 JSON 较大，8192 足够（4096 会截断长剧本）
            "num_predict": kwargs.get("num_predict", 8192),
            "temperature": kwargs.get("temperature", 0.3 if is_json else 0.7),
        }
        # 上下文长度：数值型时传给 Ollama 的 options.num_ctx（放大窗口避免长 JSON 被截断）。
        # settings.ollama.get 支持缺省回退；非数值（如意外写入字符串）时忽略，交给服务端默认。
        num_ctx = kwargs.get("num_ctx")
        if num_ctx is None:
            num_ctx = settings.ollama.get("num_ctx", 8192)
        if isinstance(num_ctx, int) and num_ctx > 0:
            payload["options"] = {"num_ctx": num_ctx}
        # json_mode=True → 强制 Ollama 输出合法 JSON（脚本/分镜等结构化场景用）
        if is_json:
            payload["format"] = "json"

        async with httpx.AsyncClient(timeout=kwargs.get("timeout", 600)) as client:
            resp = await client.post(
                f"{self.base_url}/api/chat",
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
            return data["message"]["content"]


# ---------------------------------------------------------------------------
# DeepSeekProvider —— 云端 DeepSeek Chat API（OpenAI SDK 兼容模式）
# ---------------------------------------------------------------------------
class DeepSeekProvider(LLMProvider):
    """DeepSeek 云端大模型接入（剧本/分镜推荐用，推理能力比本地 qwen3:8b 强一个档次）

    为什么用 OpenAI SDK 兼容而不是自己写 httpx 请求？
      — DeepSeek 官方文档明确走 OpenAI 兼容端点（base_url=https://api.deepseek.com/v1），
        这样我们不需要重新实现鉴权/流式/错误处理，也和业内其他项目保持一致。
    安全原则：
      - API Key 必须通过环境变量 DEEPSEEK_API_KEY 注入，绝对不允许硬编码进代码或提交 git。
      - 任何日志/print 都不能打印或记录 Key 的明文。
    """

    # 这些是 DeepSeek 官方固定值，写为类常量，避免魔法字符串散落在各处
    # 注意：必须带 /v1，否则 AsyncOpenAI SDK 会报 404（OpenAI SDK 默认拼在 base_url 后面加路径）
    BASE_URL = "https://api.deepseek.com/v1"
    # 默认模型升级到 V4-Flash：比旧的 deepseek-chat（V3）速度快 2x、便宜 10x
    DEFAULT_MODEL = "deepseek-v4-flash"
    # 默认单次输出上限，剧本/分镜 JSON 比较长，设置成 8192 避免被截断
    DEFAULT_MAX_TOKENS = 8192
    # 大模型调用默认超时 10 分钟（长 JSON 输出可能会慢）
    DEFAULT_TIMEOUT = 600

    @staticmethod
    def _resolve_api_key(model_name: str) -> str:
        """根据要调用的模型名，自动挑选对应的 API Key（支持双 Key 独立控制）。

        为什么做这个？
          — 用户给了两个 Key（Flash / Pro）对应两个模型、两套账号/额度。
            分开读 Key 有三个好处：
              ① 额度用完 / 轮换 Key 可以单独操作，互不影响
              ② 分镜等复杂 JSON 场景可以单独加预算
              ③ 记账时能清晰区分「剧本（便宜）」和「分镜（贵）」两条线的 API 消费
        """
        low = (model_name or "").lower()
        # Pro 模型：推理强、消耗 DEEPSEEK_API_KEY_PRO（如果没配则退回通用 Key）
        if "pro" in low:
            return (
                os.environ.get("DEEPSEEK_API_KEY_PRO")
                or os.environ.get("DEEPSEEK_API_KEY", "")
            )
        # Flash 模型（包含 v4-flash / v4-flask 常见拼写）：消耗 DEEPSEEK_API_KEY_FLASH
        if "flash" in low or "flask" in low:
            return (
                os.environ.get("DEEPSEEK_API_KEY_FLASH")
                or os.environ.get("DEEPSEEK_API_KEY_FLSK")
                or os.environ.get("DEEPSEEK_API_KEY", "")
            )
        # 其他模型（老 deepseek-chat、自定义模型）→ 通用 DEEPSEEK_API_KEY
        return os.environ.get("DEEPSEEK_API_KEY", "")

    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None):
        """初始化：根据模型名自动选 Key、确定模型名、懒加载 client。

        优先级：
          - api_key 参数（显式传入，测试用）> 自动按模型名从 env 解析
          - model 参数 > 环境变量 DEEPSEEK_MODEL > 类常量 DEFAULT_MODEL（deepseek-v4-flash）
        """
        # 先确定要用的模型名（解析 api_key 必须先知道模型名，才能选对 env 里的 Key）
        resolved_model = model or os.environ.get("DEEPSEEK_MODEL") or self.DEFAULT_MODEL

        # 如果调用方没显式传 api_key，就按模型名从环境变量里挑对应 Key
        if api_key is None:
            api_key = self._resolve_api_key(resolved_model)
        # Key 为空 → 立即抛错，避免到实际调用才发现
        if not api_key:
            raise RuntimeError(
                f"未设置 DeepSeek 模型「{resolved_model}」对应的 API Key。"
                "请在项目根目录 .env 里写：\n"
                "  - V4-Flash（剧本默认）：DEEPSEEK_API_KEY_FLASH=sk-xxx\n"
                "  - V4-Pro（分镜/结构化）：DEEPSEEK_API_KEY_PRO=sk-yyy\n"
                "  - 或者写通用默认 Key：DEEPSEEK_API_KEY=sk-xxx\n"
                "然后重启服务。绝对不要把 Key 硬编码进代码或提交到 git。"
            )

        self.api_key = api_key
        self.default_model = resolved_model
        # 占位：client 在 _get_client() 里第一次被调用时才真正实例化
        self._client = None

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------
    def _get_client(self):
        """懒加载 OpenAI AsyncClient —— 第一次调用时才 import openai + 创建对象。

        为什么不放在 __init__？
          ① 没装 openai 包时也可以正常使用 OllamaProvider，不互相污染依赖。
          ② TDD 测试里可以 mock 掉这个方法返回 MagicMock，不用连网。
          ③ 单例模式，避免每次 generate/chat 都 new 一个新 client。
        """
        if self._client is None:
            # 延迟 import openai SDK：只有真正要调用 DeepSeek 时才加载
            from openai import AsyncOpenAI  # noqa: F401 懒加载
            self._client = AsyncOpenAI(
                api_key=self.api_key,
                base_url=self.BASE_URL,
            )
        return self._client

    # ------------------------------------------------------------------
    # LLMProvider 接口实现
    # ------------------------------------------------------------------
    async def generate(self, prompt: str,
                       system_prompt: Optional[str] = None,
                       model: Optional[str] = None,
                       **kwargs) -> str:
        """「一句话提示词」快捷封装：自动拼成 system + user 消息后调用 chat()。

        这个方法是为了和 OllamaProvider.generate() 保持完全一致的签名契约，
        这样 script_agent / storyboard_agent 里不管实际用的是本地模型还是云端模型，
        都不需要改调用代码 —— 只要在 config.yaml 切一下 engine.llm_provider 就行。
        """
        return await self.chat(
            messages=[
                {"role": "system", "content": system_prompt or ""},
                {"role": "user", "content": prompt},
            ],
            model=model,
            **kwargs,
        )

    async def chat(self, messages: list,
                   model: Optional[str] = None,
                   **kwargs) -> str:
        """完整的 Chat Completion 接口：传 messages 列表，返回模型最后一段文本。

        参数处理规则（和 OllamaProvider.chat 保持同构，行为一致便于 Agent 无感切换）：
          - json_mode=True：结构化 JSON 场景（剧本/分镜）
            → 传 response_format={"type": "json_object"}
            → temperature 默认降到 0.3（更确定更少随机，避免 JSON 被截断）
          - 普通聊天/文案生成：temperature 默认 0.7（保留创意多样性）
          - max_tokens：默认 8192 足够放一集剧本/20 个分镜
          - timeout：默认 600 秒（长剧本生成比较耗时）
        """
        # 是否是 JSON 模式（剧本/分镜等结构化输出用）
        is_json_mode = bool(kwargs.get("json_mode"))

        # 组装请求参数字典，按 DeepSeek 的 OpenAI 兼容契约命名
        params = {
            "model": model or self.default_model,
            "messages": messages,
            # 温度：json 模式默认 0.3（稳定），普通模式默认 0.7（有创意）
            "temperature": kwargs.get("temperature", 0.3 if is_json_mode else 0.7),
            # 输出长度上限
            "max_tokens": kwargs.get("max_tokens", self.DEFAULT_MAX_TOKENS),
            # 超时时间：传给 create 的 kwarg（OpenAI SDK create 支持 timeout 参数）
            "timeout": kwargs.get("timeout", self.DEFAULT_TIMEOUT),
        }

        # 如果是 JSON 模式，按 DeepSeek 规范必须传 response_format={"type": "json_object"}
        # （这样 DeepSeek 服务端会保证输出格式是合法 JSON，不会只输出半个对象）
        if is_json_mode:
            params["response_format"] = {"type": "json_object"}

        # 获取懒加载 client（这里可能是真的 AsyncOpenAI，也可能是测试里 mock 的对象）
        client = self._get_client()

        # 调用云端 create（异步）—— await 等待网络返回
        resp = await client.chat.completions.create(**params)

        # 提取最后一条 assistant 的文本内容
        # DeepSeek 的返回结构和 OpenAI 完全兼容：choices[0].message.content
        return resp.choices[0].message.content


# ---------------------------------------------------------------------------
# 工厂函数：统一给 Agent 提供 LLM 实例（按 config + fallback 规则自动选择）
# ---------------------------------------------------------------------------
def get_llm_provider(name: str = "auto", *,
                     default_model: Optional[str] = None,
                     deepseek_fallback: bool | None = None) -> LLMProvider:
    """按名称获取 LLM Provider。

    为什么需要工厂函数，而不是让 Agent 自己 new？
      ① 所有 Agent 的 init 里不用写重复的 if/else（DeepSeek 无 Key → Ollama fallback 逻辑统一处理）
      ② 后续新增 LLM 后端（比如 Kimi / 豆包），只改这一个函数，不用动 20 个 Agent
      ③ 默认值 "auto" 直接读 config.yaml 的 engine.llm_provider，不用每个 Agent 再传一遍

    参数：
      name: "auto"/"config"（按 config.yaml）| "ollama" | "deepseek"
      default_model: 传给底层 Provider 的模型名（仅对 DeepSeek 有效），ScriptAgent/StoryboardAgent
                     分别传 V4-Flash / V4-Pro，实现模型分层省钱；None 则用默认模型。
      deepseek_fallback:  True=DeepSeek 初始化失败时自动回退到 Ollama
                          False=直接抛异常（给 CI/测试用）
                          None=按 config.yaml 的 engine.llm_deepseek_fallback 决定
    """
    # 默认规则：deepseek_fallback=True（生产出故障保服务，不因为没 Key 就挂）
    if deepseek_fallback is None:
        deepseek_fallback = True

    # 只有 name="auto"/"config" 时才读 config.yaml：可以按 config 覆盖默认 provider，
    # 也可以按 config 覆盖 fallback 开关（例如 CI 环境希望配错就直接报错，不要偷偷降级）
    if name in (None, "", "auto", "config"):
        llm_name = str(getattr(settings.engine, "llm_provider", "ollama")).lower()
        try:
            cfg_fb = getattr(settings.engine, "llm_deepseek_fallback", None)
            if cfg_fb is not None:
                deepseek_fallback = bool(cfg_fb)
        except Exception:
            # settings.engine 没有这个属性也没关系，保持 deepseek_fallback=True 默认
            pass
    else:
        llm_name = str(name).lower()

    # 2) 按名字实例化
    if llm_name == "deepseek":
        try:
            # default_model 参数是 ScriptAgent/StoryboardAgent 传的（V4-Flash / V4-Pro），
            # 让不同 Agent 可以用同一个 Key，但不同模型（分层省钱）
            return DeepSeekProvider(model=default_model)
        except Exception as exc:
            # 失败原因常见两种：
            #   a) RuntimeError：没有 DEEPSEEK_API_KEY
            #   b) ImportError / ModuleNotFoundError：没装 openai 包
            # 允许 fallback 的情况下：降级成 OllamaProvider，并记录 warning 方便排查
            if deepseek_fallback:
                logger.warning(
                    "[LLM] DeepSeekProvider 初始化失败，自动回退到 Ollama 本地模型。"
                    f"失败原因：{type(exc).__name__}: {exc}"
                )
                return OllamaProvider()
            raise

    # 3) 名字是 ollama 或其它任何值 → 统一走 OllamaProvider（兜底，0 成本）
    return OllamaProvider()
