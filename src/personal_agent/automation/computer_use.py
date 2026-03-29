from __future__ import annotations

from dataclasses import dataclass

from personal_agent.config.settings import Settings
from personal_agent.execution.blaxel import BlaxelSandboxHandle, BlaxelSandboxService


@dataclass(slots=True)
class ComputerUseService:
    """Thin provisioning contract for the future computer-use sandbox."""

    settings: Settings
    sandbox_service: BlaxelSandboxService

    def status(self) -> dict[str, object]:
        return {
            "enabled": self.sandbox_service.available,
            "sandbox_name": self.settings.blaxel_computer_use_sandbox_name,
            "workspace_root": self.settings.blaxel_computer_use_workspace_root,
            "preview_port": self.settings.blaxel_computer_use_preview_port,
            "persistent": True,
            "actions_enabled": [],
        }

    async def provision(self) -> dict[str, object]:
        sandbox = await self.sandbox_service.ensure_computer_use_sandbox()
        return self._serialize_handle(sandbox)

    @staticmethod
    def _serialize_handle(handle: BlaxelSandboxHandle) -> dict[str, object]:
        return {
            "enabled": True,
            "sandbox_name": handle.name,
            "component": handle.component,
            "region": handle.region,
            "image": handle.image,
            "status": handle.status,
            "url": handle.url,
            "persistent": handle.persistent,
            "labels": handle.labels,
            "actions_enabled": [],
        }
