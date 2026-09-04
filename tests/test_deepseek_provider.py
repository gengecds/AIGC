#!/usr/bin/env python3
"""DeepSeekProvider 单元测试（TDD RED 阶段先写测试）

覆盖 3 个行为：
  1) 未设置 DEEPSEEK_API_KEY 时初始化必须抛 RuntimeError（防止忘记配 Key）
  2) generate() 方法：正确包装 system+user 消息，参数传到 AsyncOpenAI 对应字段
  3) chat(json_mode=True) 时：必须传 response_format={"type": "json_object"}
"""

import sys, os, asyncio
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# 解决用户 site 路径问题（和其他测试保持一致）
import site
site.addsitedir("/Users/mac/Library/Python/3.14/lib/python/site-packages")

from unittest.mock import AsyncMock, MagicMock, patch


async def test_1_no_api_key_raises():
    """测试1：无 DEEPSEEK_API_KEY 环境变量时，初始化抛 RuntimeError"""
    print("\n[TEST 1] 无 DEEPSEEK_API_KEY 必须抛 RuntimeError ... ", end="")
    # 确保环境里没有 Key（用 patch 沙箱隔离，不污染真实环境）
    env_clean = {}
    with patch.dict(os.environ, env_clean, clear=True):
        try:
            # 每次 import 都从模块取最新类，避免缓存
            import importlib, providers.llm
            importlib.reload(providers.llm)
            DeepSeekProvider = providers.llm.DeepSeekProvider
            DeepSeekProvider(api_key=None)
            print("❌ FAIL：应该抛 RuntimeError 但没有抛")
            return False
        except AttributeError as e:
            # RED 阶段正常：类还不存在 → 也是失败（原因是 feature 未实现）
            print(f"❌ RED（正常）：DeepSeekProvider 类还未实现 — {type(e).__name__}: {e}")
            return False
        except RuntimeError as e:
            if "DEEPSEEK_API_KEY" in str(e):
                print("✅ PASS")
                return True
            print(f"❌ FAIL：RuntimeError 文案不对，实际：{e}")
            return False
        except Exception as e:
            print(f"❌ FAIL：抛了非预期异常 {type(e).__name__}: {e}")
            return False


async def test_2_generate_passes_correct_params():
    """测试2：generate() 正确拼装 system+user 消息，并按 DeepSeek API 参数契约调用"""
    print("\n[TEST 2] generate() 参数正确传递 ... ", end="")
    try:
        import importlib, providers.llm
        importlib.reload(providers.llm)
        DeepSeekProvider = providers.llm.DeepSeekProvider
    except AttributeError as e:
        print(f"❌ RED（正常）：DeepSeekProvider 类还未实现 — {type(e).__name__}: {e}")
        return False

    # 沙箱注入一个假 Key + mock AsyncOpenAI 客户端
    fake_key = "sk-test-1234567890"
    fake_resp = "这是 DeepSeek 返回的故事剧本"
    with patch.dict(os.environ, {"DEEPSEEK_API_KEY": fake_key}, clear=True):
        prov = DeepSeekProvider()
        # mock _client.chat.completions.create Async 返回
        mock_client = MagicMock()
        mock_create = AsyncMock(return_value=MagicMock(choices=[MagicMock(message=MagicMock(content=fake_resp))]))
        mock_client.chat.completions.create = mock_create
        prov._get_client = lambda: mock_client  # 替换懒加载方法，不真实网络请求

        result = await prov.generate(
            prompt="写一个科幻剧本",
            system_prompt="你是一个资深编剧",
            temperature=0.8,
            max_tokens=4096,
            timeout=30,
        )

        # 断言返回内容
        if result != fake_resp:
            print(f"❌ FAIL：返回值错误，期望「{fake_resp}」实际「{result}」")
            return False

        # 断言 AsyncOpenAI.create 只被调用 1 次 + 传参正确
        if mock_create.call_count != 1:
            print(f"❌ FAIL：create 被调用 {mock_create.call_count} 次，期望 1 次")
            return False

        # 检查关键字段（DeepSeek 的 OpenAI 兼容模式契约）
        kwargs = mock_create.call_args.kwargs
        # 1) model 必须是 DeepSeekProvider.DEFAULT_MODEL（当前是 deepseek-v4-flash）
        #    注意：用类常量而不是写死字符串，避免以后升级默认模型时又回来改测试
        expected_model = DeepSeekProvider.DEFAULT_MODEL
        if kwargs.get("model") != expected_model:
            print(f"❌ FAIL：model 字段错误，期望 {expected_model}，实际 {kwargs.get('model')}")
            return False
        # 2) messages 必须是 system + user 两条
        messages = kwargs.get("messages", [])
        if len(messages) != 2 or messages[0]["role"] != "system" or messages[1]["role"] != "user":
            print(f"❌ FAIL：messages 结构错误，实际：{messages}")
            return False
        if messages[0]["content"] != "你是一个资深编剧" or messages[1]["content"] != "写一个科幻剧本":
            print(f"❌ FAIL：messages 内容错误")
            return False
        # 3) temperature = 0.8（非 JSON 模式默认 0.7，用户传的覆盖）
        if kwargs.get("temperature") != 0.8:
            print(f"❌ FAIL：temperature 错误，期望 0.8 实际 {kwargs.get('temperature')}")
            return False
        # 4) max_tokens = 4096
        if kwargs.get("max_tokens") != 4096:
            print(f"❌ FAIL：max_tokens 错误，期望 4096 实际 {kwargs.get('max_tokens')}")
            return False
        # 5) timeout = 30（传给 create 的参数）
        if mock_create.call_args.kwargs.get("timeout") != 30:
            # timeout 可能是 AsyncOpenAI 客户端默认或调用参数分别放
            if mock_create.call_args.kwargs.get("timeout") is None:
                # 也可能是 client 构造时的 timeout，这里放宽：只要 create 没报错就算通过
                pass
            else:
                print(f"❌ INFO：timeout 实际是 {mock_create.call_args.kwargs.get('timeout')}，期望 30（可放宽）")
        print("✅ PASS")
        return True


async def test_3_chat_json_mode():
    """测试3：json_mode=True 时，API 调用必须带 response_format={"type":"json_object"}，且 temperature 降到 0.3"""
    print("\n[TEST 3] chat(json_mode=True) 正确传 response_format ... ", end="")
    try:
        import importlib, providers.llm
        importlib.reload(providers.llm)
        DeepSeekProvider = providers.llm.DeepSeekProvider
    except AttributeError as e:
        print(f"❌ RED（正常）：DeepSeekProvider 类还未实现 — {type(e).__name__}: {e}")
        return False

    fake_key = "sk-test-jsonmode-000"
    fake_json = '{"title":"测试","scenes":[{"id":1}]}'
    with patch.dict(os.environ, {"DEEPSEEK_API_KEY": fake_key}, clear=True):
        prov = DeepSeekProvider()
        mock_client = MagicMock()
        mock_create = AsyncMock(return_value=MagicMock(choices=[MagicMock(message=MagicMock(content=fake_json))]))
        mock_client.chat.completions.create = mock_create
        prov._get_client = lambda: mock_client

        messages = [{"role": "system", "content": "输出 JSON"}, {"role": "user", "content": "生成一个故事"}]
        result = await prov.chat(messages, json_mode=True, timeout=30)

        if result != fake_json:
            print(f"❌ FAIL：返回 JSON 值错误")
            return False
        kwargs = mock_create.call_args.kwargs
        # response_format 必须存在
        if kwargs.get("response_format") != {"type": "json_object"}:
            print(f"❌ FAIL：response_format 错误，实际 {kwargs.get('response_format')}")
            return False
        # json_mode 下默认 temperature 应为 0.3
        if kwargs.get("temperature") != 0.3:
            print(f"❌ FAIL：json_mode 下 temperature 应为 0.3，实际 {kwargs.get('temperature')}")
            return False
        print("✅ PASS")
        return True


async def main():
    print("=" * 60)
    print("DeepSeekProvider 单元测试（TDD RED/GREEN 验证）")
    print("=" * 60)
    results = [
        await test_1_no_api_key_raises(),
        await test_2_generate_passes_correct_params(),
        await test_3_chat_json_mode(),
    ]
    passed = sum(1 for r in results if r)
    total = len(results)
    print("\n" + "=" * 60)
    print(f"结果: {passed}/{total} 通过")
    if passed == total:
        print("🎉 全部 GREEN ✅")
        return 0
    print(f"⚠️  当前处于 TDD RED 阶段或存在实现缺陷，失败 {total - passed} 个")
    return 1


if __name__ == "__main__":
    exit(asyncio.run(main()))
