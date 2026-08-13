"""工作区布局(一个对话一个目录)的回归测试。

这些断言钉住的是**跨模块的口径一致性**:容器路径、宿主相对路径、路由可见性三方
必须同源,以前分散各处时出现过 `artifacts/` 用原始 thread_id 而 `.sandbox/` 用
规范化 key 的不一致。
"""

from __future__ import annotations

from app.harness.sandbox.layout import (
    LAYOUT_VERSION,
    UPLOAD_ORIGINALS_DIR,
    container_artifacts_dir,
    container_thread_dir,
    is_artifact_path,
    run_uploads_dir,
    thread_artifacts_dir,
    thread_dir_name,
    thread_uploads_dir,
    thread_work_dir,
    viewable_kind,
)
from app.harness.tools.workspace import artifact_dir_for_thread, normalize_artifact_path


def test_thread_dir_name_is_filesystem_safe() -> None:
    assert thread_dir_name("thread_abc123") == "thread_abc123"
    assert thread_dir_name("eval-read-file") == "eval-read-file"
    assert thread_dir_name(None) == "default"
    assert thread_dir_name("  ") == "default"
    # 路径分隔符与相对片段不能穿出目录,也不能造出隐藏目录
    assert "/" not in thread_dir_name("a/../b")
    assert thread_dir_name("..") == "default"
    assert not thread_dir_name(".hidden").startswith(".")


def test_thread_subdirs_share_one_root() -> None:
    """三个子目录必须挂在同一个对话目录下——模型 `ls` 一次要能全看到。"""

    root = thread_dir_name("thread_x")
    assert thread_work_dir("thread_x") == f"{root}/work"
    assert thread_artifacts_dir("thread_x") == f"{root}/artifacts"
    assert thread_uploads_dir("thread_x") == f"{root}/uploads"
    assert run_uploads_dir("thread_x", "run_1") == f"{root}/uploads/run_1"


def test_container_paths_mirror_host_paths() -> None:
    """容器路径 = /workspace + 宿主相对路径,不允许两套算法。"""

    assert container_thread_dir("thread_x") == "/workspace/thread_x"
    assert container_artifacts_dir("thread_x") == f"/workspace/{thread_artifacts_dir('thread_x')}"


def test_viewable_kind_allows_artifacts_and_uploads_only() -> None:
    assert viewable_kind("thread_x/artifacts/report.md") == "artifacts"
    assert viewable_kind("thread_x/uploads/run_1/data.csv") == "uploads"
    # 旧布局的存量文件仍然可读,否则迁移前后前端会"少东西"
    assert viewable_kind("artifacts/thread_x/report.md") == "artifacts"
    assert viewable_kind("uploads/run_1/data.csv") == "uploads"


def test_viewable_kind_rejects_private_areas() -> None:
    """scratch、上传原件、卸载文件、skills 都不能经产物接口读出去。"""

    assert viewable_kind("thread_x/work/scratch.py") is None
    assert viewable_kind(f"{UPLOAD_ORIGINALS_DIR}/file_x/orig.txt") is None
    assert viewable_kind(".slotflow_offload/web_fetch-call_1.txt") is None
    assert viewable_kind(".playwright-mcp/state.json") is None
    assert viewable_kind("skills/secret/SKILL.md") is None
    assert viewable_kind("thread_x/artifacts/../../etc/passwd") is None
    assert viewable_kind("") is None


def test_is_artifact_path_excludes_uploads() -> None:
    """写/删只能落在产物区:上传目录不能被 artifact 接口删。"""

    assert is_artifact_path("thread_x/artifacts/a.md") is True
    assert is_artifact_path("artifacts/legacy.md") is True
    assert is_artifact_path("thread_x/uploads/run_1/a.csv") is False
    assert is_artifact_path("thread_x/work/a.py") is False


def test_normalize_artifact_path_tolerates_model_prefixes() -> None:
    """模型常把自己所在目录一起写进来,前缀要剥干净但不能剥掉真实文件名。"""

    base = artifact_dir_for_thread("thread_x")
    for given in (
        "report.md",
        "artifacts/report.md",
        "thread_x/artifacts/report.md",
        "/artifacts/report.md",
    ):
        assert normalize_artifact_path(given, "thread_x") == f"{base}/report.md"

    assert normalize_artifact_path("charts/sales.html", "thread_x") == f"{base}/charts/sales.html"
    # 名字里恰好含 artifacts 的普通文件不该被误伤
    assert normalize_artifact_path("artifacts-summary.md", "thread_x") == (
        f"{base}/artifacts-summary.md"
    )
    assert normalize_artifact_path("", "thread_x") == f"{base}/artifact.md"


def test_layout_version_is_part_of_container_identity() -> None:
    """挂载结构固定在 docker run,布局版本必须能换出新容器名。"""

    from app.harness.sandbox.config import SlotFlowSandboxConfig
    from app.harness.sandbox.docker import LazyDockerSandbox

    config = SlotFlowSandboxConfig()
    name = LazyDockerSandbox(config=config, thread_id="t").container_name
    assert name.startswith("slotflow-sandbox-")

    import app.harness.sandbox.docker as docker_module

    original = docker_module.LAYOUT_VERSION
    try:
        docker_module.LAYOUT_VERSION = f"{LAYOUT_VERSION}-next"
        changed = LazyDockerSandbox(config=config, thread_id="t").container_name
    finally:
        docker_module.LAYOUT_VERSION = original

    assert changed != name
