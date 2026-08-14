"""清理 `backend/.slotflow` 里已经没人用的东西。

默认只打印计划(dry-run),加 ``--apply`` 才真正删除。用法::

    uv run python scripts/prune_slotflow_storage.py            # 看计划
    uv run python scripts/prune_slotflow_storage.py --apply    # 执行

清理四类,每类的判据都写在下面对应的函数里:

1. **孤儿 thread 目录** —— 聊天库里已经没有这个对话了,目录却还在。
2. **评测残留目录** —— ``--live`` 评测用样本 id 当 thread_id,跑完留下 ``eval-*``。
3. **可再生的运行时目录** —— ``.playwright-mcp`` 这类进程状态,删了下次自动重建。
4. **历史 checkpoint 版本** —— 占大头的就是这个。

**关于第 4 类**:LangGraph 每个 super-step 都写一份**完整** checkpoint,而且永不回收。
真机实测 7 个对话 132 MB,其中一个 9 条消息的对话有 156 个版本、72 MB;传过大文件的那个
对话每个版本都把同一份 373K 字符的工具结果重新序列化一遍。恢复对话只需要**最新**那一份,
旧版本只服务 time-travel(``aget_state_history``),SlotFlow 没有用到。所以默认保留每个
thread 最新的 N 个版本(``--keep``,默认 3,留一点余量),其余连同它们的 ``writes`` 一起删。

**刻意不碰**的三类,删了会弄坏现有数据:

- ``.uploads/`` —— 上传原件,老消息的附件预览还在按 ``workspace_path`` 读它;
- 旧布局的 ``uploads/`` 和 ``artifacts/`` —— 迁移脚本当初就刻意没搬(同样的原因,
  见 ``migrate_workspace_layout.py`` 的模块注释),后端保留了旧路径读取分支;
- ``.slotflow_offload/`` —— 超长工具结果的卸载文件,老对话里的句柄还指向它。
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# 保留名单:以点开头的 SlotFlow 自有存储 + 旧布局遗留目录。理由见模块注释。
KEEP_DIRS = frozenset({".uploads", ".slotflow_offload", "uploads", "artifacts", "default"})
# 可再生的运行时目录:删掉下次自动重建,不含任何用户数据。
REGENERABLE_DIRS = frozenset({".playwright-mcp"})


def dir_size(path: Path) -> int:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def plan_workspace(workspace: Path, live_threads: set[str]) -> list[tuple[Path, str]]:
    """返回 [(目录, 删除理由)]。"""

    if not workspace.is_dir():
        return []
    victims: list[tuple[Path, str]] = []
    for entry in sorted(workspace.iterdir()):
        if not entry.is_dir():
            continue
        name = entry.name
        if name in KEEP_DIRS:
            continue
        if name in REGENERABLE_DIRS:
            victims.append((entry, "可再生的运行时状态"))
            continue
        if name.startswith("eval-"):
            victims.append((entry, "评测残留(--live 用样本 id 当 thread_id)"))
            continue
        if name.startswith("thread_") and name not in live_threads:
            victims.append((entry, "孤儿:聊天库里已无此对话"))
    return victims


def plan_checkpoints(db: Path, keep: int) -> dict[str, tuple[int, int]]:
    """返回 {thread_id: (可删版本数, 该 thread 总版本数)}。"""

    if not db.is_file():
        return {}
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        plan: dict[str, tuple[int, int]] = {}
        for (thread_id,) in conn.execute("select distinct thread_id from checkpoints"):
            ids = [
                row[0]
                for row in conn.execute(
                    # checkpoint_id 是单调递增的 uuid7,按它倒序就是按时间倒序。
                    "select checkpoint_id from checkpoints where thread_id=? "
                    "order by checkpoint_id desc",
                    (thread_id,),
                )
            ]
            if len(ids) > keep:
                plan[thread_id] = (len(ids) - keep, len(ids))
        return plan
    finally:
        conn.close()


def apply_checkpoint_prune(db: Path, keep: int) -> int:
    """删除每个 thread 除最新 `keep` 个之外的 checkpoint 及其 writes。"""

    conn = sqlite3.connect(db)
    try:
        removed = 0
        with conn:  # 单事务:要么全删要么不动
            for (thread_id,) in list(conn.execute("select distinct thread_id from checkpoints")):
                doomed = [
                    row[0]
                    for row in conn.execute(
                        "select checkpoint_id from checkpoints where thread_id=? "
                        "order by checkpoint_id desc limit -1 offset ?",
                        (thread_id, keep),
                    )
                ]
                if not doomed:
                    continue
                marks = ",".join("?" * len(doomed))
                # 先删 writes:它按 checkpoint_id 外键关联,留下就是悬空行。
                conn.execute(
                    f"delete from writes where thread_id=? and checkpoint_id in ({marks})",
                    (thread_id, *doomed),
                )
                conn.execute(
                    f"delete from checkpoints where thread_id=? and checkpoint_id in ({marks})",
                    (thread_id, *doomed),
                )
                removed += len(doomed)
        return removed
    finally:
        conn.close()


def try_vacuum(db: Path) -> str:
    """回收删除后留下的空洞。后端在跑时拿不到独占锁,失败不算错误。"""

    conn = sqlite3.connect(db)
    try:
        conn.execute("VACUUM")
        return "已 VACUUM 回收空间"
    except sqlite3.OperationalError as exc:
        return f"跳过 VACUUM({exc})——停掉后端再跑一次本脚本即可回收磁盘"
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="真正删除(默认只打印计划)")
    parser.add_argument(
        "--keep", type=int, default=3, help="每个对话保留多少个最新 checkpoint 版本(默认 3)"
    )
    parser.add_argument("--root", default=".slotflow", help="SlotFlow 存储根目录")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    workspace = root / "workspace"
    chat_db = root / "chat.sqlite3"
    checkpoint_db = root / "checkpoints.sqlite3"

    live_threads: set[str] = set()
    if chat_db.is_file():
        conn = sqlite3.connect(f"file:{chat_db}?mode=ro", uri=True)
        try:
            live_threads = {row[0] for row in conn.execute("select id from threads")}
        finally:
            conn.close()

    victims = plan_workspace(workspace, live_threads)
    freed = sum(dir_size(path) for path, _ in victims)
    print(f"[workspace] 可删目录 {len(victims)} 个,约 {freed / 1e6:.2f} MB")
    for path, reason in victims:
        print(f"   - {path.name:32s} {dir_size(path) / 1e6:7.2f} MB  {reason}")

    ck_plan = plan_checkpoints(checkpoint_db, args.keep)
    doomed_total = sum(count for count, _ in ck_plan.values())
    size_before = checkpoint_db.stat().st_size if checkpoint_db.is_file() else 0
    print(
        f"\n[checkpoints] 当前 {size_before / 1e6:.1f} MB;"
        f"每个对话保留最新 {args.keep} 个版本,可删 {doomed_total} 个版本"
    )
    for thread_id, (count, total) in sorted(ck_plan.items(), key=lambda kv: -kv[1][0]):
        print(f"   - {thread_id:30s} 删 {count:4d} / 共 {total:4d}")

    if not args.apply:
        print("\n(dry-run)加 --apply 才会真正删除。")
        return 0

    for path, _ in victims:
        shutil.rmtree(path, ignore_errors=True)
    print(f"\n已删除 {len(victims)} 个目录。")

    if doomed_total:
        removed = apply_checkpoint_prune(checkpoint_db, args.keep)
        print(f"已删除 {removed} 个历史 checkpoint 版本。{try_vacuum(checkpoint_db)}")
        size_after = checkpoint_db.stat().st_size
        print(f"checkpoints.sqlite3: {size_before / 1e6:.1f} MB → {size_after / 1e6:.1f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
