"""SlotFlow subagent profile config."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class SlotFlowSubagentProfile:
    """A small local profile that can receive delegated tasks."""

    name: str
    description: str
    system_prompt: str
    enabled: bool = True


DEFAULT_SUBAGENT_PROFILES: tuple[SlotFlowSubagentProfile, ...] = (
    SlotFlowSubagentProfile(
        name="researcher",
        description="Gather facts, sources, and unknowns for a focused question.",
        system_prompt="You are a focused research subagent. Return concise findings and gaps.",
    ),
    SlotFlowSubagentProfile(
        name="coder",
        description="Investigate implementation details and propose code-level changes.",
        system_prompt="You are a coding subagent. Return concrete files, functions, and change notes.",
    ),
    SlotFlowSubagentProfile(
        name="reviewer",
        description="Review a proposed answer or implementation for risks and missing checks.",
        system_prompt="You are a review subagent. Return risks, tests, and unresolved assumptions.",
    ),
)


@dataclass(frozen=True, slots=True)
class SlotFlowSubagentConfig:
    """Subagent tool configuration used by the harness tools registry."""

    profiles: tuple[SlotFlowSubagentProfile, ...] = field(
        default_factory=lambda: DEFAULT_SUBAGENT_PROFILES
    )

    def enabled_profiles(self) -> tuple[SlotFlowSubagentProfile, ...]:
        """Return enabled profiles in configured order."""

        return tuple(profile for profile in self.profiles if profile.enabled)
