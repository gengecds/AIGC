---
description: AIGC 管线断点（checkpoint）与 resume 的语义陷阱——改断点内容不会触发重跑
alwaysApply: true
---

# 管线断点与续跑规则

## 核心事实：resume 的起点只看「最后一个存在断点的 agent」，不看断点内容

`pipeline/scheduler.py` 的 resume 分支：

```python
last_agent = self.state.get_last_completed_agent(AGENTS_PIPELINE)
start_idx = AGENTS_PIPELINE.index(last_agent) + 1
agents_to_run = [a for a in agents if a.name in AGENTS_PIPELINE[start_idx:]]
```

`get_last_completed_agent()` 只判断 `storage/checkpoints/{agent}_checkpoint.json` **文件是否存在**，
完全不检查里面的 data。

### 因此这两个操作都是错的

| 错误操作 | 实际后果 |
|---|---|
| 手工删掉断点里的部分条目（例如只想重出缺失的 5 个镜头）后再 resume | 管线**不会**重跑该 agent，直接从下一个 agent 继续 → 用残缺数据往下跑 |
| 手改断点后调 `POST /approve/{pid}` | approve 用**内存里的旧数据**覆盖你手改的断点，修改全部丢失 |

## 正确做法

| 目的 | 操作 |
|---|---|
| 只改断点数据，执行起点不变 | 改完断点 → **重新 `POST /api/v1/pipeline/run` 带 `"resume": true`**（不要用 approve） |
| 让某个 agent 整个重跑 | **删掉该 agent 及其后所有** `{agent}_checkpoint.json`，再 resume |
| 续跑前清理 | 旧管线还挂在 `active` 时同输入提交会返回 `{"duplicated":true}`，先 `POST /cancel/{pid}` |

## 附带约束

- checkpoint 是**全局共享**的（`storage/checkpoints/`，不按 pipeline_id 隔离），两条管线同时跑会互相覆盖。
- 改 `.py` 会触发 uvicorn 热重载并**打断正在跑的管线**；改 `.yaml` 不触发热重载，需 touch 一个 `.py`。

## 排查手法（图没出全时先做这个）

不要急着重跑（FLUX 15.3 分钟/张，代价极大），先确认图是不是已经生成过：

- ComfyUI `/history` 能反查每次出图的 `prompt_id` → 输出文件名 + 完整提示词；
- 拿 `prompt_id` 与 `storage/output/images/` 里已落盘的 PNG 对号入座，把缺失条目**回填断点**即可，
  比重新出图省 1~2 小时。
