"""给学习版后端使用的小型 ID 工具。

真实产品通常会把 ID 交给数据库、LangGraph run store 或者分布式 ID 服务。
SlotFlow 第一阶段还没有这些东西，所以这里用一个很直白的规则：

    thread_ + 随机短串
    msg_    + 随机短串
    run_    + 随机短串

这样做有两个好处：

1. 看日志和测试输出时，一眼能知道这个 ID 属于哪类对象；
2. 以后切换到数据库 ID 或 LangGraph 的 run_id 时，只需要换这一层。
"""

from __future__ import annotations

from uuid import uuid4


def new_id(prefix: str) -> str:
    """生成一个带类型前缀的可读 ID。

    `uuid4().hex` 足够随机，但完整 32 位在教学日志里太长。这里保留前 12 位，
    对本地学习和单进程测试来说已经足够，同时输出更容易扫读。
    """

    return f"{prefix}_{uuid4().hex[:12]}"


def new_thread_id() -> str:
    """生成一条会话 thread 的 ID。"""

    return new_id("thread")


def new_message_id() -> str:
    """生成一条持久化 message 的 ID。"""

    return new_id("msg")


def new_run_id() -> str:
    """生成一次流式运行 run 的 ID。"""

    return new_id("run")
