"""SlotFlow 聊天后端的数据形状。

这一层只回答一个问题：前端、后端、agent、测试之间传递的数据到底长什么样？

旧项目里很多复杂度来自“同一份数据同时要适配多个协议”。SlotFlow 第一阶段先
把业务自己的形状定下来：thread 是一轮会话，message 是会话里的消息，run 是
一次流式执行。后续无论真实 agent 内部怎么变，FastAPI 边界都尽量保持这个形状。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


ChatMode = Literal["flash", "pro", "ultra"]
MessageRole = Literal["user", "assistant", "system", "tool"]
RunStatus = Literal["queued", "running", "completed", "failed", "cancelled"]


def utc_now() -> datetime:
    """返回当前 UTC 时间。

    用一个小函数包起来，是为了让所有记录的时间来源一致。以后如果测试需要固定
    时间，或者要换成数据库自动时间戳，也只需要调整这一处附近的代码。
    """

    return datetime.now(UTC)


class ThreadCreateRequest(BaseModel):
    """创建空会话 thread 时的请求体。

    这个模型后面会交给 FastAPI 使用。现在先把它放在模块一，是为了提前固定
    API 请求里“创建会话”这件事的最小输入。
    """

    title: str | None = Field(
        default=None,
        description="可选标题，后面会显示在前端会话列表里。",
    )


class ThreadRecord(BaseModel):
    """一条会话容器。

    可以把 thread 想成 ChatGPT 左侧列表里的一条会话。它自己不直接回答问题，
    只是把这个会话下面的 messages 和 runs 串起来。
    """

    id: str
    title: str
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class MessageRecord(BaseModel):
    """thread 里保存下来的一条消息。

    第一阶段只保存文本。等真实 harness 接上以后，`metadata` 可以承接工具调用、
    图片附件、引用来源等附加信息，而不用马上改动前端消息列表的主结构。
    """

    id: str
    thread_id: str
    role: MessageRole
    content: str
    run_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)


class ChatStreamRequest(BaseModel):
    """启动一次 assistant 流式运行时的请求体。

    这是前端点击“发送”时最核心的输入。现在字段很少，是故意的：

    - `message` 是用户这次说的话；
    - `model_name` 暂时只是透传到 run context，让你能看到配置怎么进入后端；
    - `mode` 模拟不同能力档位，后面会变成真实 feature flags；
    - `files` 先用字符串占位，未来可以替换为上传文件 ID。
    """

    message: str = Field(min_length=1)
    model_name: str = "deepseek-v4-flash"
    mode: ChatMode = "pro"
    agent_name: str = "default"
    files: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("message")
    @classmethod
    def message_must_not_be_blank(cls, value: str) -> str:
        """拒绝只有空白字符的提问，同时保留用户原始文本。"""

        if not value.strip():
            raise ValueError("message cannot be blank")
        return value


class RunRecord(BaseModel):
    """thread 里的一次执行记录。

    thread 是长期容器，run 是一次动作。比如用户连续问三次，就会有一个 thread
    和三个 run。把它们拆开以后，取消、重试、错误记录都会更清楚。
    """

    id: str
    thread_id: str
    status: RunStatus = "queued"
    model_name: str
    mode: ChatMode
    agent_name: str
    error: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class RunContext(BaseModel):
    """放在 graph config 旁边传递的运行时业务开关。

    LangGraph 的 `config["configurable"]` 更像“运行时定位牌”，例如 thread_id；
    而这里的 context 更像“本次运行的业务开关”，例如是否启用规划、子 agent。
    把这两类信息分开，后面接真实 harness 时会更容易判断该把字段放到哪里。
    """

    thread_id: str
    run_id: str
    model_name: str
    mode: ChatMode
    agent_name: str
    files: list[str] = Field(default_factory=list)
    thinking_enabled: bool
    is_plan_mode: bool
    subagent_enabled: bool


class RunConfigBundle(BaseModel):
    """调用 agent stream 时需要一起带上的两份对象。

    `config` 模拟 LangGraph RunnableConfig；`context` 是 SlotFlow 自己整理出的
    业务上下文。测试会专门保护这个边界，避免以后又把所有字段混成一团。
    """

    config: dict[str, Any]
    context: RunContext
