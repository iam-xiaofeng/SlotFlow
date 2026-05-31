# Rewrite Boundary

SlotFlow keeps the DeerFlow harness capabilities, but removes the old product
and LangGraph Platform compatibility layers from the first learning milestone.

## Learning Goal

SlotFlow is not only a smaller implementation. It is also a guided full-stack
learning project.

Every module should make the complete path easier to understand:

```txt
frontend input
-> backend API
-> run config
-> harness / agent
-> SSE events
-> frontend stream state
-> UI rendering
```

For each module, explain four things before moving on:

```txt
what problem it solves
what input it receives
what output it produces
where it sits in the frontend -> backend -> agent -> frontend loop
```

Development should advance in small verified modules:

```txt
1. backend health/API skeleton
2. run config builder
3. fake agent stream -> business SSE
4. thread/message persistence
5. real harness agent builder + checkpointer
6. frontend SSE parser
7. frontend use-chat-stream hook
8. chat UI for messages/tools/state
9. real agent streaming
```

`make verify` is the project health gate, not the chat feature itself. It
currently verifies backend tests, frontend type checking, and frontend
production build. As real modules are added, this command should cover more of
the actual chat stream.

When something fails, first look for a simpler boundary or a smaller test. Do
not add compatibility layers, global state, or old protocol adapters just to
hide the failure.

## Keep

```txt
harness agents
harness tools
harness middlewares
model config and model creation
thread state
checkpointer support
memory files
agent factory / feature flags
```

`factory.py` and `features.py` from the DeerFlow harness are runtime assembly
code, not test code. They are useful because the new backend must pass a
checkpointer explicitly when it builds the agent graph.

## Rewrite

```txt
gateway API
run/stream orchestration
SSE event contract
frontend state hook
frontend chat UI
```

The first backend stream should be direct:

```txt
HTTP endpoint
-> agent.astream(stream_mode=["values", "messages", "custom"])
-> business SSE events
```

Do not recreate this chain in the first milestone:

```txt
RunManager -> StreamBridge -> sse_consumer -> LangGraph SDK useStream
```

## First Tests

The first useful tests should protect the core invariants:

```txt
thread_id is placed in config["configurable"]
checkpointer is passed when the graph is created
messages chunks become message.delta SSE events
values chunks become state.snapshot SSE events
stream errors end as run.error
thread messages are persisted
```

The old `backend/tests` directory should be used as reference material, not
copied wholesale.
