from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass, field
from typing import Protocol
from urllib.parse import urlparse

from blaxel.core import SandboxInstance
from blaxel.core.sandbox.default.sandbox import SandboxAPIError
from blaxel.core.sandbox.types import VolumeBinding

from personal_agent.config.settings import Settings


class ExecutionProvider(Protocol):
    """Abstract interface for sandbox-backed command execution."""

    async def run_code(self, language: str, code: str) -> str:
        raise NotImplementedError


@dataclass(slots=True)
class BlaxelSandboxHandle:
    """Resolved Blaxel sandbox metadata used by the application runtime."""

    name: str
    component: str
    region: str | None
    image: str | None
    status: str | None
    url: str | None
    persistent: bool
    labels: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class BlaxelCommandResult:
    """Result of a command executed inside a Blaxel sandbox."""

    sandbox_name: str
    command: str
    exit_code: int
    stdout: str
    stderr: str
    logs: str
    status: str | None
    working_dir: str | None = None
    pid: str | None = None


@dataclass(slots=True)
class BlaxelSandboxService:
    """Factory and helper layer for Blaxel-backed sandbox lifecycles."""

    settings: Settings

    @property
    def available(self) -> bool:
        return self.settings.blaxel_sandboxes_enabled

    async def ensure_orchestrator_sandbox(self) -> BlaxelSandboxHandle:
        sandbox = await SandboxInstance.create_if_not_exists(
            self._sandbox_configuration(
                name=self.settings.blaxel_orchestrator_sandbox_name,
                image=self.settings.blaxel_orchestrator_sandbox_image,
                memory=self.settings.blaxel_orchestrator_sandbox_memory,
                component="orchestrator",
                persistent=True,
                ttl=self.settings.blaxel_orchestrator_sandbox_ttl,
                idle_ttl=self.settings.blaxel_orchestrator_sandbox_idle_ttl,
                volume_name=self.settings.blaxel_orchestrator_volume_name,
                mount_path=self.settings.blaxel_orchestrator_volume_mount_path,
                envs={
                    "PERSONAL_AGENT_PI_COMMAND": self.settings.pi_command,
                    "PERSONAL_AGENT_PI_PROVIDER": self.settings.pi_provider_value,
                    "PERSONAL_AGENT_PI_MODEL": self.settings.pi_model_value,
                    "PERSONAL_AGENT_PI_BASE_URL": self.settings.pi_base_url_value,
                },
            )
        )
        return self._sandbox_handle(
            sandbox,
            component="orchestrator",
            persistent=True,
        )

    async def create_execution_sandbox(
        self,
        *,
        request_key: str | None = None,
        envs: dict[str, str | None] | None = None,
    ) -> BlaxelSandboxHandle:
        sandbox_name = self._unique_ephemeral_name(
            self.settings.blaxel_execution_sandbox_prefix,
            request_key=request_key,
        )
        sandbox = await SandboxInstance.create(
            self._sandbox_configuration(
                name=sandbox_name,
                image=self.settings.blaxel_execution_sandbox_image,
                memory=self.settings.blaxel_execution_sandbox_memory,
                component="execution",
                persistent=False,
                ttl=self.settings.blaxel_execution_sandbox_ttl,
                idle_ttl=self.settings.blaxel_execution_sandbox_idle_ttl,
                volume_name=self.settings.blaxel_execution_volume_name,
                mount_path=self.settings.blaxel_execution_volume_mount_path,
                envs=envs,
            )
        )
        return self._sandbox_handle(
            sandbox,
            component="execution",
            persistent=False,
        )

    async def ensure_repo_sandbox(self, repo_url: str) -> BlaxelSandboxHandle:
        repo_key = self.repo_key(repo_url)
        sandbox = await SandboxInstance.create_if_not_exists(
            self._sandbox_configuration(
                name=self.repo_sandbox_name(repo_url),
                image=self.settings.blaxel_repo_sandbox_image,
                memory=self.settings.blaxel_repo_sandbox_memory,
                component="repo",
                persistent=True,
                ttl=self.settings.blaxel_repo_sandbox_ttl,
                idle_ttl=self.settings.blaxel_repo_sandbox_idle_ttl,
                volume_name=self.settings.blaxel_repo_volume_name,
                mount_path=self.settings.blaxel_repo_volume_mount_path,
                labels={"repo": repo_key},
            )
        )
        return self._sandbox_handle(
            sandbox,
            component="repo",
            persistent=True,
        )

    async def ensure_computer_use_sandbox(self) -> BlaxelSandboxHandle:
        sandbox = await SandboxInstance.create_if_not_exists(
            self._sandbox_configuration(
                name=self.settings.blaxel_computer_use_sandbox_name,
                image=self.settings.blaxel_computer_use_sandbox_image,
                memory=self.settings.blaxel_computer_use_sandbox_memory,
                component="computer-use",
                persistent=True,
                ttl=self.settings.blaxel_computer_use_sandbox_ttl,
                idle_ttl=self.settings.blaxel_computer_use_sandbox_idle_ttl,
                volume_name=self.settings.blaxel_computer_use_volume_name,
                mount_path=self.settings.blaxel_computer_use_volume_mount_path,
                ports=self._normalized_ports(
                    [
                        self.settings.blaxel_computer_use_preview_port,
                    ]
                ),
            )
        )
        return self._sandbox_handle(
            sandbox,
            component="computer-use",
            persistent=True,
        )

    async def get_sandbox(self, sandbox_name: str) -> BlaxelSandboxHandle:
        sandbox = await SandboxInstance.get(sandbox_name)
        labels = self._labels_from_sandbox(sandbox)
        return self._sandbox_handle(
            sandbox,
            component=labels.get("component", "unknown"),
            persistent=labels.get("persistent", "false") == "true",
        )

    async def delete_sandbox(self, sandbox_name: str) -> None:
        sandbox = await SandboxInstance.get(sandbox_name)
        await sandbox.delete()

    async def delete_sandbox_if_exists(self, sandbox_name: str) -> bool:
        try:
            await self.delete_sandbox(sandbox_name)
        except SandboxAPIError as exc:
            if getattr(exc, "status_code", None) == 404:
                return False
            raise
        return True

    async def run_command(
        self,
        sandbox_name: str,
        *,
        command: str,
        working_dir: str | None = None,
        env: dict[str, str] | None = None,
        timeout_seconds: int | None = None,
        wait_for_completion: bool = True,
        name: str | None = None,
    ) -> BlaxelCommandResult:
        sandbox = await SandboxInstance.get(sandbox_name)
        use_inline_wait = wait_for_completion and (
            timeout_seconds is None or timeout_seconds <= 60
        )
        payload: dict[str, object] = {
            "command": command,
            "wait_for_completion": use_inline_wait,
        }
        if working_dir:
            payload["working_dir"] = working_dir
        if env:
            payload["env"] = env
        if timeout_seconds is not None:
            payload["timeout"] = timeout_seconds
        if name:
            payload["name"] = name
        process = await sandbox.process.exec(payload)
        if wait_for_completion and not use_inline_wait:
            identifier = str(getattr(process, "name", "") or getattr(process, "pid", ""))
            if not identifier:
                raise RuntimeError("Sandbox process did not return a name or pid.")
            process = await sandbox.process.wait(
                identifier,
                max_wait=timeout_seconds * 1000 if timeout_seconds else 600_000,
                interval=1_000,
            )
            logs = await sandbox.process.logs(identifier)
        else:
            logs = str(getattr(process, "logs", "") or "")
        return BlaxelCommandResult(
            sandbox_name=sandbox_name,
            command=command,
            exit_code=int(getattr(process, "exit_code", 0) or 0),
            stdout=str(getattr(process, "stdout", "") or ""),
            stderr=str(getattr(process, "stderr", "") or ""),
            logs=logs,
            status=self._stringify_status(getattr(process, "status", None)),
            working_dir=working_dir,
            pid=str(getattr(process, "pid", "") or "") or None,
        )

    async def read_file(self, sandbox_name: str, path: str) -> str:
        sandbox = await SandboxInstance.get(sandbox_name)
        return await sandbox.fs.read(path)

    async def write_file(self, sandbox_name: str, path: str, content: str) -> None:
        sandbox = await SandboxInstance.get(sandbox_name)
        await sandbox.fs.write(path, content)

    async def write_binary_file(
        self, sandbox_name: str, path: str, content: bytes
    ) -> None:
        sandbox = await SandboxInstance.get(sandbox_name)
        await sandbox.fs.write_binary(path, content)

    async def mkdir(self, sandbox_name: str, path: str) -> None:
        sandbox = await SandboxInstance.get(sandbox_name)
        await sandbox.fs.mkdir(path)

    async def remove_path(
        self, sandbox_name: str, path: str, *, recursive: bool = False
    ) -> None:
        sandbox = await SandboxInstance.get(sandbox_name)
        await sandbox.fs.rm(path, recursive=recursive)

    def repo_key(self, repo_url: str) -> str:
        parsed = urlparse(repo_url)
        if parsed.netloc:
            raw = f"{parsed.netloc}{parsed.path}"
        else:
            raw = repo_url
        return self._slugify(raw.replace(".git", ""), default="repo")

    def repo_sandbox_name(self, repo_url: str) -> str:
        repo_key = self.repo_key(repo_url)
        return self._stable_name(
            prefix=self.settings.blaxel_repo_sandbox_prefix,
            suffix=repo_key,
        )

    def execution_workdir(self) -> str:
        return self.settings.blaxel_execution_workspace_root

    def repo_workdir(self) -> str:
        return self.settings.blaxel_repo_workspace_root

    def orchestrator_workdir(self) -> str:
        return self.settings.blaxel_orchestrator_workspace_root

    def computer_use_workdir(self) -> str:
        return self.settings.blaxel_computer_use_workspace_root

    def _sandbox_configuration(
        self,
        *,
        name: str,
        image: str,
        memory: int,
        component: str,
        persistent: bool,
        ttl: str | None,
        idle_ttl: str | None,
        volume_name: str | None,
        mount_path: str | None,
        envs: dict[str, str | None] | None = None,
        ports: list[dict[str, object]] | None = None,
        labels: dict[str, str] | None = None,
    ) -> dict[str, object]:
        payload: dict[str, object] = {
            "name": name,
            "image": image,
            "memory": memory,
            "region": self.settings.blaxel_region,
            "labels": {
                "app": "personal-agent",
                "component": component,
                "persistent": "true" if persistent else "false",
                **(labels or {}),
            },
        }
        if ttl:
            payload["ttl"] = ttl
        lifecycle = self._lifecycle(idle_ttl)
        if lifecycle is not None:
            payload["lifecycle"] = lifecycle
        normalized_envs = self._normalized_envs(envs)
        if normalized_envs:
            payload["envs"] = normalized_envs
        if ports:
            payload["ports"] = ports
        volumes = self._normalized_volumes(volume_name, mount_path)
        if volumes:
            payload["volumes"] = volumes
        return payload

    def _lifecycle(self, idle_ttl: str | None) -> dict[str, object] | None:
        if not idle_ttl:
            return None
        return {
            "expiration_policies": [
                {
                    "type": "ttl-idle",
                    "value": idle_ttl,
                    "action": "delete",
                }
            ]
        }

    @staticmethod
    def _normalized_envs(
        envs: dict[str, str | None] | None,
    ) -> list[dict[str, str]] | None:
        if not envs:
            return None
        normalized = [
            {"name": key, "value": value}
            for key, value in envs.items()
            if value is not None and value != ""
        ]
        return normalized or None

    @staticmethod
    def _normalized_ports(ports: list[int | None]) -> list[dict[str, object]] | None:
        normalized = [
            {"target": port, "protocol": "HTTP"}
            for port in ports
            if port is not None and port > 0
        ]
        return normalized or None

    @staticmethod
    def _normalized_volumes(
        volume_name: str | None,
        mount_path: str | None,
    ) -> list[VolumeBinding] | None:
        if not volume_name or not mount_path:
            return None
        return [VolumeBinding(name=volume_name, mount_path=mount_path)]

    def _sandbox_handle(
        self,
        sandbox: SandboxInstance,
        *,
        component: str,
        persistent: bool,
    ) -> BlaxelSandboxHandle:
        metadata = getattr(sandbox, "metadata", None)
        spec = getattr(sandbox, "spec", None)
        runtime = getattr(spec, "runtime", None)
        return BlaxelSandboxHandle(
            name=str(getattr(metadata, "name", "")),
            component=component,
            region=str(getattr(spec, "region", "") or "") or None,
            image=str(getattr(runtime, "image", "") or "") or None,
            status=self._stringify_status(getattr(sandbox, "status", None)),
            url=str(getattr(metadata, "url", "") or "") or None,
            persistent=persistent,
            labels=self._labels_from_sandbox(sandbox),
        )

    @staticmethod
    def _labels_from_sandbox(sandbox: SandboxInstance) -> dict[str, str]:
        metadata = getattr(sandbox, "metadata", None)
        labels = getattr(metadata, "labels", None)
        if labels is None:
            return {}
        if hasattr(labels, "to_dict"):
            return {
                str(key): str(value)
                for key, value in labels.to_dict().items()
                if value is not None
            }
        return {str(key): str(value) for key, value in dict(labels).items()}

    def _stable_name(self, *, prefix: str, suffix: str) -> str:
        cleaned_prefix = self._slugify(prefix, default="sandbox")
        cleaned_suffix = self._slugify(suffix, default="default")
        digest = hashlib.sha1(cleaned_suffix.encode("utf-8")).hexdigest()[:8]
        max_suffix_length = max(6, 49 - len(cleaned_prefix) - len(digest) - 2)
        trimmed_suffix = cleaned_suffix[:max_suffix_length].strip("-") or "default"
        return f"{cleaned_prefix}-{trimmed_suffix}-{digest}"[:49].rstrip("-")

    def _unique_ephemeral_name(
        self,
        prefix: str,
        *,
        request_key: str | None,
    ) -> str:
        cleaned_prefix = self._slugify(prefix, default="sandbox")
        key_digest = hashlib.sha1((request_key or "task").encode("utf-8")).hexdigest()[:6]
        timestamp = int(time.time())
        name = f"{cleaned_prefix}-{timestamp}-{key_digest}"
        return name[:49].rstrip("-")

    @staticmethod
    def _slugify(value: str, *, default: str) -> str:
        cleaned = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
        return cleaned or default

    @staticmethod
    def _stringify_status(status: object) -> str | None:
        if status is None:
            return None
        value = getattr(status, "value", status)
        text = str(value).strip()
        return text or None


@dataclass(slots=True)
class BlaxelExecutionProvider:
    """Thin execution provider backed by throwaway Blaxel sandboxes."""

    sandbox_service: BlaxelSandboxService

    async def run_code(self, language: str, code: str) -> str:
        sandbox = await self.sandbox_service.create_execution_sandbox(
            request_key=language
        )
        sandbox_name = sandbox.name
        workdir = self.sandbox_service.execution_workdir()
        extension = {
            "python": "py",
            "javascript": "js",
            "typescript": "ts",
            "shell": "sh",
        }.get(language.lower(), "txt")
        source_path = f"{workdir}/snippet.{extension}"
        try:
            await self.sandbox_service.mkdir(sandbox_name, workdir)
            await self.sandbox_service.write_file(sandbox_name, source_path, code)
            command = {
                "python": f"python3 {source_path}",
                "javascript": f"node {source_path}",
                "typescript": f"npx -y tsx {source_path}",
                "shell": f"sh {source_path}",
            }.get(language.lower())
            if command is None:
                return f"Unsupported execution language: {language}"
            result = await self.sandbox_service.run_command(
                sandbox_name,
                command=command,
                working_dir=workdir,
                timeout_seconds=60,
            )
            output = result.stdout.strip()
            error_output = result.stderr.strip()
            if result.exit_code != 0:
                return error_output or output or f"Execution failed with exit code {result.exit_code}."
            return output or error_output or "Execution completed without textual output."
        finally:
            await self.sandbox_service.delete_sandbox_if_exists(sandbox_name)
