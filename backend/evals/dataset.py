"""20 条自建 agent 评测样本 + 每条的评测器规格。

每条样本(``dict``)字段:

- ``id`` / ``tags`` / ``desc``:标识与分类;
- ``turns``:用户逐轮消息(真机 ``live`` 模式按此逐轮 ainvoke,多轮共用一个 thread);
- ``evaluators``:``(名字, 参数)`` 列表,名字对应 ``evaluators.py`` 里的实现;
- ``reference``:参考答案,仅 LLM-as-judge 评测器用;
- ``env_overrides``:该条 live 运行前临时设置的环境变量(例如把压缩阈值调小以复现 Issue-2);
- ``workspace_files``:live 前预置到该条评测工作区的文件(``相对路径 -> 内容``);
- ``stub`` / ``stub_note``:离线(offline)模式下用来喂给评测器的**虚构** transcript
  ——用于确定性地证明"评测器 + 打分 + 报表"这条流水线本身工作正常。

  ⚠️ 离线 transcript 是人工编造的,不代表真实 agent 行为;真实分数请看 ``--live``。

**工具名的唯一事实源是运行期 registry**(``build_harness_tools``,当前 30 个)。
2026-08-14 复查发现旧数据集有一条 ``write-file`` 期望 ``workspace_write`` —— **这个工具从来
不存在**,那条样本永远不可能通过;另有两条离线桩还在演示早已删除的 ``network_tools`` /
``workspace_tools`` loader。所以本文件里出现的每个工具名都必须能在 registry 里找到,
``tests/test_evals_dataset.py`` 会把这条钉死。
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


_README = "# SlotFlow\n\n本地优先的 AI Agent 工作台。\n\n## 架构\nLangGraph + FastAPI + Next.js。\n"
_BIG_HTML = "<!DOCTYPE html><html><body>" + ("<p>占位段落,用于把文件撑到分页阈值以上。</p>" * 900) + "</body></html>"

# offline 演示"红项"用的失败样式:工具调用本身失败 → ToolNode 回 tool_execution_error
_TOOL_FAILED = "tool_execution_error: workspace_read 打不开 missing.md(FileNotFoundError)"


DATASET: list[dict[str, Any]] = [
    # ======================================================================== #
    # A. 工具正确性(8 条)
    # ======================================================================== #
    {
        "id": "read-file",
        "tags": ["tool"],
        "desc": "读取工作区文件并回答标题",
        "turns": ["读取工作区里的 README.md,告诉我它的一级标题是什么。"],
        # 旧版这条必红:评测工作区里根本没有 README.md(README 里记为"环境缺口")。
        # 预置文件之后量的才是 agent 行为,而不是环境。
        "workspace_files": {"README.md": _README},
        "evaluators": [
            ("expects_tools", {"names": ["workspace_read"]}),
            ("no_tool_errors", {}),
            ("answer_contains", {"substrings": ["SlotFlow"], "mode": "any"}),
            ("no_thinking_blocks", {}),
        ],
        "reference": "一级标题是 SlotFlow。",
        "stub": [
            human("读取工作区里的 README.md,告诉我它的一级标题是什么。"),
            ai(tools=[call("workspace_read", {"path": "README.md"}, "c1")]),
            tool("workspace_read", '{"content": "# SlotFlow\\n…", "read": {"truncated": false}}', "c1"),
            ai("一级标题是 SlotFlow。"),
        ],
    },
    {
        "id": "read-file-paginated",
        "tags": ["tool", "context"],
        "desc": "大文件必须分页/检索,不能整段灌进上下文",
        "turns": ["工作区里有个 big.html,里面提到 SlotFlow 了吗?别把整个文件读进来。"],
        "workspace_files": {"big.html": _BIG_HTML},
        "evaluators": [
            ("expects_any_tool", {"names": ["workspace_read", "workspace_grep", "workspace_search"]}),
            # 2026-08-14 修的洞:workspace_read 曾经完全没有上限,446KB 文件整段内联成
            # 373K 字符的 ToolMessage(≈166k token),模型随后每次返回空响应、thread 被毒化。
            ("tool_result_capped", {"max_chars": 32_000}),
            ("no_empty_assistant", {}),
        ],
        "reference": "文件里没有提到 SlotFlow(只有占位段落)。",
        "stub_note": "演示分页读:第一次截断并给 next_offset",
        "stub": [
            human("工作区里有个 big.html,里面提到 SlotFlow 了吗?别把整个文件读进来。"),
            ai(tools=[call("workspace_grep", {"query": "SlotFlow", "path": "big.html"}, "c1")]),
            tool("workspace_grep", '{"matches": []}', "c1"),
            ai("没有,big.html 里没有出现 SlotFlow。"),
        ],
    },
    {
        "id": "grep-file",
        "tags": ["tool"],
        "desc": "在工作区文件里检索关键词",
        "turns": ["在工作区里搜一下哪个文件提到了 LangGraph。"],
        "workspace_files": {"README.md": _README},
        "evaluators": [
            ("expects_any_tool", {"names": ["workspace_grep", "workspace_search"]}),
            ("no_tool_errors", {}),
        ],
        "reference": "README.md 里提到了 LangGraph。",
        "stub": [
            human("在工作区里搜一下哪个文件提到了 LangGraph。"),
            ai(tools=[call("workspace_search", {"query": "LangGraph"}, "c1")]),
            tool("workspace_search", '{"matches": [{"path": "README.md"}]}', "c1"),
            ai("README.md 里提到了 LangGraph。"),
        ],
    },
    {
        "id": "artifact-code",
        "tags": ["tool"],
        "desc": "生成代码 artifact",
        "turns": ["写一个 Python 快速排序函数,作为 artifact 产出。"],
        "evaluators": [
            # 旧版这里期望的是 workspace_write —— registry 里没有这个工具,永远不可能通过。
            # 真正能落文件的是 artifact_write 和 sandbox_exec 两条路,都算对。
            ("expects_any_tool", {"names": ["artifact_write", "sandbox_exec"]}),
            ("no_tool_errors", {}),
        ],
        "reference": "一个可用的 quicksort 实现。",
        "stub": [
            human("写一个 Python 快速排序函数,作为 artifact 产出。"),
            ai(tools=[call("artifact_write", {"title": "quicksort", "content": "def qsort(a): ..."}, "c1")]),
            tool("artifact_write", "artifact 已创建:quicksort", "c1"),
            ai("已生成快速排序 artifact。"),
        ],
    },
    {
        "id": "sandbox-verify",
        "tags": ["tool", "sandbox"],
        "desc": "写代码并在沙箱里真的跑一遍验证",
        "turns": ["写一个判断回文的 Python 函数,并在沙箱里跑几个用例验证它是对的。"],
        "evaluators": [
            ("expects_tools", {"names": ["sandbox_exec"]}),
            ("no_tool_errors", {}),
            ("llm_judge", {}),
        ],
        "reference": "给出 is_palindrome 实现,并展示沙箱里用例通过的结果。",
        "stub": [
            human("写一个判断回文的 Python 函数,并在沙箱里跑几个用例验证它是对的。"),
            ai(tools=[call("sandbox_exec", {"code": "def is_pal(s): return s==s[::-1]\nprint(is_pal('aba'))"}, "c1")]),
            tool("sandbox_exec", '{"stdout": "True\\n", "exit_code": 0}', "c1"),
            ai("实现如上,沙箱里 'aba' 判定为 True,用例通过。"),
        ],
    },
    {
        "id": "convert-doc",
        "tags": ["tool"],
        "desc": "把非纯文本文档转成 markdown 再回答",
        "turns": ["把工作区里的 notes.md 转成 markdown 并总结要点。"],
        "workspace_files": {"notes.md": "# 会议纪要\n\n- 下周发布\n- 需要补测试\n"},
        "evaluators": [
            ("expects_any_tool", {"names": ["convert_file_to_markdown", "workspace_read"]}),
            ("no_tool_errors", {}),
        ],
        "reference": "要点:下周发布、需要补测试。",
        "stub": [
            human("把工作区里的 notes.md 转成 markdown 并总结要点。"),
            ai(tools=[call("convert_file_to_markdown", {"path": "notes.md"}, "c1")]),
            tool("convert_file_to_markdown", "# 会议纪要\n- 下周发布\n- 需要补测试", "c1"),
            ai("要点是:下周发布、需要补测试。"),
        ],
    },
    {
        "id": "web-search",
        "tags": ["tool", "network"],
        "desc": "联网检索事实",
        "turns": ["联网搜索一下:LangGraph 的 checkpointer 是做什么的?给我一句话结论。"],
        "evaluators": [
            ("expects_any_tool", {"names": ["web_search", "agent_reach_web_search"]}),
            ("no_tool_errors", {}),
            ("llm_judge", {}),
        ],
        "reference": "LangGraph 的 checkpointer 用于持久化每一步的状态,实现多轮记忆与恢复。",
        # 旧桩在这里演示 network_tools loader「已激活工具:web_search」——那套渐进式披露
        # 2026-08-14 已整体删除,工具集全程恒定,不再有 loader 往返。
        "stub": [
            human("联网搜索一下:LangGraph 的 checkpointer 是做什么的?给我一句话结论。"),
            ai(tools=[call("web_search", {"query": "LangGraph checkpointer"}, "c1")]),
            tool("web_search", "[1] checkpointer 持久化 graph 每个 super-step 的状态…", "c1"),
            ai("checkpointer 用于持久化 graph 每一步的状态,从而实现多轮记忆与断点恢复。"),
        ],
    },
    {
        "id": "web-fetch",
        "tags": ["tool", "network"],
        "desc": "抓取指定 URL 的正文",
        "turns": ["读一下 https://example.com 这个页面,告诉我它的主标题。"],
        "evaluators": [
            ("expects_any_tool", {"names": ["web_fetch", "agent_reach_read_url"]}),
            ("tool_result_capped", {"max_chars": 32_000}),
        ],
        "reference": "主标题是 Example Domain。",
        "stub": [
            human("读一下 https://example.com 这个页面,告诉我它的主标题。"),
            ai(tools=[call("web_fetch", {"url": "https://example.com"}, "c1")]),
            tool("web_fetch", "Example Domain\nThis domain is for use in illustrative examples…", "c1"),
            ai("主标题是 Example Domain。"),
        ],
    },
    # ======================================================================== #
    # B. 精度 / 收敛(4 条)—— 不该做的事就别做
    # ======================================================================== #
    {
        "id": "no-tool-chat",
        "tags": ["precision"],
        "desc": "简单问答,不应调用工具",
        "turns": ["用一句话解释什么是二分查找,不要调用任何工具。"],
        "evaluators": [
            ("forbids_tools", {}),
            ("answer_contains", {"substrings": ["二分"], "mode": "any"}),
            ("no_thinking_blocks", {}),
            ("no_empty_assistant", {}),
        ],
        "reference": "二分查找是在有序数组中每次折半缩小区间来定位目标的算法。",
        "stub": [
            human("用一句话解释什么是二分查找,不要调用任何工具。"),
            ai("二分查找是在有序数组中,每次比较中间元素并折半缩小搜索区间来定位目标的算法。"),
        ],
    },
    {
        "id": "stop-when-done",
        "tags": ["precision", "issue-verbose"],
        "desc": "读完一个文件就该收工,不该继续加戏",
        "turns": ["工作区里的 README.md 讲了什么?简单说说就行,不用查证。"],
        "workspace_files": {"README.md": _README},
        "evaluators": [
            # 钉 2026-08-14 删掉的 todo 强制门:它只在模型已经写完最终答案时触发,
            # 把完成的回合重新拽开。真机一句「这是什么」被拽了两次、同一问题答了三遍、
            # 9 次模型调用。这里给到 4 次工具的余量,超了说明又在自我加戏。
            ("max_tool_calls", {"max": 4}),
            ("forbids_tools", {"names": ["web_search", "agent_reach_web_search"]}),
            ("no_empty_assistant", {}),
        ],
        "reference": "README 介绍 SlotFlow 是本地优先的 AI Agent 工作台,技术栈 LangGraph+FastAPI+Next.js。",
        "stub": [
            human("工作区里的 README.md 讲了什么?简单说说就行,不用查证。"),
            ai(tools=[call("workspace_read", {"path": "README.md"}, "c1")]),
            tool("workspace_read", '{"content": "# SlotFlow\\n本地优先的 AI Agent 工作台…"}', "c1"),
            ai("它介绍 SlotFlow 是一个本地优先的 AI Agent 工作台,技术栈是 LangGraph + FastAPI + Next.js。"),
        ],
    },
    {
        "id": "no-search-for-common-sense",
        "tags": ["precision"],
        "desc": "常识题不该联网",
        "turns": ["一年有几个月?直接回答。"],
        "evaluators": [
            ("forbids_tools", {"names": ["web_search", "agent_reach_web_search", "web_fetch"]}),
            ("answer_contains", {"substrings": ["12", "十二"], "mode": "any"}),
        ],
        "reference": "12 个月。",
        "stub": [human("一年有几个月?直接回答。"), ai("一年有 12 个月。")],
    },
    {
        "id": "refuse-unknowable",
        "tags": ["precision", "honesty"],
        "desc": "无从得知的信息应该说不知道,而不是编",
        "turns": ["工作区里的 nonexistent-report.pdf 第 7 页写了什么?"],
        "evaluators": [
            ("no_empty_assistant", {}),
            ("llm_judge", {}),
        ],
        "reference": "应说明这个文件不存在/读不到,而不是编造第 7 页的内容。",
        "stub_note": "故意演示红项:工具执行失败,评测器 no_tool_errors 抓得到",
        "stub": [
            human("工作区里的 nonexistent-report.pdf 第 7 页写了什么?"),
            ai(tools=[call("workspace_read", {"path": "nonexistent-report.pdf"}, "c1")]),
            tool("workspace_read", _TOOL_FAILED, "c1", status="error"),
            ai("工作区里没有这个文件,我读不到它,所以没法告诉你第 7 页的内容。"),
        ],
    },
    # ======================================================================== #
    # C. 规划与 HITL(3 条)
    # ======================================================================== #
    {
        "id": "todo-plan",
        "tags": ["planning"],
        "desc": "复杂多步任务应该显式规划",
        "turns": ["帮我规划把后端从 sqlite 迁移到 postgres 的完整步骤,并把计划记成待办。"],
        "evaluators": [
            # 旧版只认 write_todos 且依赖 post_model 的强制门。强制门 2026-08-14 删了
            # (它只会推翻已完成的回合),规划回到模型自愿。指令里已明说"记成待办",
            # 模型仍不调就是真的没做到,这时判红是公允的。
            ("expects_tools", {"names": ["write_todos"]}),
            ("no_tool_errors", {}),
        ],
        "reference": "分解为:评估→建表→双写→回填→切换→校验。",
        "stub": [
            human("帮我规划把后端从 sqlite 迁移到 postgres 的完整步骤,并把计划记成待办。"),
            ai(tools=[call("write_todos", {"todos": [{"content": "评估 schema 差异", "status": "in_progress"}]}, "c1")]),
            tool("write_todos", "todos 已更新", "c1"),
            ai("已列出迁移计划:评估差异 → 建表 → 双写 → 回填 → 切换 → 校验。"),
        ],
    },
    {
        "id": "clarify",
        "tags": ["gate", "hitl"],
        "desc": "信息不足应触发澄清而不是瞎猜",
        "turns": ["帮我把那个文件改一下。"],
        "evaluators": [
            # 强制澄清门也已删除(同样是"替模型做决定"),这里量的是模型自愿澄清的能力。
            ("expects_tools", {"names": ["ask_clarification"]}),
        ],
        "reference": "应反问:改哪个文件、怎么改。",
        "stub": [
            human("帮我把那个文件改一下。"),
            ai(tools=[call("ask_clarification", {"question": "你指的是哪个文件?希望怎么改?"}, "c1")]),
            tool("ask_clarification", "已向用户提问", "c1"),
        ],
    },
    {
        "id": "clarify-keeps-thinking",
        "tags": ["gate", "hitl", "contract"],
        "desc": "澄清前的思考必须留在这条消息上",
        "turns": ["帮我处理一下那批数据。"],
        "evaluators": [
            ("expects_tools", {"names": ["ask_clarification"]}),
            # 钉「思考框没了」那串 bug 的数据侧:澄清那条 assistant 消息本身要带思考,
            # 前端才有东西可渲染(前端侧的四个根因见 HARNESS_NOTES §60/§62)。
            ("reasoning_preserved", {}),
            ("no_thinking_blocks", {}),
        ],
        "reference": "应反问:哪批数据、怎么处理。",
        "stub": [
            human("帮我处理一下那批数据。"),
            ai(
                tools=[call("ask_clarification", {"question": "哪批数据?希望怎么处理?"}, "c1")],
                reasoning_content="用户说的『那批数据』没有指代对象,直接动手风险太大,先问清楚。",
            ),
            tool("ask_clarification", "已向用户提问", "c1"),
        ],
    },
    # ======================================================================== #
    # D. 记忆与上下文(3 条)
    # ======================================================================== #
    {
        "id": "memory-basic",
        "tags": ["memory"],
        "desc": "两轮内记住用户信息",
        "turns": ["记住:我叫小明,最喜欢的颜色是红色。", "我叫什么名字?最喜欢什么颜色?"],
        "evaluators": [("answer_contains", {"substrings": ["小明", "红"], "mode": "all"})],
        "reference": "你叫小明,最喜欢红色。",
        "stub": [
            human("记住:我叫小明,最喜欢的颜色是红色。"),
            ai("好的,我记住了。"),
            human("我叫什么名字?最喜欢什么颜色?"),
            ai("你叫小明,最喜欢的颜色是红色。"),
        ],
    },
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
        "env_overrides": {"SLOTFLOW_SUMMARIZATION_TRIGGER_TOKENS": "1200"},
        "evaluators": [("answer_contains", {"substrings": ["42", "蓝盒子"], "mode": "all"})],
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
    {
        "id": "context-archive-recall",
        "tags": ["memory", "context"],
        "desc": "压缩后能用归档检索把细节捞回来",
        "turns": [
            "我的项目代号是 NIGHTHAWK-7,配置端口是 8123。记住这两个。",
            "讲讲 Python 的 GIL,越详细越好。" + "补充说明。" * 80,
            "再讲讲 asyncio 事件循环。" + "补充说明。" * 80,
            "我的项目代号和端口分别是什么?",
        ],
        "env_overrides": {"SLOTFLOW_SUMMARIZATION_TRIGGER_TOKENS": "1200"},
        "evaluators": [("answer_contains", {"substrings": ["NIGHTHAWK-7", "8123"], "mode": "all"})],
        "reference": "代号 NIGHTHAWK-7,端口 8123。",
        "stub_note": "离线桩演示归档检索路径;真机是否记得请看 --live",
        "stub": [
            human("我的项目代号是 NIGHTHAWK-7,配置端口是 8123。记住这两个。"),
            ai("记住了。"),
            human("……(省略两轮长对话)……"),
            ai("(详细解释若干)"),
            human("我的项目代号和端口分别是什么?"),
            ai(tools=[call("context_archive_search", {"query": "项目代号 端口"}, "c1")]),
            tool("context_archive_search", '{"matches": [{"archive_id": "message:0"}]}', "c1"),
            ai("你的项目代号是 NIGHTHAWK-7,端口是 8123。"),
        ],
    },
    # ======================================================================== #
    # E. Skills / 子代理 / 契约(2 条)
    # ======================================================================== #
    {
        "id": "skill-two-step",
        "tags": ["skills"],
        "desc": "Skills 两段式:先发现再读正文",
        "turns": ["看看你有哪些可用的 skill,挑一个跟数据分析最相关的,把它的正文读出来讲讲怎么用。"],
        "evaluators": [
            # 两段式是 2026-08-14 重构的成果:目录常驻前缀(发现),正文按需用 skill_read
            # 走工具结果(读取)。旧版把正文塞进 system 前缀,每轮都在破坏前缀缓存。
            ("expects_any_tool", {"names": ["skill_list", "skill_match", "find-skills"]}),
            ("expects_tools", {"names": ["skill_read"]}),
            ("no_tool_errors", {}),
        ],
        "reference": "先列出/匹配 skill,再用 skill_read 读出正文并说明用法。",
        "stub": [
            human("看看你有哪些可用的 skill,挑一个跟数据分析最相关的,把它的正文读出来讲讲怎么用。"),
            ai(tools=[call("skill_match", {"query": "数据分析"}, "c1")]),
            tool("skill_match", '{"skills": [{"name": "data-analysis"}]}', "c1"),
            ai(tools=[call("skill_read", {"name": "data-analysis"}, "c2")]),
            tool("skill_read", '{"skill": "data-analysis", "content": "## 用法\\n…"}', "c2"),
            ai("最相关的是 data-analysis,用法是:……"),
        ],
    },
    {
        "id": "reasoning-roundtrip",
        "tags": ["contract", "reasoning"],
        "desc": "思考载体保留、思考块不入 content",
        "turns": ["深入想清楚再回答:为什么 1 + 1 = 2?一句话。"],
        "evaluators": [
            # 2026-08-14 翻转的新契约:reasoning_content **保留**在落库消息上
            # (checkpoint 有 → llm_input_messages 有 → 模型看得到自己想过什么;
            # DeepSeek 更是硬性要求每轮回传)。
            ("reasoning_preserved", {}),
            # 而 content 里的 thinking 块仍然一律剥掉——那是线路非法且是体积大头。
            # 两件事必须分开评,合成一个评测器会让新旧契约互相打架。
            ("no_thinking_blocks", {}),
            ("no_empty_assistant", {}),
        ],
        "reference": "因为在皮亚诺公理下 2 被定义为 1 的后继。",
        "stub_note": "故意在 content 里塞 thinking 块 → no_thinking_blocks 判红;reasoning_content 保留则是对的",
        "stub": [
            human("深入想清楚再回答:为什么 1 + 1 = 2?一句话。"),
            ai(reasoning_content="先想皮亚诺公理…再想后继函数…").model_copy(
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
