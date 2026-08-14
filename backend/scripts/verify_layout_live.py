"""真机端到端验证:用真实模型跑一遍新工作区布局涉及的全部功能。

覆盖:上传落点 → stage 到对话目录 → sandbox_exec 的 cwd/ls → artifact_write →
sandbox_artifact_copy → 产物发现 new_entries → 前端三个只读接口 → 安全边界。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx

BASE = "http://localhost:8000"
MODEL = "deepseek-v4-pro"
WORKSPACE = Path(__file__).resolve().parents[1] / ".slotflow" / "workspace"

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"{'✅' if ok else '❌'} {name}" + (f"  — {detail}" if detail else ""), flush=True)


def stream(client: httpx.Client, thread_id: str, message: str, files: list[str]) -> dict:
    """跑一次 run,返回收集到的事件。"""

    events: dict[str, list] = {}
    with client.stream(
        "POST",
        f"{BASE}/api/chat/threads/{thread_id}/runs/stream",
        json={
            "message": message,
            "model_name": MODEL,
            "provider": "custom",
            "mode": "pro",
            "files": files,
        },
        timeout=300.0,
    ) as response:
        response.raise_for_status()
        name = None
        for line in response.iter_lines():
            if line.startswith("event: "):
                name = line[7:]
            elif line.startswith("data: ") and name:
                events.setdefault(name, []).append(json.loads(line[6:]))
    return events


def main() -> int:
    client = httpx.Client(timeout=60.0)

    thread = client.post(f"{BASE}/api/chat/threads", json={"title": "布局真机验证"}).json()
    thread_id = thread["id"]
    print(f"\n对话 {thread_id}\n{'=' * 70}", flush=True)

    # --- 1. 上传:原件必须落在 .uploads/ ---
    upload = client.post(
        f"{BASE}/api/uploads",
        files={"file": ("sales.csv", b"month,amount\n1,100\n2,150\n3,225\n", "text/csv")},
    ).json()
    check(
        "上传原件落在 .uploads/",
        upload["workspace_path"].startswith(".uploads/"),
        upload["workspace_path"],
    )
    check(
        "原件在磁盘上存在",
        (WORKSPACE / upload["workspace_path"]).is_file(),
    )

    # --- 2. 真实模型跑一轮:要求它用沙箱看目录、读上传、出产物 ---
    events = stream(
        client,
        thread_id,
        "请依次完成:(1) 用 sandbox_exec 执行 `pwd && ls -1` 并把输出原样告诉我;"
        "(2) 用 workspace_read 读我上传的 sales.csv;"
        "(3) 用 artifact_write 生成 report.md,内容写出三个月的总额。"
        "三步都要真的调用工具。",
        [upload["id"]],
    )

    tool_names = [item.get("tool_name") for item in events.get("tool.status", [])]
    print(f"\n调用的工具: {tool_names}\n", flush=True)
    check("run 正常结束", "run.finished" in events, f"事件: {sorted(events)}")

    # --- 3. stage 落点:副本必须在 <thread>/uploads/<run_id>/ ---
    messages = client.get(f"{BASE}/api/chat/threads/{thread_id}/messages").json()
    staged = messages[0]["metadata"]["uploaded_files"][0]["workspace_path"]
    check(
        "上传副本落在 <thread>/uploads/<run>/",
        staged.startswith(f"{thread_id}/uploads/run_"),
        staged,
    )
    check("副本在磁盘上存在", (WORKSPACE / staged).is_file())
    check(
        "原件未被 stage 破坏",
        (WORKSPACE / upload["workspace_path"]).read_bytes().startswith(b"month,amount"),
    )

    # --- 4. sandbox_exec 的 cwd 与 ls ---
    exec_outputs = [
        item for item in events.get("tool.status", []) if item.get("tool_name") == "sandbox_exec"
    ]
    answer = "".join(
        item.get("delta", "")
        for item in events.get("message.delta", [])
        if item.get("channel") == "content"
    )
    check("模型调用了 sandbox_exec", bool(exec_outputs))
    check(
        f"沙箱 cwd 是对话目录 /workspace/{thread_id}",
        f"/workspace/{thread_id}" in answer,
        "见终答中的 pwd 输出" if f"/workspace/{thread_id}" in answer else answer[-260:],
    )
    saw_three = all(name in answer for name in ("artifacts", "uploads", "work"))
    check("ls 同时看到 work/artifacts/uploads", saw_three)

    # --- 5. 产物落点与发现 ---
    snapshot = events.get("state.snapshot", [{}])[-1]
    artifacts = (snapshot.get("state") or {}).get("slotflow", {}).get("artifacts", {})
    new_paths = [entry["path"] for entry in artifacts.get("new_entries", [])]
    check(
        "产物发现的 new_entries 只含本对话",
        bool(new_paths) and all(path.startswith(f"{thread_id}/artifacts/") for path in new_paths),
        str(new_paths),
    )
    check(
        "report.md 真的写在对话产物目录",
        (WORKSPACE / thread_id / "artifacts" / "report.md").is_file(),
    )

    # --- 6. 前端只读接口 ---
    listed = client.get(f"{BASE}/api/workspace/artifacts").json()
    listed_paths = {entry["path"] for entry in listed}
    check(
        "聚合产物列表包含本对话的 report.md",
        f"{thread_id}/artifacts/report.md" in listed_paths,
        f"共 {len(listed_paths)} 个文件",
    )
    threads_view = client.get(f"{BASE}/api/workspace/threads").json()
    mine = next((item for item in threads_view if item["thread_id"] == thread_id), None)
    check("threads 视图能找到本对话", mine is not None)
    if mine:
        check(
            "threads 视图同时给出产物与上传",
            bool(mine["generated"]) and bool(mine["uploads"]),
            f"generated={len(mine['generated'])} uploads={len(mine['uploads'])}",
        )
    raw = client.get(
        f"{BASE}/api/workspace/artifacts/raw",
        params={"path": f"{thread_id}/artifacts/report.md"},
    )
    check("raw 能取到产物内容", raw.status_code == 200 and len(raw.content) > 0)
    read = client.get(
        f"{BASE}/api/workspace/artifacts/read",
        params={"path": staged},
    )
    check("read 能预览上传副本", read.status_code == 200)

    # --- 7. 安全边界 ---
    for label, path in (
        ("scratch work/ 不可读", f"{thread_id}/work/anything.py"),
        ("上传原件不可经产物接口读", upload["workspace_path"]),
        ("卸载目录不可读", ".slotflow_offload/x.txt"),
        ("越界 .. 被拒", f"{thread_id}/artifacts/../../../etc/passwd"),
    ):
        status = client.get(f"{BASE}/api/workspace/artifacts/read", params={"path": path}).status_code
        check(label, status in (400, 404), f"HTTP {status}")

    print(f"\n{'=' * 70}")
    failed = [name for name, ok, _ in results if not ok]
    print(f"通过 {len(results) - len(failed)}/{len(results)}")
    if failed:
        print("失败项:")
        for name in failed:
            print(f"  - {name}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
