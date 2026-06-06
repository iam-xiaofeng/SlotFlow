# 模块 12：Harness 只读 skills registry

模块 12 给 SlotFlow harness 加入第一版 skills 能力。

这里的 skills 不是工具本身，也不是 sandbox 执行器。它更像“能力说明书 + 工具策略提示”：

```txt
SKILL.md
-> 解析 name / description / allowed-tools
-> enabled skills
-> system prompt 片段
-> 模型知道当前 run 可以参考哪些能力说明
```

第一版只读，不做安装、编辑、删除，也不做安全扫描。

## 这一层解决什么问题

它解决的是：“harness 怎么把本地能力说明注入 agent？”

模块 11 已经有 tool registry，但 tool 只是函数。skills 负责告诉模型：

```txt
有哪些能力包
每个能力包做什么
这个 skill 期望使用哪些工具
```

这两个边界不要混：

```txt
tools  = agent 能调用的函数
skills = agent 可阅读的能力说明和工具策略提示
```

## 它接收什么输入

第一版接收本地 skills root：

```txt
SLOTFLOW_SKILLS_ROOT=/path/to/skills
SLOTFLOW_ENABLED_SKILLS=alpha,beta
```

每个 skill 由一个 `SKILL.md` 描述：

```md
---
name: alpha
description: Alpha skill
allowed-tools:
  - slotflow_context
---

# Alpha
```

当前 parser 只支持 SlotFlow 需要的最小 frontmatter 子集：

```txt
name
description
allowed-tools
```

`allowed-tools` 的语义保留三种：

```txt
字段省略 -> inherit
[]       -> none
[a, b]   -> 只允许这些工具
```

模块 12 先把这个语义读出来并写入 prompt。真正按 allowed-tools 过滤工具，会在后续工具策略模块里做。

## 它输出什么数据

registry 输出：

```txt
list[Skill]
```

prompt builder 输出：

```txt
<slotflow-skills>
Enabled skills for this run:
- alpha: Alpha skill
  allowed_tools: slotflow_context
</slotflow-skills>
```

这段会被拼进 harness system prompt。

## 它在完整链路里的位置

模块 12 位于 harness builder 内部：

```txt
前端输入
-> 后端 API
-> run 配置
-> runtime 模式选择
-> harness builder
-> skills registry       <-- 当前模块
-> system prompt
-> LangGraph agent graph
-> AgentEvent / SSE / 前端
```

## 主要代码

```txt
backend/app/harness/skills/__init__.py
backend/app/harness/skills/types.py
backend/app/harness/skills/parser.py
backend/app/harness/skills/registry.py
backend/app/harness/config.py
backend/app/harness/builder.py
backend/app/chat/runtime.py
backend/tests/test_harness_skills.py
```

`SlotFlowHarnessConfig` 现在增加：

```txt
skills_root
enabled_skills
```

`chat/runtime.py` 负责从环境变量读取这两个字段，但不解析 skill 内容。真正扫描和 prompt 构建仍在
`app/harness/skills` 内部。

## 测试怎么读

测试文件：

```txt
backend/tests/test_harness_skills.py
```

它保护六件事：

```txt
1. parser 能读取 name / description / allowed-tools
2. allowed-tools 的 None / [] / list 语义不丢
3. registry 能扫描 skills root
4. enabled_names 能过滤启用技能
5. builder 会把 enabled skills 摘要拼进 system prompt
6. runtime 会把 env 中的 skills 配置传给 harness
```

## 这一模块不做什么

当前明确不做：

```txt
不安装 skill
不编辑 skill
不删除 skill
不执行 skill 里的脚本
不做 skill 安全扫描
不真正过滤 tools
不依赖 sandbox
```

这些能力后续可以加，但第一步先把“只读技能说明进入 agent prompt”的链路跑通。
