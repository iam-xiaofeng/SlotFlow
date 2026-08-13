---
type: 架构组件
title: 模型供应商与运行时发现
description: SlotFlow 基于 LiteLLM 的多供应商模型集成、运行时动态模型发现机制、供应商配置和模型选择路由。
tags: [architecture, models, litellm, providers]
openwiki:
  roles: [architecture, integration]
  source_paths:
    - backend/app/chat/litellm_provider.py
    - backend/app/chat/runtime/models.py
    - backend/app/chat/model_catalog.py
  symbols:
    - configured_native_provider_names
    - agent_models_for_provider
    - create_chat_model
    - ChatLiteLLM
  test_paths: [backend/tests/]
  validation_commands:
    - cd backend && uv run pytest -q
---

# 模型供应商与运行时发现

SlotFlow 通过 LiteLLM 集成多个 AI 模型供应商，支持运行时动态模型发现，而非维护硬编码的模型列表。

## 架构分层

```
┌────────────────────────────────────────────┐
│            RunContext.model_provider        │
│         业务层：用户选择供应商              │
└──────────────────┬─────────────────────────┘
                   │
┌──────────────────┴─────────────────────────┐
│         runtime/models.create_chat_model    │
│         适配层：创建模型实例                │
└──────────────────┬─────────────────────────┘
                   │
┌──────────────────┴─────────────────────────┐
│         chat/litellm_provider.py            │
│         供应商层：LiteLLM 集成              │
│   configured_native_provider_names()       │
│   agent_models_for_provider()             │
└──────────────────┬─────────────────────────┘
                   │
┌──────────────────┴─────────────────────────┐
│              LiteLLM SDK                    │
│         底层：多供应商统一 API              │
│   get_valid_models()                       │
│   models_by_provider                       │
└────────────────────────────────────────────┘
```

## 支持的供应商

通过环境变量配置：

| 供应商 | 环境变量 | 说明 |
|--------|----------|------|
| DeepSeek | `DEEPSEEK_API_KEY` | DeepSeek 推理模型 |
| OpenAI | `OPENAI_API_KEY` | GPT-4o、GPT-4 等 |
| Anthropic | `ANTHROPIC_API_KEY` | Claude 系列 |
| 自定义 Relay | `CUSTOM_OPENAI_API_KEY` + `CUSTOM_OPENAI_BASE_URL` | 任何 OpenAI-compatible API |

## 运行时模型发现

### 供应商发现

`chat/litellm_provider.py::configured_native_provider_names()` 是唯一的供应商发现入口：

1. 调用 LiteLLM 的公共 API `get_valid_models(check_provider_endpoint=False)` 获取所有可用模型
2. 根据环境变量中存在 API Key 的供应商进行过滤
3. 返回已配置的原生供应商名称列表

### 模型筛选

`agent_models_for_provider()` 对每个供应商的模型进行筛选：

1. 从 LiteLLM 的内置 `models_by_provider` 元数据中获取模型列表
2. 过滤条件：`mode=chat` 且 `supports_function_calling=True`
3. 返回可用于 Agent 场景的模型列表

### 模型命名

可选模型使用供应商限定的命名空间 ID：`provider/model`。这种格式使得运行时路由无需额外的 SlotFlow 供应商映射。

## 模型创建

`runtime/models.create_chat_model` 根据 `RunContext.model_provider` 创建 `ChatLiteLLM` 实例：

- 读取对应的环境变量获取 API Key 和 Base URL
- 配置模型参数（temperature、max_tokens 等）
- 返回可用于 `model.bind_tools(tools)` 的模型实例

## 思考模式（Thinking/Reasoning）

部分模型支持 thinking/reasoning 模式，将推理过程与最终答案分离：

- **DeepSeek**：原生支持 reasoning tokens
- **Anthropic Claude**：支持 extended thinking
- **OpenAI**：部分模型支持 reasoning

投影层将推理输出通过 `message.delta` 事件的 `reasoning` 通道发送，前端可独立渲染推理过程。

详见 [聊天 API](chat-api.md#agentevent)。

## 模型目录 API

`chat/model_catalog.py` 提供前端模型选择器所需的数据：

- **可用供应商列表**：从 `configured_native_provider_names()` 获取
- **每个供应商的模型列表**：从 `agent_models_for_provider()` 获取
- **模型能力元数据**：上下文窗口大小、是否支持 function calling 等

## 自定义 OpenAI-compatible Relay

对于自托管或代理模型，使用 `CUSTOM_OPENAI_API_KEY` 和 `CUSTOM_OPENAI_BASE_URL`：

```bash
CUSTOM_OPENAI_API_KEY=sk-your-key
CUSTOM_OPENAI_BASE_URL=https://your-relay.example.com/v1
```

模型发现遵循相同的 LiteLLM 路径，只要 relay 兼容 OpenAI API 格式即可。

## 供应商/模型边界

`chat/litellm_provider.py` 是唯一的供应商/模型目录边界。所有模型调用经过此处归一化：

1. **供应商差异归一化**：不同供应商的 API 差异在此处处理
2. **版本兼容**：LiteLLM 处理不同模型版本的 API 兼容性
3. **错误映射**：将各供应商的错误码映射为统一的 SlotFlow 错误

## 配置示例

`backend/.env_example` 包含完整的配置模板：

```bash
# DeepSeek
DEEPSEEK_API_KEY=sk-your-deepseek-key

# OpenAI
OPENAI_API_KEY=sk-your-openai-key

# Anthropic
ANTHROPIC_API_KEY=sk-ant-your-anthropic-key

# Custom OpenAI-compatible relay
CUSTOM_OPENAI_API_KEY=sk-your-custom-key
CUSTOM_OPENAI_BASE_URL=https://your-relay.example.com/v1
```

## 不变性条件

- **单一供应商边界**：所有模型调用必须经过 `litellm_provider.py`
- **运行时发现**：模型列表从 LiteLLM 实时获取，不硬编码
- **环境变量驱动**：供应商配置通过环境变量，不存储在数据库或代码中
- **命名空间隔离**：模型 ID 使用 `provider/model` 格式，避免名称冲突

## 变更导航

| 变更意图 | 入口文件 | 关键符号 | 聚焦测试 |
|----------|----------|----------|----------|
| 添加新供应商 | `backend/app/chat/litellm_provider.py` | `configured_native_provider_names` | `backend/tests/` |
| 修改模型筛选 | `backend/app/chat/litellm_provider.py` | `agent_models_for_provider` | `backend/tests/` |
| 调整模型参数 | `backend/app/chat/runtime/models.py` | `create_chat_model` | `backend/tests/` |
| 前端模型选择器 | `frontend/src/hooks/use-model-catalog.ts` | React hook | `frontend/src/` |

**最小验证命令：** `cd backend && uv run pytest -q`