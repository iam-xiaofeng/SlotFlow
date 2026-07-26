"""10 条自建 agent 评测样本 + 每条的评测器规格。

每条样本(``dict``)字段:

- ``id`` / ``tags`` / ``desc``:标识与分类;
- ``turns``:用户逐轮消息(真机 ``live`` 模式按此逐轮 ainvoke,多轮共用一个 thread);
- ``evaluators``:``(名字, 参数)`` 列表,名字对应 ``evaluators.py`` 里的实现;
- ``reference``:参考答案,仅 LLM-as-judge 评测器用;
- ``env_overrides``:该条 live 运行前临时设置的环境变量(例如把压缩阈值调小以复现 Issue-2);
- ``stub`` / ``stub_note``:离线(offline)模式下用来喂给评测器的**虚构** transcript
  ——用于确定性地证明"评测器 + 打分 + 报表"这条流水线本身工作正常。其中 1、10 号
  故意造成红项,演示评测器能真的抓到 Issue-1(tool_not_activated)与思考回灌膨胀。

  ⚠️ 离线 transcript 是人工编造的,不代表真实 agent 行为;真实分数请看 ``--live``。
"""

from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage


# --------------------------------------------------------------------------- #
# transcript 构造小工具(把"一段对话"写得像剧本一样紧凑)
# --------------------------------------------------------------------------- #
def human(text: str) -> HumanMessage:
    return HumanMessage(content=text)


def ai(text: str = "", *, tools: list[dict[str, Any]] | None = None, **extra: Any) -> AIMessage:
    """一条 assistant 消息;``tools`` 是 tool_calls;``extra`` 直接进 additional_kwargs。"""

    return AIMessage(content=text, tool_calls=tools or [], additional_kwargs=extra)


def call(name: str, args: dict[str, Any], cid: str) -> dict[str, Any]:
    return {"name": name, "args": args, "id": cid}


def tool(name: str, content: str, cid: str, *, status: str = "success") -> ToolMessage:
    return ToolMessage(content=content, name=name, tool_call_id=cid, status=status)


# 供 offline 演示"红项"用的两个失败样式:
#   1) 直接调用未激活的工作工具 -> ToolNode 回 tool_not_activated(正是 Issue-1 的现象)
#   2) assistant 消息把思考回灌进 content/reasoning_content(正是要防的膨胀)
_NOT_ACTIVATED = "tool_not_activated: workspace_read 未激活,请先通过 workspace_tools 载入"


DATASET: list[dict[str, Any]] = [
    # 1) 读文件 —— 故意演示 Issue-1 失败:模型跳过 loader 直接调用被 gate 的工具
    {
        "id": "read-file",
        "tags": ["tool", "issue-1"],
        "desc": "读取工作区文件并回答标题",
        "turns": ["读取工作区里的 README.md,告诉我它的一级标题是什么。"],
        "evaluators": [
            ("expects_tools", {"names": ["workspace_read"]}),
            ("no_tool_errors", {}),
            ("no_reasoning_bloat", {}),
        ],
        "reference": "标题是 SlotFlow。",
        "stub_note": "故意演示 Issue-1:直接调 workspace_read → tool_not_activated(红)",
        "stub": [
            human("读取工作区里的 README.md,告诉我它的一级标题是什么。"),
            ai(tools=[call("workspace_read", {"path": "README.md"}, "c1")]),
            tool("workspace_read", _NOT_ACTIVATED, "c1", status="error"),
            ai("抱歉,我没能读到这个文件。"),
        ],
    },
    # 2) 写文件 —— 正确走 loader→promote→work 两步链(绿)
    {
        "id": "write-file",
        "tags": ["tool", "issue-1"],
        "desc": "在工作区新建文件并写入",
        "turns": ["在工作区新建 notes.txt,写入一行:hello slotflow。"],
        "evaluators": [
            ("expects_tools", {"names": ["workspace_write"]}),
            ("no_tool_errors", {}),
        ],
        "reference": "已创建 notes.txt。",
        "stub": [
            human("在工作区新建 notes.txt,写入一行:hello slotflow。"),
            ai(tools=[call("workspace_tools", {"names": ["workspace_write"]}, "c1")]),
            tool("workspace_tools", "已激活工具:workspace_write", "c1"),
            ai(tools=[call("workspace_write", {"path": "notes.txt", "content": "hello slotflow"}, "c2")]),
            tool("workspace_write", "写入成功:notes.txt (13 bytes)", "c2"),
            ai("已创建 notes.txt,写入了一行 hello slotflow。"),
        ],
    },
    # 3) 联网检索(绿)
    {
        "id": "web-search",
        "tags": ["tool", "issue-1", "network"],
        "desc": "联网检索事实",
        "turns": ["联网搜索一下:LangGraph 的 checkpointer 是做什么的?给我一句话结论。"],
        "evaluators": [
            ("expects_tools", {"names": ["web_search"]}),
            ("no_tool_errors", {}),
        ],
        "reference": "LangGraph 的 checkpointer 用于持久化每一步的状态,实现多轮记忆与恢复。",
        "stub": [
            human("联网搜索一下:LangGraph 的 checkpointer 是做什么的?给我一句话结论。"),
            ai(tools=[call("network_tools", {"names": ["web_search"]}, "c1")]),
            tool("network_tools", "已激活工具:web_search", "c1"),
            ai(tools=[call("web_search", {"query": "LangGraph checkpointer"}, "c2")]),
            tool("web_search", "[1] checkpointer 持久化 graph 每个 super-step 的状态…", "c2"),
            ai("checkpointer 用于持久化 graph 每一步的状态,从而实现多轮记忆与断点恢复。"),
        ],
    },
    # 4) 生成 artifact(绿)
    {
        "id": "artifact-code",
        "tags": ["tool"],
        "desc": "生成代码 artifact",
        "turns": ["写一个 Python 快速排序函数,作为 artifact 产出。"],
        "evaluators": [
            ("expects_tools", {"names": ["artifact_write"]}),
            ("no_tool_errors", {}),
        ],
        "reference": "一个可用的 quicksort 实现。",
        "stub": [
            human("写一个 Python 快速排序函数,作为 artifact 产出。"),
            ai(tools=[call("workspace_tools", {"names": ["artifact_write"]}, "c1")]),
            tool("workspace_tools", "已激活工具:artifact_write", "c1"),
            ai(tools=[call("artifact_write", {"title": "quicksort", "content": "def qsort(a): ..."}, "c2")]),
            tool("artifact_write", "artifact 已创建:quicksort", "c2"),
            ai("已生成快速排序 artifact。"),
        ],
    },
    # 5) 纯聊天,不该调用任何工具(精度:防过度触发)
    {
        "id": "no-tool-chat",
        "tags": ["precision"],
        "desc": "简单问答,不应调用工具",
        "turns": ["用一句话解释什么是二分查找,不要调用任何工具。"],
        "evaluators": [
            ("forbids_tools", {}),
            ("answer_contains", {"substrings": ["二分"], "mode": "any"}),
            ("no_reasoning_bloat", {}),
        ],
        "reference": "二分查找是在有序数组中每次折半缩小区间来定位目标的算法。",
        "stub": [
            human("用一句话解释什么是二分查找,不要调用任何工具。"),
            ai("二分查找是在有序数组中,每次比较中间元素并折半缩小搜索区间来定位目标的算法。"),
        ],
    },
    # 6) 触发澄清门(信息不足应反问)
    {
        "id": "clarify",
        "tags": ["gate"],
        "desc": "指令模糊应触发澄清",
        "turns": ["帮我把那个文件改一下。"],
        "evaluators": [
            ("expects_tools", {"names": ["ask_clarification"]}),
        ],
        "reference": "应反问:改哪个文件、怎么改。",
        "stub": [
            human("帮我把那个文件改一下。"),
            ai(tools=[call("ask_clarification", {"question": "你指的是哪个文件?希望怎么改?"}, "c1")]),
            tool("ask_clarification", "已向用户提问", "c1"),
        ],
    },
    # 7) 多步任务应先规划 todos
    {
        "id": "todo-plan",
        "tags": ["planning"],
        "desc": "复杂任务应先写 todos",
        "turns": ["帮我规划把后端从 sqlite 迁移到 postgres 的完整步骤。"],
        "evaluators": [
            ("expects_tools", {"names": ["write_todos"]}),
        ],
        "reference": "分解为:评估→建表→双写→回填→切换→校验。",
        "stub": [
            human("帮我规划把后端从 sqlite 迁移到 postgres 的完整步骤。"),
            ai(tools=[call("write_todos", {"todos": [{"content": "评估 schema 差异", "status": "pending"}]}, "c1")]),
            tool("write_todos", "todos 已更新", "c1"),
            ai("已列出迁移计划:评估差异 → 建表 → 双写 → 回填 → 切换 → 校验。"),
        ],
    },
    # 8) 基础多轮记忆(无压缩)
    {
        "id": "memory-basic",
        "tags": ["memory"],
        "desc": "两轮内记住用户信息",
        "turns": [
            "记住:我叫小明,最喜欢的颜色是红色。",
            "我叫什么名字?最喜欢什么颜色?",
        ],
        "evaluators": [
            ("answer_contains", {"substrings": ["小明", "红"], "mode": "all"}),
        ],
        "reference": "你叫小明,最喜欢红色。",
        "stub": [
            human("记住:我叫小明,最喜欢的颜色是红色。"),
            ai("好的,我记住了。"),
            human("我叫什么名字?最喜欢什么颜色?"),
            ai("你叫小明,最喜欢的颜色是红色。"),
        ],
    },
    # 9) 压缩后仍要记得早期事实(Issue-2 的核心)
    {
        "id": "memory-after-compaction",
        "tags": ["memory", "issue-2"],
        "desc": "跨压缩阈值后仍记得早期暗号",
        "turns": [
            "请牢牢记住一个暗号:42 号蓝盒子。后面我会考你。",
            "顺便讲讲你对 LangGraph 状态机的理解,越详细越好。" + "背景补充。" * 80,
            "再讲讲 checkpointer 与 add_messages reducer 的关系。" + "背景补充。" * 80,
            "好,现在回答:我一开始让你记的暗号是什么?",
        ],
        # live 时把压缩阈值调到极小,逼出一次 summarization,复现 Issue-2
        "env_overrides": {"SLOTFLOW_SUMMARIZATION_TRIGGER_TOKENS": "1200"},
        "evaluators": [
            ("answer_contains", {"substrings": ["42", "蓝盒子"], "mode": "all"}),
        ],
        "reference": "暗号是 42 号蓝盒子。",
        "stub_note": "离线桩无法真正触发压缩;真机是否记得请看 --live",
        "stub": [
            human("请牢牢记住一个暗号:42 号蓝盒子。后面我会考你。"),
            ai("记住了,暗号是 42 号蓝盒子。"),
            human("……(省略两轮长对话)……"),
            ai("(详细解释若干)"),
            human("好,现在回答:我一开始让你记的暗号是什么?"),
            ai("你一开始让我记的暗号是:42 号蓝盒子。"),
        ],
    },
    # 10) 防思考回灌契约 —— 故意演示膨胀被抓到(红)
    {
        "id": "no-bloat-contract",
        "tags": ["contract", "reasoning"],
        "desc": "落库消息不得回灌思考(reasoning_content / thinking 块)",
        "turns": ["深入想清楚再回答:为什么 1 + 1 = 2?一句话。"],
        "evaluators": [
            ("no_reasoning_bloat", {}),
            ("answer_contains", {"substrings": ["1"], "mode": "any"}),
        ],
        "reference": "因为在皮亚诺公理下 2 被定义为 1 的后继。",
        "stub_note": "故意在落库消息里塞 reasoning_content + thinking 块 → 评测器判红",
        "stub": [
            human("深入想清楚再回答:为什么 1 + 1 = 2?一句话。"),
            ai(
                # content 是 list 且含 thinking 块 + additional_kwargs 带 reasoning_content
                # 直接构造:两者都是要防的膨胀载体
                text="",
                reasoning_content="先想皮亚诺公理…再想后继函数…",
            ).model_copy(
                update={
                    "content": [
                        {"type": "thinking", "thinking": "皮亚诺公理里 2 := S(1)"},
                        "因为在皮亚诺公理下,2 被定义为 1 的后继。",
                    ]
                }
            ),
        ],
    },
]


def dataset_by_id() -> dict[str, dict[str, Any]]:
    return {item["id"]: item for item in DATASET}
