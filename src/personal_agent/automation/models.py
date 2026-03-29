from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class PiTaskRequest:
    """Single non-interactive Pi task request."""

    prompt: str
    workdir: str | None = None
    files: list[str] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)
    provider: str | None = None
    model: str | None = None
    thinking: str | None = None
    append_system_prompt: str | None = None
    timeout_seconds: int | None = None
    session_id: str | None = None
    structured_output: bool = False


@dataclass(slots=True)
class PiToolExecution:
    """Single structured Pi tool execution emitted during a task."""

    tool_name: str
    tool_call_id: str | None = None
    arguments: dict[str, object] = field(default_factory=dict)
    output: str = ""
    is_error: bool = False


@dataclass(slots=True)
class PiTaskResult:
    """Structured Pi command execution result."""

    available: bool
    command: list[str]
    exit_code: int
    stdout: str
    stderr: str
    duration_seconds: float
    session_id: str | None = None
    assistant_response: str = ""
    tool_traces: list[PiToolExecution] = field(default_factory=list)
    sandbox_mode: str | None = None
    sandbox_name: str | None = None
    sandbox_image: str | None = None


@dataclass(slots=True)
class PiRepositoryTaskRequest:
    """Repository-scoped Pi task request executed in an isolated temp workspace."""

    repo_url: str
    prompt: str
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
    requested_by: str | None = None
    allow_push: bool = False


@dataclass(slots=True)
class PiRepositoryTaskResult:
    """Result of an isolated clone/edit/commit/PR workflow."""

    available: bool
    command: list[str]
    exit_code: int
    stdout: str
    stderr: str
    duration_seconds: float
    sandbox_mode: str
    workspace_dir: str | None
    repo_dir: str | None
    repo_url: str
    sandbox_name: str | None = None
    sandbox_image: str | None = None
    workspace_id: str | None = None
    base_branch: str | None = None
    branch_name: str | None = None
    commit_sha: str | None = None
    pr_url: str | None = None
    changes_detected: bool = False
    review_required: bool = False
    push_pending: bool = False
    setup_commands: list[list[str]] = field(default_factory=list)
    git_status: str = ""


@dataclass(slots=True)
class PiRepositoryPushRequest:
    """Approve and push an already prepared repository sandbox."""

    workspace_id: str
    pr_title: str | None = None
    pr_body: str | None = None
    base_branch: str | None = None
    requested_by: str | None = None


@dataclass(slots=True)
class PiRepositoryPushResult:
    """Result for a push/PR attempt from an existing sandbox."""

    available: bool
    command: list[str]
    exit_code: int
    stdout: str
    stderr: str
    duration_seconds: float
    sandbox_mode: str
    workspace_id: str
    workspace_dir: str | None
    repo_dir: str | None
    repo_url: str | None
    sandbox_name: str | None = None
    sandbox_image: str | None = None
    branch_name: str | None = None
    base_branch: str | None = None
    pr_url: str | None = None
    review_required: bool = False


@dataclass(slots=True)
class CandidateProfile:
    """User-provided application details used by job automation."""

    full_name: str | None = None
    email: str | None = None
    phone: str | None = None
    location: str | None = None
    linkedin_url: str | None = None
    github_url: str | None = None
    portfolio_url: str | None = None
    resume_path: str | None = None
    cover_letter_path: str | None = None
    extra_notes: str | None = None


@dataclass(slots=True)
class JobApplicationRequest:
    """Job-application workflow request."""

    job_url: str
    company_name: str | None = None
    role_title: str | None = None
    notes: str | None = None
    submit: bool = False


@dataclass(slots=True)
class JobApplicationResult:
    """Result of a job application planning/execution step."""

    status: str
    fit_summary: str
    automation_result: dict[str, object] | None
    profile_missing_fields: list[str] = field(default_factory=list)
