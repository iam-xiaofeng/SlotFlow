"""DeepSeek live smoke test。

这个测试默认跳过，因为它会真的访问 DeepSeek API，依赖网络、API key、余额和模型服务。

手动运行方式：

```bash
cd ~/code/SlotFlow/backend
SLOTFLOW_LIVE_DEEPSEEK=1 DEEPSEEK_API_KEY=... uv run pytest tests/test_live_deepseek.py -q
```

不要把这个测试放进 `make verify` 的必经路径。它的职责只是证明：

DeepSeek OpenAI-compatible API
-> LangChain create_agent
-> LangGraph v3 astream_events
-> SlotFlow AgentEvent
"""

from __future__ import annotations

import os

import pytest

from app.chat.agent_adapter import collect_agent_events
from app.chat.models import ChatStreamRequest
from app.chat.run_config import build_run_config
from app.chat.runtime import RuntimeBackedAgentAdapter, SlotFlowRuntimeConfig


pytestmark = pytest.mark.skipif(
    os.environ.get("SLOTFLOW_LIVE_DEEPSEEK") != "1",
    reason="set SLOTFLOW_LIVE_DEEPSEEK=1 to run live DeepSeek smoke test",
)


@pytest.mark.asyncio
async def test_deepseek_agent_adapter_streams_v3_events() -> None:
    """真实调用一次 DeepSeek，验证 SlotFlow 的 v3 adapter 能跑通。"""

    if not os.environ.get("DEEPSEEK_API_KEY"):
        pytest.skip("DEEPSEEK_API_KEY is required for live DeepSeek smoke test")

    model_name = os.environ.get("SLOTFLOW_DEEPSEEK_MODEL", "deepseek-v4-flash")
    request = ChatStreamRequest(
        message="用一句中文短句回答：SlotFlow 后端 smoke test 是否通过？",
        model_name=model_name,
        mode="flash",
    )
    bundle = build_run_config(
        thread_id="thread_live_deepseek",
        run_id="run_live_deepseek",
        request=request,
    )
    adapter = RuntimeBackedAgentAdapter(
        SlotFlowRuntimeConfig(
            adapter_mode="deepseek",
            model_name=model_name,
            checkpointer_backend="memory",
        )
    )

    events = await collect_agent_events(adapter.stream_events(request=request, bundle=bundle))
    event_names = [event.event for event in events]

    assert event_names[0] == "run.prepared"
    assert event_names[-1] == "run.finished"
    assert "message.delta" in event_names or "state.snapshot" in event_names
