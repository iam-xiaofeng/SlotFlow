"""SlotFlow subagent profile config."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class SlotFlowSubagentProfile:
    """A small local profile that can receive delegated tasks."""

    name: str
    description: str
    system_prompt: str
    capabilities: tuple[str, ...] = ()
    output_contract: str = "Return findings, evidence, risks, and next steps."
    enabled: bool = True


DEFAULT_SUBAGENT_PROFILES: tuple[SlotFlowSubagentProfile, ...] = (
    SlotFlowSubagentProfile(
        name="researcher",
        description="Gather facts, sources, and unknowns for a focused question.",
        system_prompt="You are a focused research subagent. Return concise findings and gaps.",
        capabilities=(
            "web and document research",
            "source comparison",
            "open-question tracking",
        ),
        output_contract="Return bullet findings with sources, confidence, and unresolved gaps.",
    ),
    SlotFlowSubagentProfile(
        name="analyst",
        description="Turn raw facts, metrics, or tables into a structured conclusion.",
        system_prompt="You are an analytical subagent. Explain assumptions and derive conclusions.",
        capabilities=(
            "data interpretation",
            "metric comparison",
            "scenario reasoning",
        ),
        output_contract="Return assumptions, calculations, conclusion, and caveats.",
    ),
    SlotFlowSubagentProfile(
        name="planner",
        description="Break a complex request into ordered work with dependencies and checks.",
        system_prompt="You are a planning subagent. Make the smallest executable plan.",
        capabilities=(
            "task decomposition",
            "dependency ordering",
            "verification planning",
        ),
        output_contract="Return ordered steps, required inputs, blockers, and verification.",
    ),
    SlotFlowSubagentProfile(
        name="coder",
        description="Investigate implementation details and propose code-level changes.",
        system_prompt="You are a coding subagent. Return concrete files, functions, and change notes.",
        capabilities=(
            "codebase inspection",
            "implementation sketching",
            "test targeting",
        ),
        output_contract="Return files, relevant symbols, proposed changes, and tests.",
    ),
    SlotFlowSubagentProfile(
        name="reviewer",
        description="Review a proposed answer or implementation for risks and missing checks.",
        system_prompt="You are a review subagent. Return risks, tests, and unresolved assumptions.",
        capabilities=(
            "bug finding",
            "test gap review",
            "behavioral regression review",
        ),
        output_contract="Return severity-ranked findings, missing tests, and residual risk.",
    ),
    SlotFlowSubagentProfile(
        name="writer",
        description="Draft or polish user-facing documents, reports, and release text.",
        system_prompt="You are a writing subagent. Keep text concrete, structured, and concise.",
        capabilities=(
            "report drafting",
            "README and changelog writing",
            "tone and structure editing",
        ),
        output_contract="Return the proposed text plus assumptions that need confirmation.",
    ),
)


@dataclass(frozen=True, slots=True)
class SlotFlowSubagentConfig:
    """Subagent tool configuration used by the harness tools registry."""

    profiles: tuple[SlotFlowSubagentProfile, ...] = field(
        default_factory=lambda: DEFAULT_SUBAGENT_PROFILES
    )
    recursion_limit: int = 100

    def enabled_profiles(self) -> tuple[SlotFlowSubagentProfile, ...]:
        """Return enabled profiles in configured order."""

        return tuple(profile for profile in self.profiles if profile.enabled)
