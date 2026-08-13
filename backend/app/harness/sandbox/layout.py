"""工作区目录布局的单一事实源。

布局(2026-08-09 起,取代原先 ``artifacts/`` + ``uploads/`` + ``.sandbox/`` 三个横切目录)::

    <workspace_root>/
    ├── <thread>/
    │   ├── work/                  沙箱 scratch,容器 cwd 就在这一层的父目录
    │   ├── artifacts/             用户可见产物
    │   └── uploads/<run_id>/      本次 run 的上传副本
    ├── .uploads/<file_id>/        上传原件 + metadata.json(容器内只读)
    ├── .slotflow_offload/         超长工具结果卸载
    └── .playwright-mcp/           浏览器 MCP 状态

这样一个对话的全部文件收在一个目录里:模型在容器里 ``ls`` 就能同时看到
``work / artifacts / uploads``,删对话也只需要删一个目录。以点开头的目录是
SlotFlow 自己的存储,``ls`` 默认不显示,也不该出现在前端的产物视图里。

**为什么集中在这里**:容器路径、宿主相对路径、路由可见性校验三方必须对齐,
以前分散在 ``docker.py`` / ``tools/workspace.py`` / ``workspace/routes.py``
各写一份,``artifacts/`` 用原始 thread_id 而 ``.sandbox/`` 用规范化后的 key,
已经出现过口径不一致。
"""

from __future__ import annotations

import re
from typing import Literal

# 容器内的挂载点。skills 刻意挂在 /workspace 之外:嵌套挂载会在宿主的
# workspace 根上留下一个空的挂载点目录,破坏"根目录下只有 thread 目录"。
CONTAINER_WORKSPACE_ROOT = "/workspace"
CONTAINER_SKILLS_ROOT = "/skills"

WORK_DIR_NAME = "work"
ARTIFACTS_DIR_NAME = "artifacts"
UPLOADS_DIR_NAME = "uploads"
THREAD_SUBDIR_NAMES = (WORK_DIR_NAME, ARTIFACTS_DIR_NAME, UPLOADS_DIR_NAME)

# 上传原件:不进 thread 目录(上传发生在 POST /api/uploads,那时还没有 thread),
# 以点开头让它对模型的 `ls` 隐形,并在容器里叠一层只读挂载保护。
UPLOAD_ORIGINALS_DIR = ".uploads"

# 旧布局的顶层目录,只用于向后兼容地读取存量文件。
LEGACY_ARTIFACTS_DIR = "artifacts"
LEGACY_UPLOADS_DIR = "uploads"
LEGACY_WORK_DIR = ".sandbox"

# 挂载结构一变,复用旧容器就会带着旧挂载继续跑。容器名里混入布局版本,
# 换布局自然换一个新容器,老容器留在原地不影响(它只是不再被使用)。
LAYOUT_VERSION = "v2"

ViewableKind = Literal["artifacts", "uploads"]


def thread_dir_name(thread_id: str | None) -> str:
    """把 thread_id 规范化成一个安全的目录名。"""

    cleaned = re.sub(r"[^a-zA-Z0-9_.-]+", "-", (thread_id or "").strip())
    return cleaned.strip(".-") or "default"


def thread_dir(thread_id: str | None) -> str:
    """本对话在工作区里的根目录(相对 workspace root)。"""

    return thread_dir_name(thread_id)


def thread_work_dir(thread_id: str | None) -> str:
    """沙箱 scratch 目录。"""

    return f"{thread_dir(thread_id)}/{WORK_DIR_NAME}"


def thread_artifacts_dir(thread_id: str | None) -> str:
    """用户可见产物目录。"""

    return f"{thread_dir(thread_id)}/{ARTIFACTS_DIR_NAME}"


def thread_uploads_dir(thread_id: str | None) -> str:
    """本对话的上传副本目录。"""

    return f"{thread_dir(thread_id)}/{UPLOADS_DIR_NAME}"


def run_uploads_dir(thread_id: str | None, run_id: str) -> str:
    """某一次 run 的上传副本目录。"""

    return f"{thread_uploads_dir(thread_id)}/{run_id}"


def container_thread_dir(thread_id: str | None) -> str:
    """容器内的对话根目录,也是 ``docker exec`` 的默认工作目录。"""

    return f"{CONTAINER_WORKSPACE_ROOT}/{thread_dir(thread_id)}"


def container_work_dir(thread_id: str | None) -> str:
    return f"{CONTAINER_WORKSPACE_ROOT}/{thread_work_dir(thread_id)}"


def container_artifacts_dir(thread_id: str | None) -> str:
    return f"{CONTAINER_WORKSPACE_ROOT}/{thread_artifacts_dir(thread_id)}"


def container_uploads_dir(thread_id: str | None) -> str:
    return f"{CONTAINER_WORKSPACE_ROOT}/{thread_uploads_dir(thread_id)}"


def viewable_kind(path: str) -> ViewableKind | None:
    """判断一个工作区相对路径能不能给前端看,以及它属于哪一类。

    只有产物和上传可读:``<thread>/artifacts…`` 与 ``<thread>/uploads…``,外加旧布局的
    ``artifacts…`` / ``uploads…``。其余区域(``.uploads`` 原件、``.slotflow_offload``、
    ``.playwright-mcp``、挂进来的 skills)一律拒绝。

    这里只做**归类**,真正的越界防护仍然由 ``SlotFlowWorkspace.resolve_path`` 负责;
    两道都要过。
    """

    parts = [part for part in path.strip().strip("/").split("/") if part not in ("", ".")]
    if not parts or any(part == ".." for part in parts):
        return None
    # 以点开头的都是 SlotFlow 自己的存储,不对外。
    if any(part.startswith(".") for part in parts):
        return None

    if len(parts) >= 2:
        if parts[1] == ARTIFACTS_DIR_NAME:
            return "artifacts"
        if parts[1] == UPLOADS_DIR_NAME:
            return "uploads"

    if parts[0] == LEGACY_ARTIFACTS_DIR:
        return "artifacts"
    if parts[0] == LEGACY_UPLOADS_DIR:
        return "uploads"
    return None


def is_artifact_path(path: str) -> bool:
    """写/删只允许落在产物区(新布局 ``<thread>/artifacts`` 或旧布局 ``artifacts/``)。"""

    return viewable_kind(path) == "artifacts"
