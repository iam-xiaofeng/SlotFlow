"""把工作区从旧布局迁到「一个对话一个目录」的新布局。

旧 → 新::

    artifacts/<thread>/**   →  <thread>/artifacts/**
    .sandbox/<thread>/**    →  <thread>/work/**
    .sandbox/<散落文件>     →  default/work/<散落文件>
    uploads/<file_id>/**    →  .uploads/<file_id>/**   （并改写 metadata.json 里的 workspace_path）

**刻意不搬**的两类,搬了反而会弄坏现有数据:

- ``uploads/<run_id>/``:哪个 run 属于哪个对话只记在聊天库的消息元数据里,搬动就必须同步
  改写数据库里的 ``workspace_path``。后端保留了旧路径的读取分支,留在原地照样能预览。
- ``artifacts/`` 下的散落文件:本来就不属于任何对话,前端有「未归类产物」分组兜着。

默认只打印计划(dry-run),加 ``--apply`` 才真正移动。

用法::

    uv run python scripts/migrate_workspace_layout.py            # 看计划
    uv run python scripts/migrate_workspace_layout.py --apply    # 执行
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.harness.sandbox.config import SlotFlowSandboxConfig  # noqa: E402
from app.harness.sandbox.layout import (  # noqa: E402
    ARTIFACTS_DIR_NAME,
    LEGACY_ARTIFACTS_DIR,
    LEGACY_UPLOADS_DIR,
    LEGACY_WORK_DIR,
    UPLOAD_ORIGINALS_DIR,
    WORK_DIR_NAME,
    thread_dir_name,
)


class Plan:
    """收集「从哪搬到哪」,先打印再执行,避免边扫边改。"""

    def __init__(self) -> None:
        self.moves: list[tuple[Path, Path]] = []
        self.metadata_rewrites: list[tuple[Path, str, str]] = []
        self.skipped: list[str] = []

    def move(self, source: Path, target: Path) -> None:
        self.moves.append((source, target))

    def rewrite(self, path: Path, old: str, new: str) -> None:
        self.metadata_rewrites.append((path, old, new))

    def skip(self, reason: str) -> None:
        self.skipped.append(reason)


def build_plan(root: Path) -> Plan:
    plan = Plan()

    # 工作区根下散落的文件(历史上有工具直接往根写)。新布局要求根下只有对话目录,
    # 把它们归进旧布局的 artifacts/ 桶——前端本来就有「未归类产物」分组,
    # 顺带让这些文件第一次变得可见,而不是躺在根目录没人管。
    for child in sorted(root.iterdir()):
        if child.is_file() and not child.name.startswith("."):
            plan.move(child, root / LEGACY_ARTIFACTS_DIR / child.name)

    legacy_artifacts = root / LEGACY_ARTIFACTS_DIR
    if legacy_artifacts.is_dir():
        loose = 0
        for child in sorted(legacy_artifacts.iterdir()):
            if child.is_dir():
                plan.move(child, root / thread_dir_name(child.name) / ARTIFACTS_DIR_NAME)
            else:
                loose += 1
        if loose:
            plan.skip(
                f"{LEGACY_ARTIFACTS_DIR}/ 下 {loose} 个散落文件不属于任何对话,"
                "留在原地(前端归入「未归类产物」)"
            )

    legacy_work = root / LEGACY_WORK_DIR
    if legacy_work.is_dir():
        for child in sorted(legacy_work.iterdir()):
            if child.is_dir():
                plan.move(child, root / thread_dir_name(child.name) / WORK_DIR_NAME)
            else:
                plan.move(child, root / "default" / WORK_DIR_NAME / child.name)

    legacy_uploads = root / LEGACY_UPLOADS_DIR
    if legacy_uploads.is_dir():
        staged_runs = 0
        for child in sorted(legacy_uploads.iterdir()):
            if not child.is_dir():
                continue
            if not child.name.startswith("file_"):
                staged_runs += 1
                continue
            plan.move(child, root / UPLOAD_ORIGINALS_DIR / child.name)
            metadata = child / "metadata.json"
            old_prefix = f"{LEGACY_UPLOADS_DIR}/{child.name}/"
            new_prefix = f"{UPLOAD_ORIGINALS_DIR}/{child.name}/"
            if metadata.is_file():
                try:
                    stored = json.loads(metadata.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    plan.skip(f"{metadata} 读不出来,跳过改写")
                    continue
                workspace_path = stored.get("workspace_path")
                if isinstance(workspace_path, str) and workspace_path.startswith(old_prefix):
                    plan.rewrite(
                        root / UPLOAD_ORIGINALS_DIR / child.name / "metadata.json",
                        workspace_path,
                        new_prefix + workspace_path[len(old_prefix) :],
                    )
        if staged_runs:
            plan.skip(
                f"{LEGACY_UPLOADS_DIR}/ 下 {staged_runs} 个 run 目录留在原地:"
                "run→对话的对应只存在聊天库里,搬动需同步改写数据库,后端已保留旧路径读取"
            )

    return plan


def apply_plan(plan: Plan) -> None:
    for source, target in plan.moves:
        if not source.exists():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            # 目标已存在时逐个文件合并,避免整目录 move 直接失败或覆盖。
            _merge_into(source, target)
        else:
            shutil.move(str(source), str(target))

    for metadata_path, old, new in plan.metadata_rewrites:
        if not metadata_path.is_file():
            continue
        stored = json.loads(metadata_path.read_text(encoding="utf-8"))
        if stored.get("workspace_path") != old:
            continue
        stored["workspace_path"] = new
        metadata_path.write_text(
            json.dumps(stored, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


def _merge_into(source: Path, target: Path) -> None:
    if source.is_file():
        if not target.exists():
            shutil.move(str(source), str(target))
        return
    target.mkdir(parents=True, exist_ok=True)
    for child in list(source.iterdir()):
        _merge_into(child, target / child.name)
    if not any(source.iterdir()):
        source.rmdir()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="真正执行移动(默认只打印计划)")
    parser.add_argument("--workspace-root", default=None, help="覆盖工作区根目录")
    args = parser.parse_args()

    config = SlotFlowSandboxConfig(
        workspace_root=Path(args.workspace_root) if args.workspace_root else None
    )
    root = config.resolved_workspace_root()
    if not root.is_dir():
        print(f"工作区不存在:{root}")
        return 1

    plan = build_plan(root)
    print(f"工作区:{root}\n")
    if not plan.moves and not plan.metadata_rewrites:
        print("没有需要迁移的内容(可能已经是新布局)。")
    for source, target in plan.moves:
        print(f"  移动  {source.relative_to(root)}  →  {target.relative_to(root)}")
    for metadata_path, old, new in plan.metadata_rewrites:
        print(f"  改写  {metadata_path.relative_to(root)}:{old} → {new}")
    for reason in plan.skipped:
        print(f"  保留  {reason}")

    if not args.apply:
        print("\n以上是计划。加 --apply 才会真正执行。")
        return 0

    apply_plan(plan)
    print("\n✅ 迁移完成。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
