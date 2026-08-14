"""宿主侧读取已安装 Skill 的正文与附属文件。

**为什么需要它。** 在这之前,system prompt 里只有每个 Skill 的 `name: description`,正文
(SKILL.md 的操作步骤)唯一的读取路径是 `sandbox_exec` 去 `cat /skills/<name>/SKILL.md` ——
也就是说 Docker 不可用时 Skill 正文完全读不到,而 SlotFlow 本来就有一整套 Docker 降级路径。
把 Skill 绑死在容器上是设计错误。

现在的分工是**两段式**:目录(name + description)常驻 system prompt 供模型判断"要不要用";
正文由模型自己决定读哪个,经 `skill_read` 以**工具结果**的形式进上下文。好处有三:

1. 不读的 Skill 一个 token 都不占,装几十个 Skill 也不会挤掉 system 前缀;
2. 正文进的是 `messages` 通道,压缩时会被自然折叠(再靠 `used_skills` 台账记住读过谁);
3. 不依赖 Docker——纯本地文件读取。

正文刻意**不做超长卸载**(`steps/tool_output_offload.py` 只处理 `ToolMessage`,而
`skill_read` 返回 `Command`):把操作步骤挪去文件再让模型回读是本末倒置,模型需要的就是
这段文本本身在上下文里。体积改用这里的字符上限控制,截断时明确告诉模型还剩多少。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.harness.skills.parser import SKILL_FILE_NAME, extract_frontmatter
from app.harness.skills.registry import load_enabled_skills
from app.harness.skills.types import Skill

# 单次读取的字符上限。SKILL.md 正文通常几 KB;真超了就截断并告知剩余量,
# 由模型用 offset 续读,而不是静默吞掉后半段。
MAX_SKILL_READ_CHARS = 24_000
# 附属文件清单的条数上限(一个 Skill 带上百个模板时不至于刷屏)。
MAX_LISTED_FILES = 60
# 建议候选名的条数上限(读错名字时的提示)。
MAX_SUGGESTIONS = 20

_SOURCE = "slotflow_skill_read"


def read_skill(
    *,
    skills_root: Path | None,
    name: str,
    path: str = "",
    offset: int = 0,
    max_chars: int = MAX_SKILL_READ_CHARS,
) -> dict[str, Any]:
    """读取一个已安装 Skill 的 SKILL.md 正文,或它目录下的某个附属文件。

    `path` 为空读 SKILL.md 正文(去掉 frontmatter);否则读 Skill 目录内的相对路径文件。
    `offset` 是字符偏移,用于续读被截断的长正文。
    """

    clean_name = (name or "").strip()
    if not clean_name:
        return {"error": "empty_skill_name", "source": _SOURCE}
    if skills_root is None:
        return {"error": "skills_root_not_configured", "skill": clean_name, "source": _SOURCE}

    skills = load_enabled_skills(skills_root=skills_root, enabled_names=None)
    skill = _resolve_skill(skills, clean_name)
    if skill is None:
        return {
            "error": "skill_not_found",
            "skill": clean_name,
            "available_skills": [item.name for item in skills[:MAX_SUGGESTIONS]],
            "hint": (
                "Call skill_list to see installed Skills, or skill_match to search them. "
                "Use the exact name from that result."
            ),
            "source": _SOURCE,
        }

    target, path_error = _resolve_target(skill, path)
    if path_error is not None:
        return {
            "error": path_error,
            "skill": skill.name,
            "path": path,
            "files": _list_files(skill.skill_dir),
            "source": _SOURCE,
        }

    try:
        raw = target.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return {
            "error": "binary_file",
            "skill": skill.name,
            "path": _relative_path(skill.skill_dir, target),
            "hint": (
                "This file is not UTF-8 text. Run it or inspect it inside the sandbox instead "
                "(installed Skills are mounted read-only at /skills)."
            ),
            "source": _SOURCE,
        }
    except OSError as exc:
        return {
            "error": "read_failed",
            "skill": skill.name,
            "path": _relative_path(skill.skill_dir, target),
            "detail": exc.__class__.__name__,
            "source": _SOURCE,
        }

    is_skill_file = target.name == SKILL_FILE_NAME and target.parent == skill.skill_dir
    body = _strip_frontmatter(raw) if is_skill_file else raw
    content, truncation = _slice_content(body, offset=offset, max_chars=max_chars)

    result: dict[str, Any] = {
        "skill": skill.name,
        "description": skill.description,
        "path": _relative_path(skill.skill_dir, target),
        "skill_dir": str(skill.skill_dir),
        "content": content,
        "source": _SOURCE,
    }
    result.update(truncation)
    if is_skill_file:
        result["files"] = _list_files(skill.skill_dir)
        result["usage"] = (
            "These are the Skill's own instructions — follow them for this task instead of "
            "improvising. Read a listed file with skill_read(name, path=...); run helper scripts "
            "with sandbox_exec (Skills are mounted read-only at /skills in the sandbox)."
        )
    return result


def _resolve_skill(skills: list[Skill], name: str) -> Skill | None:
    lowered = name.lower()
    for skill in skills:
        if skill.name == name:
            return skill
    for skill in skills:
        if skill.name.lower() == lowered:
            return skill
    # 模型偶尔会传目录名而不是 frontmatter 里的 name,兜一手。
    for skill in skills:
        if skill.skill_dir.name.lower() == lowered:
            return skill
    return None


def _resolve_target(skill: Skill, path: str) -> tuple[Path, str | None]:
    if not path.strip():
        return skill.skill_file, None

    candidate = Path(path.strip())
    if candidate.is_absolute():
        return skill.skill_file, "absolute_path_not_allowed"

    skill_dir = skill.skill_dir.resolve()
    resolved = (skill_dir / candidate).resolve()
    # 目录穿越防护:附属文件必须真的落在这个 Skill 自己的目录里。
    if resolved != skill_dir and skill_dir not in resolved.parents:
        return skill.skill_file, "path_outside_skill_dir"
    if not resolved.is_file():
        return skill.skill_file, "file_not_found"
    return resolved, None


def _strip_frontmatter(content: str) -> str:
    frontmatter = extract_frontmatter(content)
    if frontmatter is None:
        return content
    marker = f"---\n{frontmatter}\n---"
    if content.startswith(marker):
        return content[len(marker) :].lstrip("\n")
    # frontmatter 存在但行尾形态不同(例如 CRLF),退回按第二个 `---` 切。
    lines = content.splitlines()
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            return "\n".join(lines[index + 1 :]).lstrip("\n")
    return content


def _slice_content(body: str, *, offset: int, max_chars: int) -> tuple[str, dict[str, Any]]:
    total = len(body)
    start = max(0, offset)
    limit = max(1, max_chars)
    if start >= total:
        return "", {
            "truncated": False,
            "total_chars": total,
            "offset": start,
            "note": "offset is past the end of this file",
        }
    chunk = body[start : start + limit]
    end = start + len(chunk)
    if end >= total:
        return chunk, {"truncated": False, "total_chars": total, "offset": start}
    return chunk, {
        "truncated": True,
        "total_chars": total,
        "offset": start,
        "next_offset": end,
        "note": (
            f"{total - end} more characters remain. Continue with "
            f"skill_read(name, offset={end}) when you need the rest."
        ),
    }


def _list_files(skill_dir: Path) -> list[dict[str, Any]]:
    """列出 Skill 目录下的附属文件(脚本/模板/参考资料),不含 SKILL.md 本身。"""

    entries: list[dict[str, Any]] = []
    try:
        candidates = sorted(skill_dir.rglob("*"))
    except OSError:
        return entries
    for item in candidates:
        relative = item.relative_to(skill_dir)
        if any(part.startswith(".") for part in relative.parts):
            continue
        if not item.is_file() or relative.as_posix() == SKILL_FILE_NAME:
            continue
        try:
            size = item.stat().st_size
        except OSError:
            size = 0
        entries.append({"path": relative.as_posix(), "size_bytes": size})
        if len(entries) >= MAX_LISTED_FILES:
            entries.append({"path": "…", "note": "more files omitted; browse with sandbox_exec"})
            break
    return entries


def _relative_path(skill_dir: Path, target: Path) -> str:
    try:
        return target.relative_to(skill_dir).as_posix()
    except ValueError:
        return target.name
