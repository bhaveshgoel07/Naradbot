from __future__ import annotations

import asyncio
import json
import os
import re
import shlex
import shutil
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from personal_agent.automation.models import (
    PiRepositoryTaskRequest,
    PiRepositoryTaskResult,
    PiTaskRequest,
    PiTaskResult,
)
from personal_agent.config.settings import Settings


@dataclass(slots=True)
class _CommandResult:
    command: list[str]
    exit_code: int
    stdout: str
    stderr: str


@dataclass(slots=True)
class PiCodingAgentService:
    """Thin subprocess wrapper around the Pi coding-agent CLI."""

    settings: Settings

    def status(self) -> dict[str, object]:
        command = shlex.split(self.settings.pi_command)
        executable = command[0] if command else None
        resolved_binary = shutil.which(executable) if executable else None
        git_binary = shutil.which("git")
        gh_binary = shutil.which("gh")
        return {
            "configured_command": command,
            "resolved_binary": resolved_binary,
            "available": resolved_binary is not None,
            "default_tools": list(self.settings.pi_default_tools),
            "default_model": self.settings.pi_model,
            "default_provider": self.settings.pi_provider,
            "default_thinking": self.settings.pi_default_thinking,
            "git_binary": git_binary,
            "gh_binary": gh_binary,
            "git_available": git_binary is not None,
            "gh_available": gh_binary is not None,
            "sandbox_mode": "isolated_repo_clone",
            "workspace_root": str(self.settings.pi_workspace_root_path),
            "repo_workflow_available": resolved_binary is not None and git_binary is not None,
            "blaxel_execution_enabled": False,
            "requires_github_token_for_prs": True,
        }

    async def run_task(self, request: PiTaskRequest) -> PiTaskResult:
        command = self._build_command(request)
        start = time.monotonic()
        result = await self._run_command(
            command,
            cwd=request.workdir or str(Path.cwd()),
            timeout_seconds=request.timeout_seconds or self.settings.pi_timeout_seconds,
        )
        return PiTaskResult(
            available=self.status()["available"],
            command=self._sanitize_command(result.command),
            exit_code=result.exit_code,
            stdout=result.stdout,
            stderr=result.stderr,
            duration_seconds=round(time.monotonic() - start, 2),
        )

    async def run_repository_task(
        self, request: PiRepositoryTaskRequest
    ) -> PiRepositoryTaskResult:
        start = time.monotonic()
        status = self.status()
        setup_commands: list[list[str]] = []
        sandbox_mode = "isolated_repo_clone"

        if not status["available"]:
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

    async def _run_task_with_env(
        self,
        request: PiTaskRequest,
        *,
        env: dict[str, str],
    ) -> PiTaskResult:
        command = self._build_command(request)
        start = time.monotonic()
        result = await self._run_command(
            command,
            cwd=request.workdir or str(Path.cwd()),
            env=env,
            timeout_seconds=request.timeout_seconds or self.settings.pi_timeout_seconds,
        )
        return PiTaskResult(
            available=self.status()["available"],
            command=self._sanitize_command(result.command),
            exit_code=result.exit_code,
            stdout=result.stdout,
            stderr=result.stderr,
            duration_seconds=round(time.monotonic() - start, 2),
        )

    def _build_command(self, request: PiTaskRequest) -> list[str]:
        command = shlex.split(self.settings.pi_command)
        command.append("-p")

        if self.settings.pi_no_session:
            command.append("--no-session")

        tools = request.tools or list(self.settings.pi_default_tools)
        if tools:
            command.extend(["--tools", ",".join(tools)])

        provider = request.provider or self.settings.pi_provider
        if provider:
            command.extend(["--provider", provider])

        model = request.model or self.settings.pi_model
        if model:
            command.extend(["--model", model])

        api_key = self.settings.pi_api_key_value
        if api_key:
            command.extend(["--api-key", api_key])

        thinking = request.thinking or self.settings.pi_default_thinking
        if thinking:
            command.extend(["--thinking", thinking])

        if request.append_system_prompt:
            command.extend(["--append-system-prompt", request.append_system_prompt])

        for file_path in request.files:
            command.append(f"@{file_path}")

        command.append(request.prompt)
        return command

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
        }
        github_token = self.settings.pi_github_token_value
        if github_token:
            env["GITHUB_TOKEN"] = github_token
            env["GH_TOKEN"] = github_token
        return env

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
            if parsed.netloc not in {"github.com", "www.github.com"}:
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

    def _default_commit_message(self, request: PiRepositoryTaskRequest) -> str:
        return (request.pr_title or self._default_pr_title(request.prompt))[:72]

    @staticmethod
    def _build_repo_system_prompt(
        *,
        repo_dir: Path,
        extra_prompt: str | None,
    ) -> str:
        sections = [
            "You are operating in an isolated cloned repository workspace.",
            f"Only read and write files inside this repository: {repo_dir}",
            "Do not inspect host-local files, parent directories, or unrelated local data.",
            "Make the requested repository changes directly on disk.",
            "Run targeted validation when it materially helps.",
            "Do not open a pull request yourself; the surrounding automation will commit, push, and open the PR after you finish editing.",
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
