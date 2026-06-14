"""FastAPI routes for user-managed SlotFlow skills."""

from __future__ import annotations

import hashlib
import re
import shutil
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, Request, Response, UploadFile

from app.chat.runtime import (
    DEFAULT_SKILLS_ROOT,
    SlotFlowRuntimeConfig,
    refresh_runtime_skills_config,
)
from app.harness.skills import load_enabled_skills
from app.harness.skills.store import ProtectedSkillError, SlotFlowSkillsConfigStore
from app.skills.models import SkillInstallRequest, SkillRecord, SkillUpdateRequest


router = APIRouter(prefix="/api/skills", tags=["Skills"])
MAX_SKILL_FOLDER_UPLOAD_BYTES = 5 * 1024 * 1024


@router.get("", response_model=list[SkillRecord])
async def list_skills(request: Request) -> list[SkillRecord]:
    """List valid skills in the configured skills root."""

    root = get_skills_root(request)
    runtime_config = get_runtime_config(request)
    store = get_skills_config_store(request)
    if store is not None:
        store.ensure_default_find_skills()
        refresh_runtime_skills_config(runtime_config)
    skills = load_enabled_skills(
        skills_root=root,
        enabled_names=None,
    )
    return [skill_to_record(root, skill, store=store) for skill in skills]


@router.post("/upload", response_model=list[SkillRecord])
async def upload_skill(
    request: Request,
    files: list[UploadFile] = File(...),
) -> list[SkillRecord]:
    """Upload one skill folder, preserving its relative file structure."""

    if not files:
        raise HTTPException(status_code=400, detail="skill folder is empty")

    root = get_skills_root(request)
    store = get_skills_config_store(request)
    total_bytes = 0
    uploaded_paths: list[Path] = []

    for file in files:
        relative_path = safe_skill_relative_path(file.filename or "")
        if store is not None and store.is_protected(relative_path.parts[0]):
            raise HTTPException(status_code=403, detail="protected skill cannot be overwritten")
        data = await file.read()
        total_bytes += len(data)
        if total_bytes > MAX_SKILL_FOLDER_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail="skill folder too large")

        target = root / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        uploaded_paths.append(relative_path)

    skills = uploaded_skills(root=root, uploaded_paths=uploaded_paths)
    if not skills:
        raise HTTPException(status_code=400, detail="uploaded folder must contain a valid SKILL.md")

    for skill in skills:
        if store is not None and store.is_protected(skill.name):
            raise HTTPException(status_code=403, detail="protected skill cannot be overwritten")
        if store is not None:
            store.mark_skill(skill.name, enabled=True, protected=False, source="user")
        enable_skill_for_runtime(request, skill.name)
    refresh_runtime_skills_config(get_runtime_config(request))
    return [skill_to_record(root, skill, store=store) for skill in skills]


@router.post("/install", response_model=SkillRecord)
async def install_skill(
    body: SkillInstallRequest,
    request: Request,
) -> SkillRecord:
    """Install one skill through the public skills.sh CLI."""

    root = get_skills_root(request)
    store = get_skills_config_store(request)
    if store is None:
        raise HTTPException(status_code=503, detail="skills config store is not configured")

    try:
        store.install_skill_from_registry(
            package_url=body.package_url,
            skill_name=body.skill_name,
        )
    except ProtectedSkillError as exc:
        raise HTTPException(status_code=403, detail="protected skill cannot be overwritten") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    refresh_runtime_skills_config(get_runtime_config(request))
    skill = find_skill_by_name(root, body.skill_name)
    if skill is None:
        raise HTTPException(status_code=500, detail="installed skill is not readable")
    return skill_to_record(root, skill, store=store)


@router.patch("/{skill_name}", response_model=SkillRecord)
async def update_skill(
    skill_name: str,
    body: SkillUpdateRequest,
    request: Request,
) -> SkillRecord:
    """Enable or disable one skill."""

    root = get_skills_root(request)
    store = get_skills_config_store(request)
    if store is None:
        raise HTTPException(status_code=503, detail="skills config store is not configured")
    skill = find_skill_by_name(root, skill_name)
    if skill is None:
        raise HTTPException(status_code=404, detail="skill not found")

    store.set_enabled(skill.name, body.enabled)
    refresh_runtime_skills_config(get_runtime_config(request))
    return skill_to_record(root, skill, store=store)


@router.delete("/{skill_name}", status_code=204)
async def delete_skill(skill_name: str, request: Request) -> Response:
    """Delete a user skill by its declared name."""

    root = get_skills_root(request)
    store = get_skills_config_store(request)
    skill = find_skill_by_name(root, skill_name)
    if skill is None:
        raise HTTPException(status_code=404, detail="skill not found")
    if store is not None and store.is_protected(skill.name):
        raise HTTPException(status_code=403, detail="protected skill cannot be deleted")

    resolved_root = root.resolve()
    resolved_dir = skill.skill_dir.resolve()
    if resolved_root not in resolved_dir.parents and resolved_dir != resolved_root:
        raise HTTPException(status_code=400, detail="invalid skill path")

    shutil.rmtree(resolved_dir)
    if store is not None:
        try:
            store.remove_skill_config(skill.name)
        except ProtectedSkillError as exc:
            raise HTTPException(status_code=403, detail="protected skill cannot be deleted") from exc
    runtime_config = get_runtime_config(request)
    if runtime_config.enabled_skills is not None:
        runtime_config.enabled_skills.discard(skill.name)
    return Response(status_code=204)


def get_runtime_config(request: Request) -> SlotFlowRuntimeConfig:
    runtime_config = getattr(request.app.state, "runtime_config", None)
    if runtime_config is None:
        raise HTTPException(status_code=503, detail="runtime config is not configured")
    return runtime_config


def get_skills_root(request: Request) -> Path:
    root = get_runtime_config(request).skills_root or DEFAULT_SKILLS_ROOT
    root.mkdir(parents=True, exist_ok=True)
    return root


def get_skills_config_store(request: Request) -> SlotFlowSkillsConfigStore | None:
    return get_runtime_config(request).skills_config_store


def enable_skill_for_runtime(request: Request, skill_name: str) -> None:
    runtime_config = get_runtime_config(request)
    if runtime_config.enabled_skills is not None:
        runtime_config.enabled_skills.add(skill_name)


def safe_skill_relative_path(filename: str) -> Path:
    raw_parts = filename.replace("\\", "/").split("/")
    safe_parts = [safe_path_part(part) for part in raw_parts if part and part not in {".", ".."}]
    if not safe_parts:
        raise HTTPException(status_code=400, detail="invalid skill file path")
    relative_path = Path(*safe_parts)
    if relative_path.is_absolute() or ".." in relative_path.parts:
        raise HTTPException(status_code=400, detail="invalid skill file path")
    return relative_path


def safe_path_part(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._")
    if cleaned:
        return cleaned[:120]
    digest = hashlib.sha1(value.encode("utf-8")).hexdigest()[:12]
    return f"item_{digest}"


def find_skill_by_name(root: Path, skill_name: str):
    for skill in load_enabled_skills(skills_root=root, enabled_names=None):
        if skill.name == skill_name:
            return skill
    return None


def uploaded_skills(*, root: Path, uploaded_paths: list[Path]):
    skill_dirs = {
        (root / relative_path).parent.resolve()
        for relative_path in uploaded_paths
        if relative_path.name == "SKILL.md"
    }
    return [
        skill
        for skill in load_enabled_skills(skills_root=root, enabled_names=None)
        if skill.skill_dir.resolve() in skill_dirs
    ]


def skill_to_record(
    root: Path,
    skill,
    *,
    store: SlotFlowSkillsConfigStore | None = None,
) -> SkillRecord:
    config = store.get_config(skill.name) if store is not None else None
    return SkillRecord(
        name=skill.name,
        description=skill.description,
        path=str(skill.skill_file.relative_to(root)),
        enabled=config.enabled if config is not None else skill.enabled,
        protected=config.protected if config is not None else False,
        source=config.source if config is not None else "user",
    )
