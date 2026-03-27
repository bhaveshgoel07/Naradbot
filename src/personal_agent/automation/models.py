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


@dataclass(slots=True)
class PiTaskResult:
    """Structured Pi command execution result."""

    available: bool
    command: list[str]
    exit_code: int
    stdout: str
    stderr: str
    duration_seconds: float


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
    base_branch: str | None = None
    branch_name: str | None = None
    commit_sha: str | None = None
    pr_url: str | None = None
    changes_detected: bool = False
    review_required: bool = False
    setup_commands: list[list[str]] = field(default_factory=list)
    git_status: str = ""


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
