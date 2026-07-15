"""SlotFlow 聊天后端的数据形状。

这一层只回答一个问题：前端、后端、agent、测试之间传递的数据到底长什么样？

生产系统里很多复杂度来自“同一份数据同时要适配多个协议”。SlotFlow 在 API 边界
固定业务自己的形状：thread 是一轮会话，message 是会话里的消息，run 是
一次流式执行。后续无论真实 agent 内部怎么变，FastAPI 边界都尽量保持这个形状。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from app.clock import utc_now


ChatMode = Literal["flash", "pro", "ultra"]
MessageRole = Literal["user", "assistant", "system", "tool"]
RunStatus = Literal["queued", "running", "completed", "failed", "cancelled"]
ModelProvider = str


class ThreadCreateRequest(BaseModel):
    """创建空会话 thread 时的请求体。

    这个模型会交给 FastAPI 使用，用来固定 API 请求里“创建会话”这件事的最小输入。
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

    主字段只保存文本。`metadata` 可以承接工具调用、
    图片附件、引用来源等附加信息，而不用马上改动前端消息列表的主结构。
    """

    id: str
    thread_id: str
    role: MessageRole
    content: str
    run_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)


class ThreadSearchResultRecord(BaseModel):
    """A thread search hit from either the title or a stored message."""

    thread: ThreadRecord
    message: MessageRecord | None = None
    match_type: Literal["title", "message"]
    snippet: str
    score: int = 0


class ChatStreamRequest(BaseModel):
    """启动一次 assistant 流式运行时的请求体。

    这是前端点击“发送”时最核心的输入。字段保持小而明确：

    - `message` 是用户这次说的话；
    - `model_name` 是本轮由前端选择的模型；
    - `mode` 表示本轮能力档位；
    - `files` 是上传 API 返回的 file_id 列表，stream 路由会解析成上传元数据。
    """

    message: str = Field(min_length=1)
    model_name: str = "deepseek/deepseek-v4-pro"
    provider: ModelProvider | None = Field(
        default=None,
        description="可选：前端所选模型在 catalog 中的来源 provider。为空时后端按 model id 前缀推断。",
    )
    mode: ChatMode = "pro"
    thinking_enabled: bool | None = Field(
        default=None,
        description="可选：是否启用模型原生思考。为空时沿用 mode 的默认规则。",
    )
    agent_name: str = "default"
    files: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    reuse_user_message_id: str | None = Field(
        default=None,
        description="可选：复用并覆盖一条已有 user message，用于编辑或重试最后一轮。",
    )

    @field_validator("message")
    @classmethod
    def message_must_not_be_blank(cls, value: str) -> str:
        """拒绝只有空白字符的提问，同时保留用户原始文本。"""

        if not value.strip():
            raise ValueError("message cannot be blank")
        return value


class ModelOptionRecord(BaseModel):
    """A selectable model discovered from a configured provider."""

    id: str
    provider: ModelProvider
    label: str
    available: bool = True
    source: str = "catalog"


class ModelProviderRecord(BaseModel):
    """Frontend-facing provider status without exposing secrets."""

    provider: ModelProvider
    configured: bool
    base_url: str | None = None
    status: Literal["available", "fallback", "missing", "error"]
    message: str | None = None
    models: list[ModelOptionRecord] = Field(default_factory=list)


class ModelCatalogRecord(BaseModel):
    """Model discovery response used by the chat composer."""

    default_model: str
    providers: list[ModelProviderRecord] = Field(default_factory=list)


class UploadedFileContext(BaseModel):
    """一次 run 中已经解析过的上传文件引用。

    外部 API 仍然只接收 `files: list[str]`，这些字符串是上传 API 返回的 file_id。
    stream 路由会在创建 run 之前把 file_id 解析成这份结构化元数据。这样 agent/runtime
    能知道文件在 workspace 里的安全相对路径，但业务数据库仍然只保存元数据，不保存
    文件二进制。
    """

    id: str
    filename: str
    original_filename: str | None = None
    content_type: str | None = None
    size_bytes: int
    workspace_path: str
    created_at: datetime = Field(default_factory=utc_now)


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
    model_provider: ModelProvider | None = None
    mode: ChatMode
    agent_name: str
    files: list[str] = Field(default_factory=list)
    uploaded_files: list[UploadedFileContext] = Field(default_factory=list)
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
