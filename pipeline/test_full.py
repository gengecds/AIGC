#!/usr/bin/env python3
"""
完整管线测试脚本（Mock 模式，无需 GPU）

用法：
  python3 pipeline/test_full.py
"""

import sys
sys.path.insert(0, '.')
import os
import json
import asyncio

os.environ['DEEPSEEK_API_KEY'] = ''  # 在环境变量中设置

from agents.script_agent import ScriptAgent
from agents.storyboard_agent import StoryboardAgent
from agents.character_agent import CharacterDesignAgent
from agents.image_agent import ImageGenAgent
from agents.video_agent import VideoGenAgent
from agents.subtitle_agent import SubtitleAgent
from agents.compose_agent import ComposeAgent
from pipeline.scheduler import Pipeline, PipelineState


async def main():
    story = '一个程序员意外穿越到全是猫娘的世界，被当成稀有物种保护起来'

    agents = [
        ScriptAgent('deepseek'),
        StoryboardAgent('deepseek'),
        CharacterDesignAgent(),
        ImageGenAgent(use_comfyui=False),
        VideoGenAgent(use_comfyui=False),
        SubtitleAgent(),
        ComposeAgent(),
    ]

    # 先清掉旧 checkpoints
    import shutil
    shutil.rmtree('storage/checkpoints', ignore_errors=True)

    pipeline = Pipeline(PipelineState('storage/checkpoints'))

    # 只生成1集，避免耗时太长
    class SingleScriptAgent(ScriptAgent):
        async def run(self, user_input):
            r = await super().run(user_input)
            if r.success and r.data and 'episodes' in r.data:
                r.data['episodes'] = r.data['episodes'][:1]
                print(f'🎯 只保留第1集，共{len(r.data["episodes"])}集')
            return r

    agents[0] = SingleScriptAgent('deepseek')

    result = await pipeline.run(agents, story)

    print()
    print('=' * 60)
    print('📊  完整管线结果')
    print('=' * 60)

    if not result['success']:
        print(f'❌ 失败于: {result["failed_at"]}')
        print(json.dumps(result['results'], ensure_ascii=False, indent=2)[:1000])
        return

    results = result['results']
    pipeline_id = result['pipeline_id']
    print(f'🆔 Pipeline: {pipeline_id}')
    print()

    for name in ['script_agent', 'storyboard_agent', 'character_agent',
                 'image_agent', 'video_agent', 'subtitle_agent', 'compose_agent']:
        r = results.get(name, {})
        meta = r.get('metadata', {})
        status = '✅' if r.get('success', False) else '❌'
        data = r.get('data', {})

        if name == 'script_agent':
            print(f'{status} Agent 1 剧本')
            print(f'   标题: {data.get("title", "?")}')
            print(f'   角色: {meta.get("characters", 0)}, 集: {meta.get("episodes", 0)}')
        elif name == 'storyboard_agent':
            total_shots = meta.get('total_shots', 0)
            print(f'{status} Agent 2 分镜')
            print(f'   总镜头: {total_shots}')
        elif name == 'character_agent':
            print(f'{status} Agent 3 定妆照')
            print(f'   生成: {meta.get("generated", 0)}, 复用: {meta.get("skipped", 0)}')
        elif name == 'image_agent':
            img_count = meta.get('total_images', 0)
            print(f'{status} Agent 4 出图')
            print(f'   图片: {img_count}张')
        elif name == 'video_agent':
            vid_count = meta.get('total_videos', 0)
            print(f'{status} Agent 5 视频')
            print(f'   视频: {vid_count}段')
        elif name == 'subtitle_agent':
            sub_files = meta.get('files', 0)
            print(f'{status} Agent 6 字幕')
            print(f'   文件: {sub_files}个SRT')
        elif name == 'compose_agent':
            ep_count = meta.get('episodes', 0)
            pub = data.get('published', [])
            print(f'{status} Agent 7 合成')
            for p in pub:
                print(f'   ep{p["episode_number"]}: {p["final_path"]} ({p["segments"]}段)')

    print()
    print('💡 切换为 ComfyUI 生产模式:')
    print('   修改 config.yaml: engine.image_provider=\"comfyui\", engine.video_provider=\"comfyui\"')
    print('   或 new ImageGenAgent(use_comfyui=True) / new VideoGenAgent(use_comfyui=True)')


if __name__ == '__main__':
    asyncio.run(main())
