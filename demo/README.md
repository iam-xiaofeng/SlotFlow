# SlotFlow 对话实录（静态展示页）

**只读**的对话展示页,内容全部来自 SlotFlow 的**真实运行**,不是编的。
纯静态:一个 `index.html` + 一个 `transcripts.js`,**不需要后端**,丢到任何静态托管
(Cloudflare Pages / tunnel / nginx / GitHub Pages)都能跑。

```bash
# 本地看一眼
cd demo && python3 -m http.server 8080     # → http://localhost:8080

# Cloudflare 隧道对外
cloudflared tunnel --url http://localhost:8080
```

## 展示了什么

10 个 thread,分五类。前 8 个直接复用 `backend/evals/` 的评测样本(**跑过真机评测、有分数可查**),
后 2 个是专门为展示写的复杂长任务。

| thread | 看点 |
|---|---|
| 超长工具结果:中段被省略后再取回 | 沙箱打印 4 万字符 → 超阈值被卸载成文件句柄,上下文里只留 head+tail 预览。暗号在正中间(第 1001 行)必然落在被省略那段,模型只能回头用工具取 |
| 压缩之后,早期细节还能捞回来 | 压缩阈值压到 1200 token 逼出真实压缩,中间灌两轮长对话把开头挤出窗口,最后回头问第一轮的项目代号和端口 |
| 大文件不整段读 | 分页 / 检索,而不是把整个文件灌进上下文 |
| 写完代码在沙箱里真跑一遍 | Docker 沙箱执行用例、拿真实 stdout 再下结论 |
| Skills 两段式 | 目录常驻系统前缀(发现),正文按需走 `skill_read` 工具结果(读取) |
| 信息不足时先问,而且思考不丢 | HITL 澄清卡片 + 那条消息本身带着它的思考 |
| 读不到就说读不到 | 不存在的文件,承认拿不到而不是编 |
| 不该调工具时别乱调 | 精度的另一面 |
| **复杂任务:做一个精美落地页** | 规划 → 写代码 → 产出 artifact,页面成品**可直接在页面里预览** |
| **复杂任务:深度调研 Agent 框架差异** | 19 次工具调用、多轮联网检索交叉比对、5.8 万字思考 |

页面里能看到的不只是消息:**思考过程**(可折叠)、**工具调用时间线**、**工具返回原文**、
**todo 任务面板**、**澄清卡片**、**artifact 预览**(HTML 成品直接 iframe 渲染)。

## 怎么重新生成

```bash
cd backend
uv run python -m evals.build_demo --list                    # 看有哪些场景
uv run python -m evals.build_demo                           # 全部重跑
uv run python -m evals.build_demo --only frontend-page      # 只重跑一条,其余沿用
uv run python -m evals.build_demo --reprocess               # 不跑模型,只重抽结构化字段
```

`--reprocess` 那个模式值得说一句:todo 面板 / 澄清卡片 / artifact 预览这些结构化字段,
是从已导出结果里的原始 `tool_calls` **重新抽**出来的,不需要再花一次模型调用。
所以改渲染逻辑时不用重跑真机。

## 两条原则

**只导出真实运行结果,不编造。** 导出前会清洗一次:丢掉空 assistant 消息、剔除
429/限流/连接错误这类噪音——展示页是给人看「这个 agent 能做什么」的,基础设施抖动不属于
要展示的内容。**清洗只删噪音,绝不改写模型说过的话。**

(为什么会有这类噪音:走中转的 provider 被限流时不回 429,而是直接给一个空补全。
详见 `backend/evals/README.md` 的「真机结果」一节。)

**输入框是停用的。** 这是静态实录页,没有后端、不能对话。完整可交互版本请在本地跑 SlotFlow。
