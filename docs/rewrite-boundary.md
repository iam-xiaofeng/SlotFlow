# Rewrite Boundary

SlotFlow keeps the DeerFlow harness capabilities, but removes the old product
and LangGraph Platform compatibility layers from the first learning milestone.

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
