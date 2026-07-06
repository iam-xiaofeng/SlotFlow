"""Agency-style role catalog for SlotFlow subagents.

The catalog is intentionally file-backed: the parent agent sees only compact
domain summaries, while a child subagent receives at most one selected role
template for its task.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any


CATALOG_ROOT = Path(__file__).with_name("agency_agents")
ROLES_ROOT = CATALOG_ROOT / "roles"
DIVISIONS_FILE = CATALOG_ROOT / "divisions.json"
MAX_ROLE_TEMPLATE_CHARS = 12000


@dataclass(frozen=True, slots=True)
class SubagentRoleDomain:
    """Layer-2 domain group exposed to the parent model."""

    slug: str
    label: str
    description: str
    divisions: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SubagentRoleSummary:
    """Compact role metadata safe for the parent model list output."""

    id: str
    name: str
    domain: str
    division: str
    description: str
    path: str


@dataclass(frozen=True, slots=True)
class SubagentRoleTemplate(SubagentRoleSummary):
    """A concrete role prompt loaded only for a delegated child run."""

    prompt: str
    truncated: bool = False


DEFAULT_ROLE_DOMAINS: tuple[SubagentRoleDomain, ...] = (
    SubagentRoleDomain(
        slug="engineering",
        label="Engineering",
        description="Software, security, testing, geospatial, spatial computing, and game implementation.",
        divisions=("engineering", "security", "testing", "gis", "spatial-computing", "game-development"),
    ),
    SubagentRoleDomain(
        slug="design",
        label="Design",
        description="UX, UI, visual systems, brand, image prompting, and inclusive design.",
        divisions=("design",),
    ),
    SubagentRoleDomain(
        slug="finance",
        label="Finance",
        description="Financial analysis, FP&A, tax, investment research, bookkeeping, and controller work.",
        divisions=("finance",),
    ),
    SubagentRoleDomain(
        slug="market",
        label="Market",
        description="Marketing, SEO, social platforms, paid media, growth, and content distribution.",
        divisions=("marketing", "paid-media"),
    ),
    SubagentRoleDomain(
        slug="sales",
        label="Sales",
        description="Sales strategy, proposals, discovery, outreach, support, and customer operations.",
        divisions=("sales", "support"),
    ),
    SubagentRoleDomain(
        slug="product",
        label="Product",
        description="Product management, feedback synthesis, prioritization, delivery, and project operations.",
        divisions=("product", "project-management"),
    ),
    SubagentRoleDomain(
        slug="research",
        label="Research",
        description="Academic, clinical, evidence, healthcare, and domain research workflows.",
        divisions=("academic", "healthcare"),
    ),
    SubagentRoleDomain(
        slug="specialized",
        label="Specialized",
        description="Specific professional workflows that do not fit the common business or engineering domains.",
        divisions=("specialized",),
    ),
)


class SubagentRoleCatalog:
    """Read-only role library derived from agency-agents markdown files."""

    def __init__(
        self,
        *,
        roles_root: Path = ROLES_ROOT,
        divisions_file: Path = DIVISIONS_FILE,
        domains: tuple[SubagentRoleDomain, ...] = DEFAULT_ROLE_DOMAINS,
    ) -> None:
        self._roles_root = roles_root
        self._division_labels = _load_division_labels(divisions_file)
        self._domains = domains
        self._domain_by_slug = {domain.slug: domain for domain in domains}
        self._division_to_domain = {
            division: domain.slug for domain in domains for division in domain.divisions
        }
        self._roles = self._scan_roles()
        self._role_by_key = {
            key: role
            for role in self._roles
            for key in {
                _normalize_key(role.id),
                _normalize_key(role.name),
                _normalize_key(Path(role.path).stem),
            }
            if key
        }

    def domains(self) -> list[dict[str, Any]]:
        """Return compact Layer-2 domain summaries for subagent_list."""

        summaries: list[dict[str, Any]] = []
        for domain in self._domains:
            roles = [role for role in self._roles if role.domain == domain.slug]
            summaries.append(
                {
                    "slug": domain.slug,
                    "label": domain.label,
                    "description": domain.description,
                    "divisions": [
                        {
                            "slug": division,
                            "label": self._division_labels.get(division, division),
                        }
                        for division in domain.divisions
                    ],
                    "role_count": len(roles),
                    "sample_roles": [
                        {
                            "id": role.id,
                            "name": role.name,
                            "division": role.division,
                            "description": role.description,
                        }
                        for role in roles[:5]
                    ],
                }
            )
        return summaries

    def search(
        self,
        *,
        query: str = "",
        domain: str = "",
        max_results: int = 8,
    ) -> list[dict[str, Any]]:
        """Return a compact role shortlist without loading role prompt bodies."""

        clean_domain = _normalize_key(domain)
        candidates = self._candidate_roles(clean_domain) if clean_domain else list(self._roles)
        safe_max_results = max(1, min(max_results, 20))
        clean_query = query.strip()
        if clean_query:
            scored = [
                (_score_role(role, clean_query), role)
                for role in candidates
            ]
            matches = [item for item in scored if item[0] > 0]
            matches.sort(key=lambda item: (-item[0], item[1].id))
            roles = [role for _score, role in matches[:safe_max_results]]
            if not roles:
                roles = sorted(candidates, key=lambda role: role.id)[:safe_max_results]
        else:
            roles = sorted(candidates, key=lambda role: role.id)[:safe_max_results]

        return [
            {
                "id": role.id,
                "name": role.name,
                "domain": role.domain,
                "division": role.division,
                "description": role.description,
                "path": role.path,
            }
            for role in roles
        ]

    def resolve(
        self,
        *,
        domain: str = "",
        role_name: str = "",
        task: str = "",
        context: str = "",
        expected_output: str = "",
    ) -> SubagentRoleTemplate | None:
        """Resolve a concrete role from explicit role name or domain-scoped task terms."""

        clean_domain = _normalize_key(domain)
        candidates = self._candidate_roles(clean_domain)
        clean_role_name = _normalize_key(role_name)
        if clean_role_name:
            explicit = self._role_by_key.get(clean_role_name)
            if explicit is not None and (not candidates or explicit in candidates):
                return self._load_template(explicit)
            for role in candidates or self._roles:
                if clean_role_name in {
                    _normalize_key(role.id),
                    _normalize_key(role.name),
                    _normalize_key(Path(role.path).stem),
                }:
                    return self._load_template(role)
            return None

        if not clean_domain:
            return None

        scored = [
            (_score_role(role, f"{task}\n{context}\n{expected_output}"), role)
            for role in candidates
        ]
        scored.sort(key=lambda item: (-item[0], item[1].id))
        if scored and scored[0][0] > 0:
            return self._load_template(scored[0][1])
        return None

    def _candidate_roles(self, clean_domain: str) -> list[SubagentRoleSummary]:
        if not clean_domain:
            return []
        if clean_domain in self._domain_by_slug:
            return [role for role in self._roles if role.domain == clean_domain]
        return [
            role
            for role in self._roles
            if _normalize_key(role.division) == clean_domain
            or _normalize_key(self._division_labels.get(role.division, "")) == clean_domain
        ]

    def _scan_roles(self) -> list[SubagentRoleSummary]:
        roles: list[SubagentRoleSummary] = []
        if not self._roles_root.exists():
            return roles
        for role_file in sorted(self._roles_root.glob("*/*.md")):
            division = role_file.parent.name
            domain = self._division_to_domain.get(division, "specialized")
            metadata, _body = _split_frontmatter(role_file.read_text(encoding="utf-8"))
            name = metadata.get("name") or _title_from_stem(role_file.stem)
            description = metadata.get("description") or ""
            roles.append(
                SubagentRoleSummary(
                    id=_role_id(role_file.stem),
                    name=name,
                    domain=domain,
                    division=division,
                    description=description,
                    path=role_file.relative_to(self._roles_root).as_posix(),
                )
            )
        return roles

    def _load_template(self, role: SubagentRoleSummary) -> SubagentRoleTemplate:
        role_file = self._roles_root / role.path
        _metadata, body = _split_frontmatter(role_file.read_text(encoding="utf-8"))
        prompt = body.strip()
        truncated = len(prompt) > MAX_ROLE_TEMPLATE_CHARS
        if truncated:
            prompt = prompt[:MAX_ROLE_TEMPLATE_CHARS].rstrip()
        return SubagentRoleTemplate(
            id=role.id,
            name=role.name,
            domain=role.domain,
            division=role.division,
            description=role.description,
            path=role.path,
            prompt=prompt,
            truncated=truncated,
        )


@lru_cache(maxsize=1)
def default_role_catalog() -> SubagentRoleCatalog:
    """Return the process-wide role catalog."""

    return SubagentRoleCatalog()


def _load_division_labels(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    divisions = data.get("divisions")
    if not isinstance(divisions, dict):
        return {}
    labels: dict[str, str] = {}
    for slug, value in divisions.items():
        if isinstance(slug, str) and isinstance(value, dict):
            label = value.get("label")
            if isinstance(label, str):
                labels[slug] = label
    return labels


def _split_frontmatter(content: str) -> tuple[dict[str, str], str]:
    if not content.startswith("---\n"):
        return {}, content
    end = content.find("\n---", 4)
    if end < 0:
        return {}, content
    raw = content[4:end]
    body = content[end + len("\n---") :]
    metadata: dict[str, str] = {}
    for line in raw.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        metadata[key.strip()] = value.strip().strip('"').strip("'")
    return metadata, body


def _role_id(stem: str) -> str:
    return _normalize_key(stem).replace("_", "-")


def _title_from_stem(stem: str) -> str:
    return stem.replace("-", " ").replace("_", " ").title()


def _normalize_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")


def _score_role(role: SubagentRoleSummary, query: str) -> int:
    terms = set(re.findall(r"[a-z0-9]+", query.lower()))
    if not terms:
        return 0
    haystack = f"{role.id} {role.name} {role.description} {role.division}".lower()
    score = 0
    for term in terms:
        if len(term) < 3:
            continue
        if term in haystack:
            score += 1
    return score
