from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shlex
import shutil
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from personal_agent.automation.models import (
    PiRepositoryPushRequest,
    PiRepositoryPushResult,
    PiRepositoryTaskRequest,
    PiRepositoryTaskResult,
    PiToolExecution,
    PiTaskRequest,
    PiTaskResult,
)
from personal_agent.config.settings import Settings
from personal_agent.execution.blaxel import BlaxelSandboxService


@dataclass(slots=True)
class _CommandResult:
    command: list[str]
    exit_code: int
    stdout: str
    stderr: str


@dataclass(slots=True)
class _StructuredPiOutput:
    assistant_response: str
    primary_output: str
    tool_traces: list[PiToolExecution]


@dataclass(slots=True)
class PiCodingAgentService:
    """Thin subprocess wrapper around the Pi coding-agent CLI."""

    settings: Settings
    sandbox_service: BlaxelSandboxService | None = None

    def status(self) -> dict[str, object]:
        command = shlex.split(self.settings.pi_command)
        executable = command[0] if command else None
        resolved_binary = shutil.which(executable) if executable else None
        git_binary = shutil.which("git")
        gh_binary = shutil.which("gh")
        blaxel_enabled = self._blaxel_enabled
        return {
            "configured_command": command,
            "resolved_binary": resolved_binary,
            "available": resolved_binary is not None or blaxel_enabled,
            "local_command_available": resolved_binary is not None,
            "default_tools": list(self.settings.pi_default_tools),
            "default_model": self.settings.pi_model_value,
            "default_provider": self.settings.pi_provider_value,
            "default_base_url": self.settings.pi_base_url_value,
            "default_thinking": self.settings.pi_default_thinking,
            "git_binary": git_binary,
            "gh_binary": gh_binary,
            "git_available": git_binary is not None,
            "gh_available": gh_binary is not None,
            "sandbox_mode": "blaxel_execution_sandbox" if blaxel_enabled else "local_subprocess",
            "cloud_agent_mode": "blaxel" if blaxel_enabled else "local_fallback",
            "workspace_root": str(self.settings.pi_workspace_root_path),
            "repo_workflow_available": (resolved_binary is not None and git_binary is not None)
            or blaxel_enabled,
            "blaxel_execution_enabled": blaxel_enabled,
            "blaxel_orchestrator_sandbox_name": self.settings.blaxel_orchestrator_sandbox_name,
            "blaxel_orchestrator_sandbox_image": self.settings.blaxel_orchestrator_sandbox_image,
            "blaxel_execution_sandbox_image": self.settings.blaxel_execution_sandbox_image,
            "blaxel_repo_sandbox_image": self.settings.blaxel_repo_sandbox_image,
            "blaxel_computer_use_sandbox_image": self.settings.blaxel_computer_use_sandbox_image,
            "requires_github_token_for_prs": True,
            "repo_push_requires_explicit_approval": True,
        }

    async def run_task(self, request: PiTaskRequest) -> PiTaskResult:
        if self._blaxel_enabled:
            try:
                assert self.sandbox_service is not None
                await self.sandbox_service.ensure_orchestrator_sandbox()
                return await self._run_task_in_execution_sandbox(request)
            except Exception as exc:  # noqa: BLE001
                if not self._allow_local_fallback:
                    return PiTaskResult(
                        available=True,
                        command=self._sanitize_command(
                            self._build_command(
                                request,
                                output_format="json" if request.structured_output else "text",
                                session_path=(
                                    str(self._local_session_path(request.session_id))
                                    if request.session_id
                                    else None
                                ),
                            )
                        ),
                        exit_code=1,
                        stdout="",
                        stderr=f"Blaxel orchestrator execution failed: {exc}",
                        duration_seconds=0.0,
                        session_id=request.session_id,
                        sandbox_mode="blaxel_execution_sandbox",
                        sandbox_image=self.settings.blaxel_execution_sandbox_image,
                    )
        session_path = self._local_session_path(request.session_id)
        command = self._build_command(
            request,
            output_format="json" if request.structured_output else "text",
            session_path=str(session_path) if session_path is not None else None,
        )
        start = time.monotonic()
        env, cleanup_dir = self._prepare_pi_command_env(request=request, base_env=None)
        try:
            result = await self._run_command(
                command,
                cwd=request.workdir or str(Path.cwd()),
                env=env,
                timeout_seconds=request.timeout_seconds
                or self.settings.pi_timeout_seconds,
            )
        finally:
            if cleanup_dir is not None:
                shutil.rmtree(cleanup_dir, ignore_errors=True)
        return self._build_pi_task_result(
            request=request,
            command=result.command,
            exit_code=result.exit_code,
            stdout=result.stdout,
            stderr=result.stderr,
            logs="",
            duration_seconds=round(time.monotonic() - start, 2),
            available=bool(self.status()["available"]),
            sandbox_mode="local_subprocess",
        )

    @property
    def _blaxel_enabled(self) -> bool:
        return bool(
            self.settings.blaxel_sandboxes_enabled and self.sandbox_service is not None
        )

    @property
    def _allow_local_fallback(self) -> bool:
        return self.settings.environment != "production"

    async def clear_task_session(self, session_id: str) -> bool:
        if not session_id:
            return False
        if self._blaxel_enabled:
            assert self.sandbox_service is not None
            orchestrator = await self.sandbox_service.ensure_orchestrator_sandbox()
            session_path = self._sandbox_session_path(
                workspace_root=self.sandbox_service.orchestrator_workdir(),
                session_id=session_id,
            )
            try:
                await self.sandbox_service.remove_path(orchestrator.name, str(session_path))
            except Exception:  # noqa: BLE001
                return False
            return True
        session_path = self._local_session_path(session_id)
        if session_path is None or not session_path.exists():
            return False
        session_path.unlink()
        return True

    async def run_repository_task(
        self, request: PiRepositoryTaskRequest
    ) -> PiRepositoryTaskResult:
        if self._blaxel_enabled:
            return await self._run_repository_task_in_blaxel(request)

        start = time.monotonic()
        status = self.status()
        setup_commands: list[list[str]] = []
        sandbox_mode = "isolated_repo_clone"

        if not status["local_command_available"]:
            return PiRepositoryTaskResult(
                available=False,
                command=[],
                exit_code=127,
                stdout="",
                stderr="Pi coding agent binary is not available.",
                duration_seconds=round(time.monotonic() - start, 2),
                sandbox_mode=sandbox_mode,
                workspace_dir=None,
                repo_dir=None,
                repo_url=request.repo_url,
            )
        if not status["git_available"]:
            return PiRepositoryTaskResult(
                available=True,
                command=[],
                exit_code=127,
                stdout="",
                stderr="git is required for repository workflows.",
                duration_seconds=round(time.monotonic() - start, 2),
                sandbox_mode=sandbox_mode,
                workspace_dir=None,
                repo_dir=None,
                repo_url=request.repo_url,
            )

        workspace_root = self.settings.pi_workspace_root_path
        workspace_root.mkdir(parents=True, exist_ok=True)
        sandbox_dir = Path(tempfile.mkdtemp(prefix="pi-repo-", dir=workspace_root))
        workspace_id = sandbox_dir.name
        repo_dir = sandbox_dir / "repo"
        env = self._build_sandbox_env(sandbox_dir)

        github_slug = self._github_repo_slug(request.repo_url)
        clone_url = self._clone_url(request.repo_url, github_slug=github_slug)
        clone_command = [
            "git",
            "clone",
            "--depth",
            "1",
            clone_url,
            str(repo_dir),
        ]
        setup_commands.append(self._sanitize_command(clone_command))
        clone_result = await self._run_command(
            clone_command,
            cwd=str(sandbox_dir),
            env=env,
            timeout_seconds=min(
                request.timeout_seconds or self.settings.pi_timeout_seconds,
                300,
            ),
        )
        if clone_result.exit_code != 0:
            return PiRepositoryTaskResult(
                available=True,
                command=self._sanitize_command(clone_result.command),
                exit_code=clone_result.exit_code,
                stdout=clone_result.stdout,
                stderr=clone_result.stderr,
                duration_seconds=round(time.monotonic() - start, 2),
                sandbox_mode=sandbox_mode,
                workspace_id=workspace_id,
                workspace_dir=str(sandbox_dir),
                repo_dir=str(repo_dir),
                repo_url=request.repo_url,
                setup_commands=setup_commands,
            )

        base_branch = request.base_branch
        if base_branch:
            fetch_command = ["git", "fetch", "origin", base_branch, "--depth", "1"]
            setup_commands.append(fetch_command)
            fetch_result = await self._run_command(
                fetch_command,
                cwd=str(repo_dir),
                env=env,
                timeout_seconds=120,
            )
            if fetch_result.exit_code != 0:
                return PiRepositoryTaskResult(
                    available=True,
                    command=self._sanitize_command(fetch_result.command),
                    exit_code=fetch_result.exit_code,
                    stdout=fetch_result.stdout,
                    stderr=fetch_result.stderr,
                    duration_seconds=round(time.monotonic() - start, 2),
                    sandbox_mode=sandbox_mode,
                    workspace_id=workspace_id,
                    workspace_dir=str(sandbox_dir),
                    repo_dir=str(repo_dir),
                    repo_url=request.repo_url,
                    base_branch=base_branch,
                    setup_commands=setup_commands,
                )
            checkout_base_command = ["git", "checkout", "-B", base_branch, "FETCH_HEAD"]
            setup_commands.append(checkout_base_command)
            checkout_base_result = await self._run_command(
                checkout_base_command,
                cwd=str(repo_dir),
                env=env,
                timeout_seconds=120,
            )
            if checkout_base_result.exit_code != 0:
                return PiRepositoryTaskResult(
                    available=True,
                    command=self._sanitize_command(checkout_base_result.command),
                    exit_code=checkout_base_result.exit_code,
                    stdout=checkout_base_result.stdout,
                    stderr=checkout_base_result.stderr,
                    duration_seconds=round(time.monotonic() - start, 2),
                    sandbox_mode=sandbox_mode,
                    workspace_id=workspace_id,
                    workspace_dir=str(sandbox_dir),
                    repo_dir=str(repo_dir),
                    repo_url=request.repo_url,
                    base_branch=base_branch,
                    setup_commands=setup_commands,
                )
        else:
            base_branch = await self._detect_current_branch(repo_dir, env)

        branch_name = request.branch_name or self._default_branch_name(request.prompt)
        checkout_command = ["git", "checkout", "-b", branch_name]
        setup_commands.append(checkout_command)
        checkout_result = await self._run_command(
            checkout_command,
            cwd=str(repo_dir),
            env=env,
            timeout_seconds=120,
        )
        if checkout_result.exit_code != 0:
            return PiRepositoryTaskResult(
                available=True,
                command=self._sanitize_command(checkout_result.command),
                exit_code=checkout_result.exit_code,
                stdout=checkout_result.stdout,
                stderr=checkout_result.stderr,
                duration_seconds=round(time.monotonic() - start, 2),
                sandbox_mode=sandbox_mode,
                workspace_id=workspace_id,
                workspace_dir=str(sandbox_dir),
                repo_dir=str(repo_dir),
                repo_url=request.repo_url,
                base_branch=base_branch,
                branch_name=branch_name,
                setup_commands=setup_commands,
            )

        pi_result = await self._run_task_with_env(
            PiTaskRequest(
                prompt=request.prompt,
                workdir=str(repo_dir),
                tools=list(request.tools),
                provider=request.provider,
                model=request.model,
                thinking=request.thinking,
                append_system_prompt=self._build_repo_system_prompt(
                    repo_dir=repo_dir,
                    extra_prompt=request.append_system_prompt,
                ),
                timeout_seconds=request.timeout_seconds,
            ),
            env=env,
        )
        if pi_result.exit_code != 0:
            return PiRepositoryTaskResult(
                available=pi_result.available,
                command=pi_result.command,
                exit_code=pi_result.exit_code,
                stdout=pi_result.stdout,
                stderr=pi_result.stderr,
                duration_seconds=round(time.monotonic() - start, 2),
                sandbox_mode=sandbox_mode,
                workspace_id=workspace_id,
                workspace_dir=str(sandbox_dir),
                repo_dir=str(repo_dir),
                repo_url=request.repo_url,
                base_branch=base_branch,
                branch_name=branch_name,
                setup_commands=setup_commands,
            )

        git_status_result = await self._run_command(
            ["git", "status", "--short"],
            cwd=str(repo_dir),
            env=env,
            timeout_seconds=30,
        )
        git_status = git_status_result.stdout.strip()
        changes_detected = bool(git_status)
        if not changes_detected:
            return PiRepositoryTaskResult(
                available=pi_result.available,
                command=pi_result.command,
                exit_code=pi_result.exit_code,
                stdout=pi_result.stdout,
                stderr=pi_result.stderr,
                duration_seconds=round(time.monotonic() - start, 2),
                sandbox_mode=sandbox_mode,
                workspace_id=workspace_id,
                workspace_dir=str(sandbox_dir),
                repo_dir=str(repo_dir),
                repo_url=request.repo_url,
                base_branch=base_branch,
                branch_name=branch_name,
                changes_detected=False,
                review_required=False,
                setup_commands=setup_commands,
                git_status=git_status,
            )

        for config_command in (
            ["git", "config", "user.name", self.settings.pi_git_author_name],
            ["git", "config", "user.email", self.settings.pi_git_author_email],
        ):
            setup_commands.append(config_command)
            config_result = await self._run_command(
                config_command,
                cwd=str(repo_dir),
                env=env,
                timeout_seconds=30,
            )
            if config_result.exit_code != 0:
                return PiRepositoryTaskResult(
                    available=pi_result.available,
                    command=self._sanitize_command(config_result.command),
                    exit_code=config_result.exit_code,
                    stdout=config_result.stdout,
                    stderr=config_result.stderr,
                    duration_seconds=round(time.monotonic() - start, 2),
                    sandbox_mode=sandbox_mode,
                    workspace_id=workspace_id,
                    workspace_dir=str(sandbox_dir),
                    repo_dir=str(repo_dir),
                    repo_url=request.repo_url,
                    base_branch=base_branch,
                    branch_name=branch_name,
                    changes_detected=True,
                    review_required=False,
                    setup_commands=setup_commands,
                    git_status=git_status,
                )

        add_result = await self._run_command(
            ["git", "add", "-A"],
            cwd=str(repo_dir),
            env=env,
            timeout_seconds=60,
        )
        if add_result.exit_code != 0:
            return PiRepositoryTaskResult(
                available=pi_result.available,
                command=self._sanitize_command(add_result.command),
                exit_code=add_result.exit_code,
                stdout=add_result.stdout,
                stderr=add_result.stderr,
                duration_seconds=round(time.monotonic() - start, 2),
                sandbox_mode=sandbox_mode,
                workspace_id=workspace_id,
                workspace_dir=str(sandbox_dir),
                repo_dir=str(repo_dir),
                repo_url=request.repo_url,
                base_branch=base_branch,
                branch_name=branch_name,
                changes_detected=True,
                review_required=False,
                setup_commands=setup_commands,
                git_status=git_status,
            )

        commit_message = self._default_commit_message(request)
        commit_result = await self._run_command(
            ["git", "commit", "-m", commit_message],
            cwd=str(repo_dir),
            env=env,
            timeout_seconds=120,
        )
        if commit_result.exit_code != 0:
            return PiRepositoryTaskResult(
                available=pi_result.available,
                command=self._sanitize_command(commit_result.command),
                exit_code=commit_result.exit_code,
                stdout=commit_result.stdout,
                stderr=commit_result.stderr,
                duration_seconds=round(time.monotonic() - start, 2),
                sandbox_mode=sandbox_mode,
                workspace_id=workspace_id,
                workspace_dir=str(sandbox_dir),
                repo_dir=str(repo_dir),
                repo_url=request.repo_url,
                base_branch=base_branch,
                branch_name=branch_name,
                changes_detected=True,
                review_required=False,
                setup_commands=setup_commands,
                git_status=git_status,
            )

        commit_sha_result = await self._run_command(
            ["git", "rev-parse", "HEAD"],
            cwd=str(repo_dir),
            env=env,
            timeout_seconds=30,
        )
        commit_sha = commit_sha_result.stdout.strip() or None

        if not request.allow_push:
            pending_message = (
                "Push is pending approval. Approve with the workspace_id "
                "using the dedicated repo push endpoint or Discord command."
            )
            stderr = "\n".join(
                part for part in [pi_result.stderr, pending_message] if part
            )
            return PiRepositoryTaskResult(
                available=pi_result.available,
                command=pi_result.command,
                exit_code=pi_result.exit_code,
                stdout=pi_result.stdout,
                stderr=stderr,
                duration_seconds=round(time.monotonic() - start, 2),
                sandbox_mode=sandbox_mode,
                workspace_id=workspace_id,
                workspace_dir=str(sandbox_dir),
                repo_dir=str(repo_dir),
                repo_url=request.repo_url,
                base_branch=base_branch,
                branch_name=branch_name,
                commit_sha=commit_sha,
                changes_detected=True,
                review_required=False,
                push_pending=True,
                setup_commands=setup_commands,
                git_status=git_status,
            )

        pr_url: str | None = None
        stderr = pi_result.stderr
        review_required = False
        github_token = self.settings.pi_github_token_value
        if github_slug is not None and github_token is not None:
            push_result = await self._run_command(
                ["git", "push", "-u", "origin", branch_name],
                cwd=str(repo_dir),
                env=env,
                timeout_seconds=300,
            )
            if push_result.exit_code == 0:
                try:
                    pr_url = await self._create_pull_request(
                        github_slug=github_slug,
                        github_token=github_token,
                        title=request.pr_title or self._default_pr_title(request.prompt),
                        body=self._default_pr_body(request),
                        head=branch_name,
                        base=base_branch or "main",
                    )
                    review_required = pr_url is not None
                except RuntimeError as exc:
                    stderr = "\n".join(
                        part for part in [stderr, str(exc)] if part
                    )
            else:
                stderr = "\n".join(
                    part
                    for part in [stderr, push_result.stderr or push_result.stdout]
                    if part
                )
        elif github_slug is None:
            stderr = "\n".join(
                part
                for part in [
                    stderr,
                    "Pull request creation is only supported for GitHub repositories.",
                ]
                if part
            )
        else:
            stderr = "\n".join(
                part
                for part in [
                    stderr,
                    "Set PERSONAL_AGENT_PI_GITHUB_TOKEN (or GITHUB_TOKEN/GH_TOKEN) to push and open pull requests from the sandbox.",
                ]
                if part
            )

        return PiRepositoryTaskResult(
            available=pi_result.available,
            command=pi_result.command,
            exit_code=pi_result.exit_code,
            stdout=pi_result.stdout,
            stderr=stderr,
            duration_seconds=round(time.monotonic() - start, 2),
            sandbox_mode=sandbox_mode,
            workspace_id=workspace_id,
            workspace_dir=str(sandbox_dir),
            repo_dir=str(repo_dir),
            repo_url=request.repo_url,
            base_branch=base_branch,
            branch_name=branch_name,
            commit_sha=commit_sha,
            pr_url=pr_url,
            changes_detected=True,
            review_required=review_required,
            setup_commands=setup_commands,
            git_status=git_status,
        )

    async def approve_repository_push(
        self, request: PiRepositoryPushRequest
    ) -> PiRepositoryPushResult:
        if self._blaxel_enabled:
            return await self._approve_repository_push_in_blaxel(request)

        start = time.monotonic()
        sandbox_mode = "isolated_repo_clone"
        status = self.status()

        if not status["local_command_available"]:
            return PiRepositoryPushResult(
                available=False,
                command=[],
                exit_code=127,
                stdout="",
                stderr="Pi coding agent binary is not available.",
                duration_seconds=round(time.monotonic() - start, 2),
                sandbox_mode=sandbox_mode,
                workspace_id=request.workspace_id,
                workspace_dir=None,
                repo_dir=None,
                repo_url=None,
            )
        if not status["git_available"]:
            return PiRepositoryPushResult(
                available=True,
                command=[],
                exit_code=127,
                stdout="",
                stderr="git is required for repository workflows.",
                duration_seconds=round(time.monotonic() - start, 2),
                sandbox_mode=sandbox_mode,
                workspace_id=request.workspace_id,
                workspace_dir=None,
                repo_dir=None,
                repo_url=None,
            )

        workspace_dir = self._resolve_workspace_dir(request.workspace_id)
        if workspace_dir is None:
            return PiRepositoryPushResult(
                available=True,
                command=[],
                exit_code=400,
                stdout="",
                stderr="Invalid workspace_id.",
                duration_seconds=round(time.monotonic() - start, 2),
                sandbox_mode=sandbox_mode,
                workspace_id=request.workspace_id,
                workspace_dir=None,
                repo_dir=None,
                repo_url=None,
            )

        repo_dir = workspace_dir / "repo"
        if not repo_dir.exists():
            return PiRepositoryPushResult(
                available=True,
                command=[],
                exit_code=404,
                stdout="",
                stderr="Sandbox workspace was not found or has been cleaned up.",
                duration_seconds=round(time.monotonic() - start, 2),
                sandbox_mode=sandbox_mode,
                workspace_id=workspace_dir.name,
                workspace_dir=str(workspace_dir),
                repo_dir=str(repo_dir),
                repo_url=None,
            )

        env = self._build_sandbox_env(workspace_dir)
        remote_result = await self._run_command(
            ["git", "remote", "get-url", "origin"],
            cwd=str(repo_dir),
            env=env,
            timeout_seconds=30,
        )
        if remote_result.exit_code != 0:
            return PiRepositoryPushResult(
                available=True,
                command=self._sanitize_command(remote_result.command),
                exit_code=remote_result.exit_code,
                stdout=remote_result.stdout,
                stderr=remote_result.stderr,
                duration_seconds=round(time.monotonic() - start, 2),
                sandbox_mode=sandbox_mode,
                workspace_id=workspace_dir.name,
                workspace_dir=str(workspace_dir),
                repo_dir=str(repo_dir),
                repo_url=None,
            )
        repo_url = remote_result.stdout.strip() or None

        branch_result = await self._run_command(
            ["git", "branch", "--show-current"],
            cwd=str(repo_dir),
            env=env,
            timeout_seconds=30,
        )
        if branch_result.exit_code != 0:
            return PiRepositoryPushResult(
                available=True,
                command=self._sanitize_command(branch_result.command),
                exit_code=branch_result.exit_code,
                stdout=branch_result.stdout,
                stderr=branch_result.stderr,
                duration_seconds=round(time.monotonic() - start, 2),
                sandbox_mode=sandbox_mode,
                workspace_id=workspace_dir.name,
                workspace_dir=str(workspace_dir),
                repo_dir=str(repo_dir),
                repo_url=repo_url,
            )
        branch_name = branch_result.stdout.strip()
        if not branch_name:
            return PiRepositoryPushResult(
                available=True,
                command=self._sanitize_command(branch_result.command),
                exit_code=400,
                stdout=branch_result.stdout,
                stderr="No active branch found in workspace repository.",
                duration_seconds=round(time.monotonic() - start, 2),
                sandbox_mode=sandbox_mode,
                workspace_id=workspace_dir.name,
                workspace_dir=str(workspace_dir),
                repo_dir=str(repo_dir),
                repo_url=repo_url,
            )

        github_token = self.settings.pi_github_token_value
        if not github_token:
            return PiRepositoryPushResult(
                available=True,
                command=[],
                exit_code=400,
                stdout="",
                stderr=(
                    "Set PERSONAL_AGENT_PI_GITHUB_TOKEN "
                    "(or GITHUB_TOKEN/GH_TOKEN) before approving push."
                ),
                duration_seconds=round(time.monotonic() - start, 2),
                sandbox_mode=sandbox_mode,
                workspace_id=workspace_dir.name,
                workspace_dir=str(workspace_dir),
                repo_dir=str(repo_dir),
                repo_url=repo_url,
                branch_name=branch_name,
            )

        github_slug = self._github_repo_slug(repo_url or "")
        if github_slug is None:
            return PiRepositoryPushResult(
                available=True,
                command=[],
                exit_code=400,
                stdout="",
                stderr="Pull request creation is only supported for GitHub repositories.",
                duration_seconds=round(time.monotonic() - start, 2),
                sandbox_mode=sandbox_mode,
                workspace_id=workspace_dir.name,
                workspace_dir=str(workspace_dir),
                repo_dir=str(repo_dir),
                repo_url=repo_url,
                branch_name=branch_name,
            )

        authenticated_repo_url = self._clone_url(repo_url or "", github_slug=github_slug)
        if authenticated_repo_url and authenticated_repo_url != repo_url:
            set_remote_result = await self._run_command(
                ["git", "remote", "set-url", "origin", authenticated_repo_url],
                cwd=str(repo_dir),
                env=env,
                timeout_seconds=30,
            )
            if set_remote_result.exit_code != 0:
                return PiRepositoryPushResult(
                    available=True,
                    command=self._sanitize_command(set_remote_result.command),
                    exit_code=set_remote_result.exit_code,
                    stdout=set_remote_result.stdout,
                    stderr=set_remote_result.stderr,
                    duration_seconds=round(time.monotonic() - start, 2),
                    sandbox_mode=sandbox_mode,
                    workspace_id=workspace_dir.name,
                    workspace_dir=str(workspace_dir),
                    repo_dir=str(repo_dir),
                    repo_url=repo_url,
                    branch_name=branch_name,
                )

        push_command = ["git", "push", "-u", "origin", branch_name]
        push_result = await self._run_command(
            push_command,
            cwd=str(repo_dir),
            env=env,
            timeout_seconds=300,
        )
        if push_result.exit_code != 0:
            return PiRepositoryPushResult(
                available=True,
                command=self._sanitize_command(push_result.command),
                exit_code=push_result.exit_code,
                stdout=push_result.stdout,
                stderr=push_result.stderr,
                duration_seconds=round(time.monotonic() - start, 2),
                sandbox_mode=sandbox_mode,
                workspace_id=workspace_dir.name,
                workspace_dir=str(workspace_dir),
                repo_dir=str(repo_dir),
                repo_url=repo_url,
                branch_name=branch_name,
            )

        base_branch = (
            request.base_branch
            or await self._detect_remote_default_branch(repo_dir, env)
            or "main"
        )
        stderr = ""
        pr_url: str | None = None
        try:
            pr_url = await self._create_pull_request(
                github_slug=github_slug,
                github_token=github_token,
                title=request.pr_title or self._default_repo_push_pr_title(branch_name),
                body=request.pr_body
                or self._default_repo_push_pr_body(
                    requested_by=request.requested_by,
                    workspace_id=workspace_dir.name,
                ),
                head=branch_name,
                base=base_branch,
            )
        except RuntimeError as exc:
            stderr = str(exc)

        return PiRepositoryPushResult(
            available=True,
            command=self._sanitize_command(push_result.command),
            exit_code=push_result.exit_code,
            stdout=push_result.stdout,
            stderr=stderr,
            duration_seconds=round(time.monotonic() - start, 2),
            sandbox_mode=sandbox_mode,
            workspace_id=workspace_dir.name,
            workspace_dir=str(workspace_dir),
            repo_dir=str(repo_dir),
            repo_url=repo_url,
            branch_name=branch_name,
            base_branch=base_branch,
            pr_url=pr_url,
            review_required=pr_url is not None,
        )

    async def _run_task_in_orchestrator(self, request: PiTaskRequest) -> PiTaskResult:
        if self.sandbox_service is None:
            raise RuntimeError("Blaxel sandbox service is not configured.")

        start = time.monotonic()
        orchestrator = await self.sandbox_service.ensure_orchestrator_sandbox()
        workdir = self._normalize_sandbox_workdir(
            request.workdir,
            default_root=self.sandbox_service.orchestrator_workdir(),
        )
        sandbox_request = PiTaskRequest(
            prompt=request.prompt,
            workdir=workdir,
            files=[],
            tools=list(request.tools),
            provider=request.provider,
            model=request.model,
            thinking=request.thinking,
            append_system_prompt=request.append_system_prompt,
            timeout_seconds=request.timeout_seconds,
            session_id=request.session_id,
            structured_output=request.structured_output,
        )
        env, sandbox_files = await self._prepare_sandbox_task_runtime(
            sandbox_name=orchestrator.name,
            request=sandbox_request,
            workspace_root=self.sandbox_service.orchestrator_workdir(),
        )
        sandbox_request.files = sandbox_files
        session_path = self._sandbox_session_path(
            workspace_root=self.sandbox_service.orchestrator_workdir(),
            session_id=request.session_id,
        )
        command = self._build_command(
            sandbox_request,
            output_format="json" if request.structured_output else "text",
            session_path=str(session_path) if session_path is not None else None,
        )
        result = await self.sandbox_service.run_command(
            orchestrator.name,
            command=shlex.join(command),
            working_dir=workdir,
            env=env,
            timeout_seconds=request.timeout_seconds or self.settings.pi_timeout_seconds,
            name=f"pi-task-{int(time.time())}",
        )
        return self._build_pi_task_result(
            request=request,
            command=command,
            exit_code=result.exit_code,
            stdout=result.stdout or result.logs,
            stderr=result.stderr,
            logs=result.logs,
            duration_seconds=round(time.monotonic() - start, 2),
            available=True,
            sandbox_mode="blaxel_orchestrator",
            sandbox_name=orchestrator.name,
            sandbox_image=orchestrator.image,
        )

    async def _run_task_in_execution_sandbox(self, request: PiTaskRequest) -> PiTaskResult:
        if self.sandbox_service is None:
            raise RuntimeError("Blaxel sandbox service is not configured.")

        start = time.monotonic()
        execution_sandbox = await self.sandbox_service.create_execution_sandbox(
            request_key=request.prompt[:64],
        )
        workdir = self._normalize_sandbox_workdir(
            request.workdir,
            default_root=self.sandbox_service.execution_workdir(),
        )
        sandbox_request = PiTaskRequest(
            prompt=request.prompt,
            workdir=workdir,
            files=[],
            tools=list(request.tools),
            provider=request.provider,
            model=request.model,
            thinking=request.thinking,
            append_system_prompt=request.append_system_prompt,
            timeout_seconds=request.timeout_seconds,
            session_id=request.session_id,
            structured_output=request.structured_output,
        )
        session_path: PurePosixPath | None = None
        persisted_session_path: PurePosixPath | None = None
        orchestrator_name: str | None = None
        try:
            env, sandbox_files = await self._prepare_sandbox_task_runtime(
                sandbox_name=execution_sandbox.name,
                request=sandbox_request,
                workspace_root=self.sandbox_service.execution_workdir(),
            )
            sandbox_request.files = sandbox_files
            if request.session_id:
                orchestrator = await self.sandbox_service.ensure_orchestrator_sandbox()
                orchestrator_name = orchestrator.name
                persisted_session_path, session_path = await self._prepare_execution_session(
                    session_id=request.session_id,
                    source_sandbox=orchestrator.name,
                    target_sandbox=execution_sandbox.name,
                )
            command = self._build_command(
                sandbox_request,
                output_format="json" if request.structured_output else "text",
                session_path=str(session_path) if session_path is not None else None,
            )
            result = await self.sandbox_service.run_command(
                execution_sandbox.name,
                command=shlex.join(command),
                working_dir=workdir,
                env=env,
                timeout_seconds=request.timeout_seconds
                or self.settings.pi_timeout_seconds,
                name=f"pi-exec-{int(time.time())}",
            )
            if (
                request.session_id
                and session_path is not None
                and persisted_session_path is not None
                and orchestrator_name is not None
            ):
                await self._persist_execution_session(
                    source_sandbox=execution_sandbox.name,
                    source_path=session_path,
                    target_sandbox=orchestrator_name,
                    target_path=persisted_session_path,
                )
            return self._build_pi_task_result(
                request=request,
                command=command,
                exit_code=result.exit_code,
                stdout=result.stdout or result.logs,
                stderr=result.stderr,
                logs=result.logs,
                duration_seconds=round(time.monotonic() - start, 2),
                available=True,
                sandbox_mode="blaxel_execution_sandbox",
                sandbox_name=execution_sandbox.name,
                sandbox_image=execution_sandbox.image,
            )
        finally:
            await self.sandbox_service.delete_sandbox_if_exists(execution_sandbox.name)

    async def _prepare_sandbox_task_runtime(
        self,
        *,
        sandbox_name: str,
        request: PiTaskRequest,
        workspace_root: str,
    ) -> tuple[dict[str, str], list[str]]:
        if self.sandbox_service is None:
            raise RuntimeError("Blaxel sandbox service is not configured.")

        runtime_root = PurePosixPath(workspace_root) / ".personal-agent"
        home_dir = runtime_root / "home"
        config_dir = runtime_root / "config"
        cache_dir = runtime_root / "cache"
        tmp_dir = runtime_root / "tmp"
        sessions_dir = runtime_root / "sessions"
        attachments_dir = runtime_root / "attachments" / str(int(time.time() * 1000))
        workdir = PurePosixPath(request.workdir or self.sandbox_service.orchestrator_workdir())

        for path in (
            runtime_root,
            home_dir,
            config_dir,
            cache_dir,
            tmp_dir,
            sessions_dir,
            attachments_dir,
            workdir,
        ):
            await self._ensure_sandbox_directory(sandbox_name, path)

        env = {
            "HOME": str(home_dir),
            "TMPDIR": str(tmp_dir),
            "XDG_CONFIG_HOME": str(config_dir),
            "XDG_CACHE_HOME": str(cache_dir),
            "LANG": os.environ.get("LANG", "C.UTF-8"),
            "LC_ALL": os.environ.get("LC_ALL", "C.UTF-8"),
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_AUTHOR_NAME": self.settings.pi_git_author_name,
            "GIT_AUTHOR_EMAIL": self.settings.pi_git_author_email,
            "GIT_COMMITTER_NAME": self.settings.pi_git_author_name,
            "GIT_COMMITTER_EMAIL": self.settings.pi_git_author_email,
            "NPM_CONFIG_UPDATE_NOTIFIER": "false",
            "NPM_CONFIG_FUND": "false",
            "NPM_CONFIG_AUDIT": "false",
            "NPM_CONFIG_LOGLEVEL": "error",
            "CI": "true",
        }
        github_token = self.settings.pi_github_token_value
        if github_token:
            env["GITHUB_TOKEN"] = github_token
            env["GH_TOKEN"] = github_token

        provider = self._resolve_provider(request.provider)
        if provider == "nebius":
            await self._configure_nebius_provider_in_sandbox(
                sandbox_name=sandbox_name,
                home_dir=home_dir,
                env=env,
                model=self._resolve_model(request.model),
            )

        sandbox_files = await self._sync_request_files_to_sandbox(
            sandbox_name=sandbox_name,
            request_files=request.files,
            attachments_dir=attachments_dir,
        )
        return env, sandbox_files

    async def _sync_request_files_to_sandbox(
        self,
        *,
        sandbox_name: str,
        request_files: list[str],
        attachments_dir: PurePosixPath,
    ) -> list[str]:
        if self.sandbox_service is None or not request_files:
            return []

        sandbox_files: list[str] = []
        for index, file_path in enumerate(request_files):
            source_path = Path(file_path).expanduser()
            if not source_path.exists() or not source_path.is_file():
                raise FileNotFoundError(f"Attached file was not found: {source_path}")
            destination = attachments_dir / f"{index:02d}-{source_path.name}"
            await self._ensure_sandbox_directory(sandbox_name, destination.parent)
            await self.sandbox_service.write_binary_file(
                sandbox_name,
                str(destination),
                source_path.read_bytes(),
            )
            sandbox_files.append(str(destination))
        return sandbox_files

    async def _ensure_sandbox_directory(
        self,
        sandbox_name: str,
        path: PurePosixPath,
    ) -> None:
        if self.sandbox_service is None:
            raise RuntimeError("Blaxel sandbox service is not configured.")

        current = PurePosixPath("/")
        for part in path.parts:
            if part == "/":
                continue
            current = current / part
            try:
                await self.sandbox_service.mkdir(sandbox_name, str(current))
            except Exception:  # noqa: BLE001
                continue

    async def _configure_nebius_provider_in_sandbox(
        self,
        *,
        sandbox_name: str,
        home_dir: PurePosixPath,
        env: dict[str, str],
        model: str | None,
    ) -> None:
        if self.sandbox_service is None:
            raise RuntimeError("Blaxel sandbox service is not configured.")

        api_key = self.settings.pi_api_key_value
        if api_key:
            env["NEBIUS_API_KEY"] = api_key

        models_path = home_dir / ".pi" / "agent" / "models.json"
        await self._ensure_sandbox_directory(sandbox_name, models_path.parent)
        await self.sandbox_service.write_file(
            sandbox_name,
            str(models_path),
            json.dumps(self._nebius_provider_config(model), indent=2) + "\n",
        )

    async def _prepare_execution_session(
        self,
        *,
        session_id: str,
        source_sandbox: str,
        target_sandbox: str,
    ) -> tuple[PurePosixPath, PurePosixPath]:
        if self.sandbox_service is None:
            raise RuntimeError("Blaxel sandbox service is not configured.")
        source_path = self._sandbox_session_path(
            workspace_root=self.sandbox_service.orchestrator_workdir(),
            session_id=session_id,
        )
        target_path = self._sandbox_session_path(
            workspace_root=self.sandbox_service.execution_workdir(),
            session_id=session_id,
        )
        if source_path is None or target_path is None:
            raise RuntimeError("A session_id is required to prepare an execution session.")
        await self._ensure_sandbox_directory(target_sandbox, target_path.parent)
        await self._copy_sandbox_text_file_if_exists(
            source_sandbox=source_sandbox,
            source_path=source_path,
            target_sandbox=target_sandbox,
            target_path=target_path,
        )
        return source_path, target_path

    async def _persist_execution_session(
        self,
        *,
        source_sandbox: str,
        source_path: PurePosixPath,
        target_sandbox: str,
        target_path: PurePosixPath,
    ) -> None:
        await self._copy_sandbox_text_file_if_exists(
            source_sandbox=source_sandbox,
            source_path=source_path,
            target_sandbox=target_sandbox,
            target_path=target_path,
        )

    async def _copy_sandbox_text_file_if_exists(
        self,
        *,
        source_sandbox: str,
        source_path: PurePosixPath,
        target_sandbox: str,
        target_path: PurePosixPath,
    ) -> bool:
        if self.sandbox_service is None:
            raise RuntimeError("Blaxel sandbox service is not configured.")
        try:
            content = await self.sandbox_service.read_file(source_sandbox, str(source_path))
        except Exception:  # noqa: BLE001
            return False
        await self._ensure_sandbox_directory(target_sandbox, target_path.parent)
        await self.sandbox_service.write_file(target_sandbox, str(target_path), content)
        return True

    @staticmethod
    def _normalize_sandbox_workdir(
        requested_workdir: str | None,
        *,
        default_root: str,
    ) -> str:
        if not requested_workdir:
            return default_root
        if requested_workdir.startswith("/"):
            return requested_workdir
        return str(PurePosixPath(default_root) / requested_workdir)

    async def _run_repository_task_in_blaxel(
        self,
        request: PiRepositoryTaskRequest,
    ) -> PiRepositoryTaskResult:
        if self.sandbox_service is None:
            raise RuntimeError("Blaxel sandbox service is not configured.")

        start = time.monotonic()
        sandbox_mode = "per_repo_persistent_sandbox"
        setup_commands: list[list[str]] = []

        await self.sandbox_service.ensure_orchestrator_sandbox()
        repo_sandbox = await self.sandbox_service.ensure_repo_sandbox(request.repo_url)
        env = await self._prepare_repo_sandbox_env(
            repo_sandbox.name,
            provider=request.provider,
            model=request.model,
        )
        repo_root = PurePosixPath(self.sandbox_service.repo_workdir())
        source_repo_dir = repo_root / "source"
        workspaces_root = repo_root / "workspaces"
        await self._ensure_sandbox_directory(repo_sandbox.name, repo_root)
        await self._ensure_sandbox_directory(repo_sandbox.name, workspaces_root)

        github_slug = self._github_repo_slug(request.repo_url)
        clone_url = self._clone_url(request.repo_url, github_slug=github_slug)
        if not await self._sandbox_repo_exists(repo_sandbox.name, source_repo_dir, env):
            clone_command = ["git", "clone", "--depth", "1", clone_url, str(source_repo_dir)]
            setup_commands.append(self._sanitize_command(clone_command))
            clone_result = await self._run_sandbox_command(
                repo_sandbox.name,
                clone_command,
                cwd=str(repo_root),
                env=env,
                timeout_seconds=300,
            )
            if clone_result.exit_code != 0:
                return PiRepositoryTaskResult(
                    available=True,
                    command=self._sanitize_command(clone_result.command),
                    exit_code=clone_result.exit_code,
                    stdout=clone_result.stdout,
                    stderr=clone_result.stderr,
                    duration_seconds=round(time.monotonic() - start, 2),
                    sandbox_mode=sandbox_mode,
                    workspace_dir=str(repo_root),
                    repo_dir=str(source_repo_dir),
                    repo_url=request.repo_url,
                    workspace_id=repo_sandbox.name,
                    setup_commands=setup_commands,
                )
        elif clone_url != request.repo_url:
            set_remote_command = ["git", "remote", "set-url", "origin", clone_url]
            setup_commands.append(self._sanitize_command(set_remote_command))
            await self._run_sandbox_command(
                repo_sandbox.name,
                set_remote_command,
                cwd=str(source_repo_dir),
                env=env,
                timeout_seconds=30,
            )

        fetch_command = ["git", "fetch", "origin", "--prune"]
        if request.base_branch:
            fetch_command = ["git", "fetch", "origin", request.base_branch, "--prune"]
        setup_commands.append(fetch_command)
        fetch_result = await self._run_sandbox_command(
            repo_sandbox.name,
            fetch_command,
            cwd=str(source_repo_dir),
            env=env,
            timeout_seconds=180,
        )
        if fetch_result.exit_code != 0:
            return PiRepositoryTaskResult(
                available=True,
                command=self._sanitize_command(fetch_result.command),
                exit_code=fetch_result.exit_code,
                stdout=fetch_result.stdout,
                stderr=fetch_result.stderr,
                duration_seconds=round(time.monotonic() - start, 2),
                sandbox_mode=sandbox_mode,
                workspace_dir=str(repo_root),
                repo_dir=str(source_repo_dir),
                repo_url=request.repo_url,
                workspace_id=repo_sandbox.name,
                base_branch=request.base_branch,
                setup_commands=setup_commands,
            )

        base_branch = request.base_branch or await self._detect_remote_default_branch_in_sandbox(
            repo_sandbox.name,
            source_repo_dir,
            env,
        )
        if base_branch is None:
            base_branch = await self._detect_current_branch_in_sandbox(
                repo_sandbox.name,
                source_repo_dir,
                env,
            )
        base_branch = base_branch or "main"

        branch_name = request.branch_name or self._default_branch_name(request.prompt)
        workspace_name = self._default_workspace_name(request.prompt)
        workspace_id = self._compose_workspace_id(repo_sandbox.name, workspace_name)
        workspace_dir = workspaces_root / workspace_name
        repo_dir = workspace_dir / "repo"
        worktree_command = [
            "git",
            "worktree",
            "add",
            "-B",
            branch_name,
            str(repo_dir),
            f"origin/{base_branch}",
        ]
        setup_commands.append(worktree_command)
        worktree_result = await self._run_sandbox_command(
            repo_sandbox.name,
            worktree_command,
            cwd=str(source_repo_dir),
            env=env,
            timeout_seconds=180,
        )
        if worktree_result.exit_code != 0:
            return PiRepositoryTaskResult(
                available=True,
                command=self._sanitize_command(worktree_result.command),
                exit_code=worktree_result.exit_code,
                stdout=worktree_result.stdout,
                stderr=worktree_result.stderr,
                duration_seconds=round(time.monotonic() - start, 2),
                sandbox_mode=sandbox_mode,
                workspace_dir=str(workspace_dir),
                repo_dir=str(repo_dir),
                repo_url=request.repo_url,
                workspace_id=workspace_id,
                base_branch=base_branch,
                branch_name=branch_name,
                setup_commands=setup_commands,
            )

        pi_request = PiTaskRequest(
            prompt=request.prompt,
            workdir=str(repo_dir),
            tools=list(request.tools),
            provider=request.provider,
            model=request.model,
            thinking=request.thinking,
            append_system_prompt=self._build_repo_system_prompt(
                repo_dir=Path(str(repo_dir)),
                extra_prompt=request.append_system_prompt,
            ),
            timeout_seconds=request.timeout_seconds,
        )
        command = self._build_command(pi_request)
        pi_result = await self._run_sandbox_command(
            repo_sandbox.name,
            command,
            cwd=str(repo_dir),
            env=env,
            timeout_seconds=request.timeout_seconds or self.settings.pi_timeout_seconds,
        )
        if pi_result.exit_code != 0:
            return PiRepositoryTaskResult(
                available=True,
                command=self._sanitize_command(pi_result.command),
                exit_code=pi_result.exit_code,
                stdout=pi_result.stdout,
                stderr=pi_result.stderr,
                duration_seconds=round(time.monotonic() - start, 2),
                sandbox_mode=sandbox_mode,
                workspace_dir=str(workspace_dir),
                repo_dir=str(repo_dir),
                repo_url=request.repo_url,
                workspace_id=workspace_id,
                base_branch=base_branch,
                branch_name=branch_name,
                setup_commands=setup_commands,
            )

        git_status_result = await self._run_sandbox_command(
            repo_sandbox.name,
            ["git", "status", "--short"],
            cwd=str(repo_dir),
            env=env,
            timeout_seconds=30,
        )
        git_status = git_status_result.stdout.strip()
        changes_detected = bool(git_status)
        if not changes_detected:
            return PiRepositoryTaskResult(
                available=True,
                command=self._sanitize_command(pi_result.command),
                exit_code=pi_result.exit_code,
                stdout=pi_result.stdout,
                stderr=pi_result.stderr,
                duration_seconds=round(time.monotonic() - start, 2),
                sandbox_mode=sandbox_mode,
                workspace_dir=str(workspace_dir),
                repo_dir=str(repo_dir),
                repo_url=request.repo_url,
                workspace_id=workspace_id,
                base_branch=base_branch,
                branch_name=branch_name,
                changes_detected=False,
                review_required=False,
                setup_commands=setup_commands,
                git_status=git_status,
            )

        for config_command in (
            ["git", "config", "user.name", self.settings.pi_git_author_name],
            ["git", "config", "user.email", self.settings.pi_git_author_email],
        ):
            setup_commands.append(config_command)
            config_result = await self._run_sandbox_command(
                repo_sandbox.name,
                config_command,
                cwd=str(repo_dir),
                env=env,
                timeout_seconds=30,
            )
            if config_result.exit_code != 0:
                return PiRepositoryTaskResult(
                    available=True,
                    command=self._sanitize_command(config_result.command),
                    exit_code=config_result.exit_code,
                    stdout=config_result.stdout,
                    stderr=config_result.stderr,
                    duration_seconds=round(time.monotonic() - start, 2),
                    sandbox_mode=sandbox_mode,
                    workspace_dir=str(workspace_dir),
                    repo_dir=str(repo_dir),
                    repo_url=request.repo_url,
                    workspace_id=workspace_id,
                    base_branch=base_branch,
                    branch_name=branch_name,
                    changes_detected=True,
                    review_required=False,
                    setup_commands=setup_commands,
                    git_status=git_status,
                )

        add_result = await self._run_sandbox_command(
            repo_sandbox.name,
            ["git", "add", "-A"],
            cwd=str(repo_dir),
            env=env,
            timeout_seconds=60,
        )
        if add_result.exit_code != 0:
            return PiRepositoryTaskResult(
                available=True,
                command=self._sanitize_command(add_result.command),
                exit_code=add_result.exit_code,
                stdout=add_result.stdout,
                stderr=add_result.stderr,
                duration_seconds=round(time.monotonic() - start, 2),
                sandbox_mode=sandbox_mode,
                workspace_dir=str(workspace_dir),
                repo_dir=str(repo_dir),
                repo_url=request.repo_url,
                workspace_id=workspace_id,
                base_branch=base_branch,
                branch_name=branch_name,
                changes_detected=True,
                review_required=False,
                setup_commands=setup_commands,
                git_status=git_status,
            )

        commit_message = self._default_commit_message(request)
        commit_result = await self._run_sandbox_command(
            repo_sandbox.name,
            ["git", "commit", "-m", commit_message],
            cwd=str(repo_dir),
            env=env,
            timeout_seconds=120,
        )
        if commit_result.exit_code != 0:
            return PiRepositoryTaskResult(
                available=True,
                command=self._sanitize_command(commit_result.command),
                exit_code=commit_result.exit_code,
                stdout=commit_result.stdout,
                stderr=commit_result.stderr,
                duration_seconds=round(time.monotonic() - start, 2),
                sandbox_mode=sandbox_mode,
                workspace_dir=str(workspace_dir),
                repo_dir=str(repo_dir),
                repo_url=request.repo_url,
                workspace_id=workspace_id,
                base_branch=base_branch,
                branch_name=branch_name,
                changes_detected=True,
                review_required=False,
                setup_commands=setup_commands,
                git_status=git_status,
            )

        commit_sha_result = await self._run_sandbox_command(
            repo_sandbox.name,
            ["git", "rev-parse", "HEAD"],
            cwd=str(repo_dir),
            env=env,
            timeout_seconds=30,
        )
        commit_sha = commit_sha_result.stdout.strip() or None

        if not request.allow_push:
            pending_message = (
                "Push is pending approval. Approve with the workspace_id "
                "using the dedicated repo push endpoint or Discord command."
            )
            stderr = "\n".join(
                part for part in [pi_result.stderr, pending_message] if part
            )
            return PiRepositoryTaskResult(
                available=True,
                command=self._sanitize_command(pi_result.command),
                exit_code=pi_result.exit_code,
                stdout=pi_result.stdout,
                stderr=stderr,
                duration_seconds=round(time.monotonic() - start, 2),
                sandbox_mode=sandbox_mode,
                sandbox_name=repo_sandbox.name,
                sandbox_image=repo_sandbox.image,
                workspace_dir=str(workspace_dir),
                repo_dir=str(repo_dir),
                repo_url=request.repo_url,
                workspace_id=workspace_id,
                base_branch=base_branch,
                branch_name=branch_name,
                commit_sha=commit_sha,
                changes_detected=True,
                review_required=False,
                push_pending=True,
                setup_commands=setup_commands,
                git_status=git_status,
            )

        push_result = await self._approve_repository_push_in_blaxel(
            PiRepositoryPushRequest(
                workspace_id=workspace_id,
                pr_title=request.pr_title,
                pr_body=request.pr_body,
                base_branch=base_branch,
                requested_by=request.requested_by,
            )
        )
        stderr = "\n".join(part for part in [pi_result.stderr, push_result.stderr] if part)
        return PiRepositoryTaskResult(
            available=True,
            command=self._sanitize_command(pi_result.command),
            exit_code=push_result.exit_code,
            stdout=pi_result.stdout,
            stderr=stderr,
            duration_seconds=round(time.monotonic() - start, 2),
            sandbox_mode=sandbox_mode,
            sandbox_name=repo_sandbox.name,
            sandbox_image=repo_sandbox.image,
            workspace_dir=str(workspace_dir),
            repo_dir=str(repo_dir),
            repo_url=request.repo_url,
            workspace_id=workspace_id,
            base_branch=push_result.base_branch or base_branch,
            branch_name=push_result.branch_name or branch_name,
            commit_sha=commit_sha,
            pr_url=push_result.pr_url,
            changes_detected=True,
            review_required=push_result.review_required,
            push_pending=False,
            setup_commands=setup_commands,
            git_status=git_status,
        )

    async def _approve_repository_push_in_blaxel(
        self,
        request: PiRepositoryPushRequest,
    ) -> PiRepositoryPushResult:
        if self.sandbox_service is None:
            raise RuntimeError("Blaxel sandbox service is not configured.")

        start = time.monotonic()
        sandbox_mode = "per_repo_persistent_sandbox"
        parsed_workspace = self._parse_workspace_id(request.workspace_id)
        if parsed_workspace is None:
            return PiRepositoryPushResult(
                available=True,
                command=[],
                exit_code=400,
                stdout="",
                stderr="Invalid workspace_id.",
                duration_seconds=round(time.monotonic() - start, 2),
                sandbox_mode=sandbox_mode,
                workspace_id=request.workspace_id,
                workspace_dir=None,
                repo_dir=None,
                repo_url=None,
            )

        sandbox_name, workspace_name = parsed_workspace
        repo_root = PurePosixPath(self.sandbox_service.repo_workdir())
        workspace_dir = repo_root / "workspaces" / workspace_name
        repo_dir = workspace_dir / "repo"
        env = await self._prepare_repo_sandbox_env(sandbox_name)
        if not await self._sandbox_repo_exists(sandbox_name, repo_dir, env):
            return PiRepositoryPushResult(
                available=True,
                command=[],
                exit_code=404,
                stdout="",
                stderr="Sandbox workspace was not found or has been cleaned up.",
                duration_seconds=round(time.monotonic() - start, 2),
                sandbox_mode=sandbox_mode,
                workspace_id=request.workspace_id,
                workspace_dir=str(workspace_dir),
                repo_dir=str(repo_dir),
                repo_url=None,
            )

        remote_result = await self._run_sandbox_command(
            sandbox_name,
            ["git", "remote", "get-url", "origin"],
            cwd=str(repo_dir),
            env=env,
            timeout_seconds=30,
        )
        if remote_result.exit_code != 0:
            return PiRepositoryPushResult(
                available=True,
                command=self._sanitize_command(remote_result.command),
                exit_code=remote_result.exit_code,
                stdout=remote_result.stdout,
                stderr=remote_result.stderr,
                duration_seconds=round(time.monotonic() - start, 2),
                sandbox_mode=sandbox_mode,
                workspace_id=request.workspace_id,
                workspace_dir=str(workspace_dir),
                repo_dir=str(repo_dir),
                repo_url=None,
            )
        repo_url = remote_result.stdout.strip() or None

        branch_result = await self._run_sandbox_command(
            sandbox_name,
            ["git", "branch", "--show-current"],
            cwd=str(repo_dir),
            env=env,
            timeout_seconds=30,
        )
        if branch_result.exit_code != 0:
            return PiRepositoryPushResult(
                available=True,
                command=self._sanitize_command(branch_result.command),
                exit_code=branch_result.exit_code,
                stdout=branch_result.stdout,
                stderr=branch_result.stderr,
                duration_seconds=round(time.monotonic() - start, 2),
                sandbox_mode=sandbox_mode,
                workspace_id=request.workspace_id,
                workspace_dir=str(workspace_dir),
                repo_dir=str(repo_dir),
                repo_url=repo_url,
            )
        branch_name = branch_result.stdout.strip()
        if not branch_name:
            return PiRepositoryPushResult(
                available=True,
                command=self._sanitize_command(branch_result.command),
                exit_code=400,
                stdout=branch_result.stdout,
                stderr="No active branch found in workspace repository.",
                duration_seconds=round(time.monotonic() - start, 2),
                sandbox_mode=sandbox_mode,
                workspace_id=request.workspace_id,
                workspace_dir=str(workspace_dir),
                repo_dir=str(repo_dir),
                repo_url=repo_url,
                branch_name=None,
            )

        github_token = self.settings.pi_github_token_value
        if not github_token:
            return PiRepositoryPushResult(
                available=True,
                command=[],
                exit_code=400,
                stdout="",
                stderr=(
                    "Set PERSONAL_AGENT_PI_GITHUB_TOKEN "
                    "(or GITHUB_TOKEN/GH_TOKEN) before approving push."
                ),
                duration_seconds=round(time.monotonic() - start, 2),
                sandbox_mode=sandbox_mode,
                workspace_id=request.workspace_id,
                workspace_dir=str(workspace_dir),
                repo_dir=str(repo_dir),
                repo_url=repo_url,
                branch_name=branch_name,
            )

        github_slug = self._github_repo_slug(repo_url or "")
        if github_slug is None:
            return PiRepositoryPushResult(
                available=True,
                command=[],
                exit_code=400,
                stdout="",
                stderr="Pull request creation is only supported for GitHub repositories.",
                duration_seconds=round(time.monotonic() - start, 2),
                sandbox_mode=sandbox_mode,
                workspace_id=request.workspace_id,
                workspace_dir=str(workspace_dir),
                repo_dir=str(repo_dir),
                repo_url=repo_url,
                branch_name=branch_name,
            )

        authenticated_repo_url = self._clone_url(repo_url or "", github_slug=github_slug)
        if authenticated_repo_url and authenticated_repo_url != repo_url:
            set_remote_result = await self._run_sandbox_command(
                sandbox_name,
                ["git", "remote", "set-url", "origin", authenticated_repo_url],
                cwd=str(repo_dir),
                env=env,
                timeout_seconds=30,
            )
            if set_remote_result.exit_code != 0:
                return PiRepositoryPushResult(
                    available=True,
                    command=self._sanitize_command(set_remote_result.command),
                    exit_code=set_remote_result.exit_code,
                    stdout=set_remote_result.stdout,
                    stderr=set_remote_result.stderr,
                    duration_seconds=round(time.monotonic() - start, 2),
                    sandbox_mode=sandbox_mode,
                    workspace_id=request.workspace_id,
                    workspace_dir=str(workspace_dir),
                    repo_dir=str(repo_dir),
                    repo_url=repo_url,
                    branch_name=branch_name,
                )

        push_command = ["git", "push", "-u", "origin", branch_name]
        push_result = await self._run_sandbox_command(
            sandbox_name,
            push_command,
            cwd=str(repo_dir),
            env=env,
            timeout_seconds=300,
        )
        if push_result.exit_code != 0:
            return PiRepositoryPushResult(
                available=True,
                command=self._sanitize_command(push_result.command),
                exit_code=push_result.exit_code,
                stdout=push_result.stdout,
                stderr=push_result.stderr,
                duration_seconds=round(time.monotonic() - start, 2),
                sandbox_mode=sandbox_mode,
                workspace_id=request.workspace_id,
                workspace_dir=str(workspace_dir),
                repo_dir=str(repo_dir),
                repo_url=repo_url,
                branch_name=branch_name,
            )

        base_branch = request.base_branch or await self._detect_remote_default_branch_in_sandbox(
            sandbox_name,
            repo_dir,
            env,
        )
        base_branch = base_branch or "main"
        stderr = ""
        pr_url: str | None = None
        try:
            pr_url = await self._create_pull_request(
                github_slug=github_slug,
                github_token=github_token,
                title=request.pr_title or self._default_repo_push_pr_title(branch_name),
                body=request.pr_body
                or self._default_repo_push_pr_body(
                    requested_by=request.requested_by,
                    workspace_id=request.workspace_id,
                ),
                head=branch_name,
                base=base_branch,
            )
        except RuntimeError as exc:
            stderr = str(exc)

        return PiRepositoryPushResult(
            available=True,
            command=self._sanitize_command(push_result.command),
            exit_code=push_result.exit_code,
            stdout=push_result.stdout,
            stderr=stderr,
            duration_seconds=round(time.monotonic() - start, 2),
            sandbox_mode=sandbox_mode,
            sandbox_name=sandbox_name,
            sandbox_image=self.settings.blaxel_repo_sandbox_image,
            workspace_id=request.workspace_id,
            workspace_dir=str(workspace_dir),
            repo_dir=str(repo_dir),
            repo_url=repo_url,
            branch_name=branch_name,
            base_branch=base_branch,
            pr_url=pr_url,
            review_required=pr_url is not None,
        )

    async def _prepare_repo_sandbox_env(
        self,
        sandbox_name: str,
        *,
        provider: str | None = None,
        model: str | None = None,
    ) -> dict[str, str]:
        env, _ = await self._prepare_sandbox_task_runtime(
            sandbox_name=sandbox_name,
            request=PiTaskRequest(
                prompt="repo-runtime",
                workdir=self.sandbox_service.repo_workdir() if self.sandbox_service else None,
                provider=provider,
                model=model,
            ),
            workspace_root=self.sandbox_service.repo_workdir() if self.sandbox_service else "/workspace",
        )
        return env

    async def _run_sandbox_command(
        self,
        sandbox_name: str,
        command: list[str],
        *,
        cwd: str,
        env: dict[str, str],
        timeout_seconds: int,
    ) -> _CommandResult:
        if self.sandbox_service is None:
            raise RuntimeError("Blaxel sandbox service is not configured.")

        result = await self.sandbox_service.run_command(
            sandbox_name,
            command=shlex.join(command),
            working_dir=cwd,
            env=env,
            timeout_seconds=timeout_seconds,
            name=f"cmd-{int(time.time())}",
        )
        return _CommandResult(
            command=command,
            exit_code=result.exit_code,
            stdout=result.stdout or result.logs,
            stderr=self._merged_error_output(
                stdout=result.stdout,
                stderr=result.stderr,
                logs=result.logs,
            ),
        )

    async def _sandbox_repo_exists(
        self,
        sandbox_name: str,
        repo_dir: PurePosixPath,
        env: dict[str, str],
    ) -> bool:
        result = await self._run_sandbox_command(
            sandbox_name,
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=str(repo_dir),
            env=env,
            timeout_seconds=15,
        )
        return result.exit_code == 0

    async def _detect_current_branch_in_sandbox(
        self,
        sandbox_name: str,
        repo_dir: PurePosixPath,
        env: dict[str, str],
    ) -> str | None:
        result = await self._run_sandbox_command(
            sandbox_name,
            ["git", "branch", "--show-current"],
            cwd=str(repo_dir),
            env=env,
            timeout_seconds=30,
        )
        branch = result.stdout.strip()
        return branch or None

    async def _detect_remote_default_branch_in_sandbox(
        self,
        sandbox_name: str,
        repo_dir: PurePosixPath,
        env: dict[str, str],
    ) -> str | None:
        result = await self._run_sandbox_command(
            sandbox_name,
            ["git", "symbolic-ref", "refs/remotes/origin/HEAD"],
            cwd=str(repo_dir),
            env=env,
            timeout_seconds=30,
        )
        if result.exit_code != 0:
            return None
        ref = result.stdout.strip()
        prefix = "refs/remotes/origin/"
        if not ref.startswith(prefix):
            return None
        branch = ref[len(prefix) :].strip()
        return branch or None

    @staticmethod
    def _default_workspace_name(prompt: str) -> str:
        timestamp = time.strftime("%Y%m%d%H%M%S", time.gmtime())
        slug = re.sub(r"[^a-z0-9]+", "-", prompt.lower()).strip("-")
        short_slug = slug[:18] or "workspace"
        return f"ws-{timestamp}-{short_slug}"

    @staticmethod
    def _compose_workspace_id(sandbox_name: str, workspace_name: str) -> str:
        return f"{sandbox_name}__{workspace_name}"

    @staticmethod
    def _parse_workspace_id(workspace_id: str) -> tuple[str, str] | None:
        if "__" not in workspace_id:
            return None
        sandbox_name, workspace_name = workspace_id.split("__", 1)
        if not sandbox_name or not workspace_name:
            return None
        if not re.fullmatch(r"[a-z0-9-]+", sandbox_name):
            return None
        if not re.fullmatch(r"[a-z0-9-]+", workspace_name):
            return None
        return sandbox_name, workspace_name

    async def _run_task_with_env(
        self,
        request: PiTaskRequest,
        *,
        env: dict[str, str],
    ) -> PiTaskResult:
        session_path = self._local_session_path(request.session_id)
        command = self._build_command(
            request,
            output_format="json" if request.structured_output else "text",
            session_path=str(session_path) if session_path is not None else None,
        )
        start = time.monotonic()
        prepared_env, cleanup_dir = self._prepare_pi_command_env(
            request=request,
            base_env=env,
        )
        try:
            result = await self._run_command(
                command,
                cwd=request.workdir or str(Path.cwd()),
                env=prepared_env,
                timeout_seconds=request.timeout_seconds
                or self.settings.pi_timeout_seconds,
            )
        finally:
            if cleanup_dir is not None:
                shutil.rmtree(cleanup_dir, ignore_errors=True)
        return self._build_pi_task_result(
            request=request,
            command=result.command,
            exit_code=result.exit_code,
            stdout=result.stdout,
            stderr=result.stderr,
            logs="",
            duration_seconds=round(time.monotonic() - start, 2),
            available=bool(self.status()["available"]),
            sandbox_mode="local_subprocess",
        )

    def _build_command(
        self,
        request: PiTaskRequest,
        *,
        output_format: str = "text",
        session_path: str | None = None,
    ) -> list[str]:
        command = shlex.split(self.settings.pi_command)
        if output_format == "json":
            command.extend(["--mode", "json"])
        command.append("-p")

        if session_path:
            command.extend(["--session", session_path])
        elif self.settings.pi_no_session:
            command.append("--no-session")

        tools = request.tools or list(self.settings.pi_default_tools)
        if tools:
            command.extend(["--tools", ",".join(tools)])

        provider = self._resolve_provider(request.provider)
        if provider:
            command.extend(["--provider", provider])

        model = self._resolve_model(request.model)
        if model:
            command.extend(["--model", model])

        api_key = self.settings.pi_api_key_value
        if api_key and provider != "nebius":
            command.extend(["--api-key", api_key])

        thinking = request.thinking or self.settings.pi_default_thinking
        if thinking:
            command.extend(["--thinking", thinking])

        system_prompt = self._compose_system_prompt(request.append_system_prompt)
        if system_prompt:
            command.extend(["--append-system-prompt", system_prompt])

        for file_path in request.files:
            command.append(f"@{file_path}")

        command.append(request.prompt)
        return command

    def _local_session_path(self, session_id: str | None) -> Path | None:
        if not session_id:
            return None
        session_path = self.settings.pi_workspace_root_path / "sessions" / self._session_filename(
            session_id
        )
        session_path.parent.mkdir(parents=True, exist_ok=True)
        return session_path

    @staticmethod
    def _sandbox_session_path(
        *, workspace_root: str, session_id: str | None
    ) -> PurePosixPath | None:
        if not session_id:
            return None
        return (
            PurePosixPath(workspace_root)
            / ".personal-agent"
            / "sessions"
            / PiCodingAgentService._session_filename(session_id)
        )

    @staticmethod
    def _session_filename(session_id: str) -> str:
        cleaned = re.sub(r"[^a-z0-9-]+", "-", session_id.lower()).strip("-")
        if not cleaned:
            cleaned = hashlib.sha1(session_id.encode("utf-8")).hexdigest()[:16]
        return f"{cleaned[:96]}.jsonl"

    def _build_pi_task_result(
        self,
        *,
        request: PiTaskRequest,
        command: list[str],
        exit_code: int,
        stdout: str,
        stderr: str,
        logs: str,
        duration_seconds: float,
        available: bool,
        sandbox_mode: str | None = None,
        sandbox_name: str | None = None,
        sandbox_image: str | None = None,
    ) -> PiTaskResult:
        assistant_response = ""
        tool_traces: list[PiToolExecution] = []
        primary_output = stdout or logs
        if request.structured_output:
            parsed = self._parse_pi_json_output(stdout or logs)
            if parsed is not None:
                assistant_response = self._strip_raw_llm_tokens(parsed.assistant_response)
                tool_traces = parsed.tool_traces
                primary_output = self._strip_raw_llm_tokens(
                    parsed.primary_output or primary_output
                )
            else:
                primary_output = self._strip_raw_llm_tokens(primary_output)
        error_output = stderr
        if logs:
            error_output = self._merged_error_output(
                stdout=stdout,
                stderr=stderr,
                logs=logs,
            )
        return PiTaskResult(
            available=available,
            command=self._sanitize_command(command),
            exit_code=exit_code,
            stdout=primary_output,
            stderr=error_output,
            duration_seconds=duration_seconds,
            session_id=request.session_id,
            assistant_response=assistant_response,
            tool_traces=tool_traces,
            sandbox_mode=sandbox_mode,
            sandbox_name=sandbox_name,
            sandbox_image=sandbox_image,
        )

    def _parse_pi_json_output(self, output: str) -> _StructuredPiOutput | None:
        events: list[dict[str, object]] = []
        for raw_line in output.splitlines():
            line = raw_line.strip()
            if not line.startswith("{"):
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict):
                events.append(event)
        if not events:
            return None

        traces_by_id: dict[str, PiToolExecution] = {}
        ordered_trace_ids: list[str] = []
        agent_messages: list[dict[str, object]] = []

        for event in events:
            event_type = event.get("type")
            if event_type == "agent_end":
                raw_messages = event.get("messages")
                if isinstance(raw_messages, list):
                    agent_messages = [
                        message for message in raw_messages if isinstance(message, dict)
                    ]
                continue

            if not isinstance(event_type, str) or not event_type.startswith("tool_execution_"):
                continue

            tool_call_id = str(event.get("toolCallId", "") or "") or None
            trace_key = tool_call_id or f"tool-{len(ordered_trace_ids)}"
            trace = traces_by_id.get(trace_key)
            if trace is None:
                trace = PiToolExecution(
                    tool_name=str(event.get("toolName", "unknown") or "unknown"),
                    tool_call_id=tool_call_id,
                )
                traces_by_id[trace_key] = trace
                ordered_trace_ids.append(trace_key)

            raw_args = event.get("args")
            if isinstance(raw_args, dict):
                trace.arguments = dict(raw_args)

            if event_type == "tool_execution_update":
                partial_result = event.get("partialResult")
                if isinstance(partial_result, dict):
                    trace.output = (
                        self._extract_pi_text_from_content(partial_result.get("content"))
                        or trace.output
                    )
            if event_type == "tool_execution_end":
                raw_result = event.get("result")
                if isinstance(raw_result, dict):
                    trace.output = (
                        self._extract_pi_text_from_content(raw_result.get("content"))
                        or trace.output
                    )
                trace.is_error = bool(event.get("isError"))

        assistant_messages: list[str] = []
        tool_outputs: list[str] = []
        for message in agent_messages:
            role = message.get("role")
            content_text = self._extract_pi_text_from_content(message.get("content"))
            if role == "assistant" and content_text:
                assistant_messages.append(content_text)
            if role == "toolResult" and content_text:
                tool_outputs.append(content_text)

        assistant_response = assistant_messages[-1] if assistant_messages else ""
        fallback_output = tool_outputs[-1] if tool_outputs else ""
        if not fallback_output:
            fallback_output = next(
                (
                    trace.output
                    for trace in reversed([traces_by_id[key] for key in ordered_trace_ids])
                    if trace.output
                ),
                "",
            )
        return _StructuredPiOutput(
            assistant_response=assistant_response,
            primary_output=assistant_response or fallback_output,
            tool_traces=[traces_by_id[key] for key in ordered_trace_ids],
        )

    _RAW_LLM_TOKEN_RE = re.compile(
        r"<\|(?:tool_calls?_section_begin|tool_calls?_section_end|tool_call_begin|"
        r"tool_call_end|tool_call_argument_begin|tool_call_argument_end|"
        r"tool_result_begin|tool_result_end|im_start|im_end|endoftext)[^|]*\|>"
    )

    @staticmethod
    def _strip_raw_llm_tokens(text: str) -> str:
        if not text:
            return text
        cleaned = PiCodingAgentService._RAW_LLM_TOKEN_RE.sub("", text)
        cleaned = re.sub(r"call_\d+\s*", "", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        return cleaned

    @staticmethod
    def _extract_pi_text_from_content(content: object) -> str:
        if not isinstance(content, list):
            return ""
        parts: list[str] = []
        for item in content:
            if not isinstance(item, dict):
                continue
            if item.get("type") != "text":
                continue
            text = item.get("text")
            if isinstance(text, str) and text.strip():
                parts.append(text.strip())
        return "\n".join(parts).strip()

    def _resolve_provider(self, request_provider: str | None) -> str | None:
        return request_provider or self.settings.pi_provider_value

    def _resolve_model(self, request_model: str | None) -> str | None:
        return request_model or self.settings.pi_model_value

    def _prepare_pi_command_env(
        self,
        *,
        request: PiTaskRequest,
        base_env: dict[str, str] | None,
    ) -> tuple[dict[str, str] | None, Path | None]:
        provider = self._resolve_provider(request.provider)
        if provider != "nebius":
            return base_env, None

        env = dict(base_env) if base_env is not None else None
        cleanup_dir: Path | None = None
        if env is None:
            workspace_root = self.settings.pi_workspace_root_path
            workspace_root.mkdir(parents=True, exist_ok=True)
            cleanup_dir = Path(tempfile.mkdtemp(prefix="pi-runtime-", dir=workspace_root))
            env = self._build_pi_runtime_env(cleanup_dir)

        home_dir = Path(env["HOME"])
        self._configure_nebius_provider(
            home_dir=home_dir,
            env=env,
            model=self._resolve_model(request.model),
        )
        return env, cleanup_dir

    async def _run_command(
        self,
        command: list[str],
        *,
        cwd: str,
        env: dict[str, str] | None = None,
        timeout_seconds: int,
    ) -> _CommandResult:
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                cwd=cwd,
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            return _CommandResult(
                command=command,
                exit_code=127,
                stdout="",
                stderr=str(exc),
            )

        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=timeout_seconds,
            )
        except TimeoutError:
            process.kill()
            await process.wait()
            return _CommandResult(
                command=command,
                exit_code=124,
                stdout="",
                stderr="Pi task timed out.",
            )

        return _CommandResult(
            command=command,
            exit_code=process.returncode or 0,
            stdout=stdout.decode("utf-8", errors="replace"),
            stderr=stderr.decode("utf-8", errors="replace"),
        )

    def _build_sandbox_env(self, sandbox_dir: Path) -> dict[str, str]:
        home_dir = sandbox_dir / "home"
        config_dir = sandbox_dir / "config"
        cache_dir = sandbox_dir / "cache"
        tmp_dir = sandbox_dir / "tmp"
        for path in (home_dir, config_dir, cache_dir, tmp_dir):
            path.mkdir(parents=True, exist_ok=True)

        env = {
            "PATH": os.environ.get("PATH", ""),
            "HOME": str(home_dir),
            "LANG": os.environ.get("LANG", "C.UTF-8"),
            "LC_ALL": os.environ.get("LC_ALL", "C.UTF-8"),
            "TMPDIR": str(tmp_dir),
            "XDG_CONFIG_HOME": str(config_dir),
            "XDG_CACHE_HOME": str(cache_dir),
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_AUTHOR_NAME": self.settings.pi_git_author_name,
            "GIT_AUTHOR_EMAIL": self.settings.pi_git_author_email,
            "GIT_COMMITTER_NAME": self.settings.pi_git_author_name,
            "GIT_COMMITTER_EMAIL": self.settings.pi_git_author_email,
            "NPM_CONFIG_UPDATE_NOTIFIER": "false",
            "NPM_CONFIG_FUND": "false",
            "NPM_CONFIG_AUDIT": "false",
            "NPM_CONFIG_LOGLEVEL": "error",
            "CI": "true",
        }
        github_token = self.settings.pi_github_token_value
        if github_token:
            env["GITHUB_TOKEN"] = github_token
            env["GH_TOKEN"] = github_token
        return env

    def _build_pi_runtime_env(self, runtime_dir: Path) -> dict[str, str]:
        home_dir = runtime_dir / "home"
        config_dir = runtime_dir / "config"
        cache_dir = runtime_dir / "cache"
        tmp_dir = runtime_dir / "tmp"
        for path in (home_dir, config_dir, cache_dir, tmp_dir):
            path.mkdir(parents=True, exist_ok=True)

        env = dict(os.environ)
        env.update(
            {
                "HOME": str(home_dir),
                "TMPDIR": str(tmp_dir),
                "XDG_CONFIG_HOME": str(config_dir),
                "XDG_CACHE_HOME": str(cache_dir),
                "GIT_AUTHOR_NAME": self.settings.pi_git_author_name,
                "GIT_AUTHOR_EMAIL": self.settings.pi_git_author_email,
                "GIT_COMMITTER_NAME": self.settings.pi_git_author_name,
                "GIT_COMMITTER_EMAIL": self.settings.pi_git_author_email,
                "NPM_CONFIG_UPDATE_NOTIFIER": "false",
                "NPM_CONFIG_FUND": "false",
                "NPM_CONFIG_AUDIT": "false",
                "NPM_CONFIG_LOGLEVEL": "error",
                "CI": "true",
            }
        )
        github_token = self.settings.pi_github_token_value
        if github_token:
            env["GITHUB_TOKEN"] = github_token
            env["GH_TOKEN"] = github_token
        return env

    def _configure_nebius_provider(
        self,
        *,
        home_dir: Path,
        env: dict[str, str],
        model: str | None,
    ) -> None:
        api_key = self.settings.pi_api_key_value
        if api_key:
            env["NEBIUS_API_KEY"] = api_key

        models_path = home_dir / ".pi" / "agent" / "models.json"
        models_path.parent.mkdir(parents=True, exist_ok=True)
        models_path.write_text(
            json.dumps(self._nebius_provider_config(model), indent=2) + "\n",
            encoding="utf-8",
        )

    def _nebius_provider_config(self, model: str | None) -> dict[str, object]:
        provider_config: dict[str, object] = {
            "providers": {
                "nebius": {
                    "baseUrl": self.settings.pi_base_url_value,
                    "api": "openai-completions",
                    "apiKey": "NEBIUS_API_KEY",
                    "authHeader": True,
                    "compat": {
                        "supportsDeveloperRole": False,
                        "supportsReasoningEffort": False,
                        "supportsStore": False,
                        "supportsUsageInStreaming": False,
                        "maxTokensField": "max_tokens",
                    },
                    "models": [],
                }
            }
        }
        if model:
            provider_config["providers"]["nebius"]["models"].append(
                {
                    "id": model,
                    "name": model,
                    "input": ["text"],
                    "reasoning": True,
                }
            )
        return provider_config

    @staticmethod
    def _merged_error_output(*, stdout: str, stderr: str, logs: str) -> str:
        parts: list[str] = []
        for chunk in (stderr, stdout, logs):
            for line in chunk.splitlines():
                cleaned = line.strip()
                if not cleaned:
                    continue
                if cleaned in parts:
                    continue
                parts.append(cleaned)
        return "\n".join(parts)

    def _clone_url(self, repo_url: str, *, github_slug: str | None) -> str:
        github_token = self.settings.pi_github_token_value
        if github_slug is None or github_token is None:
            return repo_url
        return f"https://x-access-token:{github_token}@github.com/{github_slug}.git"

    @staticmethod
    def _github_repo_slug(repo_url: str) -> str | None:
        if repo_url.startswith("git@github.com:"):
            slug = repo_url.removeprefix("git@github.com:")
        else:
            parsed = urlparse(repo_url)
            if parsed.hostname not in {"github.com", "www.github.com"}:
                return None
            slug = parsed.path.lstrip("/")
        if slug.endswith(".git"):
            slug = slug[:-4]
        parts = [part for part in slug.split("/") if part]
        if len(parts) != 2:
            return None
        return "/".join(parts)

    @staticmethod
    def _default_branch_name(prompt: str) -> str:
        timestamp = time.strftime("%Y%m%d%H%M%S", time.gmtime())
        slug = re.sub(r"[^a-z0-9]+", "-", prompt.lower()).strip("-")
        short_slug = slug[:24] or "change"
        return f"personal-agent/{timestamp}-{short_slug}"

    @staticmethod
    def _default_pr_title(prompt: str) -> str:
        cleaned = " ".join(prompt.strip().split())
        if not cleaned:
            return "personal-agent changes"
        return f"personal-agent: {cleaned[:68]}".strip()

    def _default_pr_body(self, request: PiRepositoryTaskRequest) -> str:
        requested_by = request.requested_by or "unknown"
        return (
            "Automated repository update prepared by personal-agent.\n\n"
            f"Requested by: {requested_by}\n"
            f"Prompt: {request.prompt}"
        )

    @staticmethod
    def _default_repo_push_pr_title(branch_name: str) -> str:
        return f"personal-agent: approve {branch_name}"[:72]

    @staticmethod
    def _default_repo_push_pr_body(*, requested_by: str | None, workspace_id: str) -> str:
        return (
            "Automated repository update approved for push by personal-agent.\n\n"
            f"Requested by: {requested_by or 'unknown'}\n"
            f"Workspace: {workspace_id}"
        )

    def _default_commit_message(self, request: PiRepositoryTaskRequest) -> str:
        return (request.pr_title or self._default_pr_title(request.prompt))[:72]

    def _default_system_prompt(self) -> str:
        sections = [
            "You are personal-agent's cloud coding agent.",
            "Behave like a remote sandboxed engineer: inspect context, execute commands, edit files, validate changes, and answer directly.",
        ]
        if self._blaxel_enabled:
            sections.extend(
                [
                    f"Direct tasks run in the Blaxel execution sandbox template `{self.settings.blaxel_execution_sandbox_image}`.",
                    f"Repository tasks run in the persistent Blaxel repo sandbox template `{self.settings.blaxel_repo_sandbox_image}`.",
                    f"The control plane/orchestrator runs in `{self.settings.blaxel_orchestrator_sandbox_image}`.",
                ]
            )
        else:
            sections.extend(
                [
                    "Blaxel sandboxes are unavailable in this runtime, so use the local Pi subprocess as a fallback.",
                    f"For deployed cloud execution, prefer the checked-in Blaxel templates `{self.settings.blaxel_execution_sandbox_image}` and `{self.settings.blaxel_repo_sandbox_image}`.",
                ]
            )
        sections.extend(
            [
                "The personal-agent Pi workspace templates are expected to provide node, npm/npx, git, python3, and pip.",
                "If a task needs Python execution, image generation, PIL, OpenCV, matplotlib, or other Python-first tooling, use the Python-capable personal-agent workspace template instead of assuming a Node-only sandbox.",
                "If a dependency is missing at runtime, report the concrete failure, explain the impact, and continue with the closest viable fallback.",
                "Be transparent in the final answer: state what you ran, what files you changed, which runtime handled the work, and any blockers or follow-up approval needed.",
                "For repository tasks you may prepare changes and commits, but pushing and PR creation require explicit approval from the surrounding automation.",
            ]
        )
        return "\n".join(sections)

    def _compose_system_prompt(self, extra_prompt: str | None) -> str | None:
        sections = [self._default_system_prompt()]
        if extra_prompt and extra_prompt.strip():
            sections.append(extra_prompt.strip())
        prompt = "\n\n".join(section for section in sections if section).strip()
        return prompt or None

    @staticmethod
    def _build_repo_system_prompt(
        *,
        repo_dir: Path,
        extra_prompt: str | None,
    ) -> str:
        sections = [
            "Repository workflow constraints:",
            f"Only read and write files inside this repository: {repo_dir}",
            "Do not inspect host-local files, parent directories, or unrelated local data.",
            "Make the requested repository changes directly on disk.",
            "Run targeted validation when it materially helps.",
            "Leave the repository in a reviewable state and summarize the changed files plus validation you ran.",
            "Do not open a pull request yourself; the surrounding automation will commit and only push/open a PR after explicit approval.",
        ]
        if extra_prompt:
            sections.append(extra_prompt)
        return "\n".join(sections)

    async def _detect_current_branch(
        self, repo_dir: Path, env: dict[str, str]
    ) -> str | None:
        result = await self._run_command(
            ["git", "branch", "--show-current"],
            cwd=str(repo_dir),
            env=env,
            timeout_seconds=30,
        )
        branch = result.stdout.strip()
        return branch or None

    async def _detect_remote_default_branch(
        self, repo_dir: Path, env: dict[str, str]
    ) -> str | None:
        result = await self._run_command(
            ["git", "symbolic-ref", "refs/remotes/origin/HEAD"],
            cwd=str(repo_dir),
            env=env,
            timeout_seconds=30,
        )
        if result.exit_code != 0:
            return None
        ref = result.stdout.strip()
        prefix = "refs/remotes/origin/"
        if not ref.startswith(prefix):
            return None
        branch = ref[len(prefix) :].strip()
        return branch or None

    def _resolve_workspace_dir(self, workspace_id: str) -> Path | None:
        cleaned = workspace_id.strip()
        if not re.fullmatch(r"[A-Za-z0-9._-]+", cleaned):
            return None
        workspace_root = self.settings.pi_workspace_root_path.resolve()
        workspace_dir = (workspace_root / cleaned).resolve()
        if workspace_dir.parent != workspace_root:
            return None
        return workspace_dir

    async def _create_pull_request(
        self,
        *,
        github_slug: str,
        github_token: str,
        title: str,
        body: str,
        head: str,
        base: str,
    ) -> str:
        def _create() -> str:
            request = Request(
                url=f"https://api.github.com/repos/{github_slug}/pulls",
                data=json.dumps(
                    {
                        "title": title,
                        "body": body,
                        "head": head,
                        "base": base,
                    }
                ).encode("utf-8"),
                headers={
                    "Accept": "application/vnd.github+json",
                    "Authorization": f"Bearer {github_token}",
                    "Content-Type": "application/json",
                    "User-Agent": "personal-agent/0.1",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
                method="POST",
            )
            try:
                with urlopen(request, timeout=30) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            except HTTPError as exc:
                error_body = exc.read().decode("utf-8", errors="replace")
                raise RuntimeError(
                    f"GitHub pull request creation failed: {exc.code} {error_body}"
                ) from exc
            except URLError as exc:
                raise RuntimeError(
                    f"GitHub pull request creation failed: {exc.reason}"
                ) from exc

            html_url = payload.get("html_url")
            if not html_url:
                raise RuntimeError(
                    "GitHub pull request creation did not return an html_url."
                )
            return str(html_url)

        return await asyncio.to_thread(_create)

    def _sanitize_command(self, command: list[str]) -> list[str]:
        sanitized = list(command)
        secrets = [
            secret
            for secret in (
                self.settings.pi_api_key_value,
                self.settings.pi_github_token_value,
            )
            if secret
        ]
        for index, part in enumerate(sanitized):
            if part == "--api-key" and index + 1 < len(sanitized):
                sanitized[index + 1] = "***"
                continue
            redacted = part
            for secret in secrets:
                redacted = redacted.replace(secret, "***")
            sanitized[index] = redacted
        return sanitized
