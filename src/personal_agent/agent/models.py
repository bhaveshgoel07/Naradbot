from __future__ import annotations

from dataclasses import dataclass, field

from personal_agent.automation.models import PiToolExecution


@dataclass(slots=True)
class AgentRuntimeContext:
    """Runtime metadata attached to a transport-neutral agent response."""

    transport: str
    sandbox_mode: str | None = None
    sandbox_name: str | None = None
    sandbox_image: str | None = None
    duration_seconds: float | None = None


@dataclass(slots=True)
class AgentArtifact:
    """Artifact emitted by the agent, such as a workspace or PR URL."""

    kind: str
    label: str
    value: str
    url: str | None = None


@dataclass(slots=True)
class AgentFollowUp:
    """Transport-neutral next action for an agent response."""

    action: str
    label: str
    data: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class AgentResponse:
    """Canonical result returned by the central agent orchestrator."""

    kind: str
    ok: bool
    final_text: str = ""
    error_text: str = ""
    session_id: str | None = None
    exit_code: int = 0
    runtime: AgentRuntimeContext = field(
        default_factory=lambda: AgentRuntimeContext(transport="api")
    )
    tool_traces: list[PiToolExecution] = field(default_factory=list)
    artifacts: list[AgentArtifact] = field(default_factory=list)
    followups: list[AgentFollowUp] = field(default_factory=list)


@dataclass(slots=True)
class AgentMessageRequest:
    """Transport-neutral request for a conversational agent turn."""

    prompt: str
    transport: str = "api"
    session_id: str | None = None
    conversation_id: str | None = None
    actor_id: str | None = None
    requested_by: str | None = None
    workdir: str | None = None
    files: list[str] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)
    provider: str | None = None
    model: str | None = None
    thinking: str | None = None
    append_system_prompt: str | None = None
    timeout_seconds: int | None = None


@dataclass(slots=True)
class AgentRepositoryRequest:
    """Transport-neutral request for a repo preparation task."""

    repo_url: str
    prompt: str
    transport: str = "api"
    requested_by: str | None = None
    base_branch: str | None = None
    branch_name: str | None = None
    pr_title: str | None = None
    pr_body: str | None = None
    provider: str | None = None
    model: str | None = None
    thinking: str | None = None
    append_system_prompt: str | None = None
    timeout_seconds: int | None = None
    tools: list[str] = field(default_factory=list)
    allow_push: bool = False


@dataclass(slots=True)
class AgentRepositoryPushRequest:
    """Transport-neutral request for approving a prepared repo push."""

    workspace_id: str
    transport: str = "api"
    requested_by: str | None = None
    pr_title: str | None = None
    pr_body: str | None = None
    base_branch: str | None = None
