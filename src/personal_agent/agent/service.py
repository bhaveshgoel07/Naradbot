from __future__ import annotations

import re
from dataclasses import dataclass

from personal_agent.agent.models import (
    AgentArtifact,
    AgentFollowUp,
    AgentMessageRequest,
    AgentRepositoryPushRequest,
    AgentRepositoryRequest,
    AgentResponse,
    AgentRuntimeContext,
)
from personal_agent.automation.models import (
    PiRepositoryPushRequest,
    PiRepositoryTaskRequest,
    PiTaskRequest,
)
from personal_agent.automation.pi_agent import PiCodingAgentService
from personal_agent.config.settings import Settings


@dataclass(slots=True)
class AgentOrchestratorService:
    """Central transport-neutral orchestrator for the personal agent."""

    settings: Settings
    pi_agent: PiCodingAgentService

    def status(self) -> dict[str, object]:
        pi_status = self.pi_agent.status()
        return {
            **pi_status,
            "agent_type": "personal-agent",
            "supported_transports": ["api", "discord"],
            "capabilities": [
                "chat",
                "direct_code_execution",
                "repo_preparation",
                "approval_gated_push",
                "session_memory",
            ],
        }

    def session_id_for_transport(
        self,
        *,
        transport: str,
        conversation_id: str,
        actor_id: str,
    ) -> str:
        cleaned_transport = self._slug(transport, default="transport")
        cleaned_conversation = self._slug(conversation_id, default="conversation")
        cleaned_actor = self._slug(actor_id, default="actor")
        return f"{cleaned_transport}-{cleaned_conversation}-{cleaned_actor}"

    async def clear_session(self, session_id: str) -> bool:
        return await self.pi_agent.clear_task_session(session_id)

    async def handle_message(self, request: AgentMessageRequest) -> AgentResponse:
        prompt = request.prompt.strip()
        session_id = self._resolve_session_id(request)
        if not prompt:
            return AgentResponse(
                kind="chat",
                ok=False,
                error_text="Prompt was empty.",
                session_id=session_id,
                exit_code=400,
                runtime=AgentRuntimeContext(transport=request.transport),
            )

        result = await self.pi_agent.run_task(
            PiTaskRequest(
                prompt=prompt,
                workdir=request.workdir,
                files=list(request.files),
                tools=list(request.tools),
                provider=request.provider,
                model=request.model,
                thinking=request.thinking,
                append_system_prompt=self._compose_transport_system_prompt(
                    request.transport,
                    request.append_system_prompt,
                ),
                timeout_seconds=request.timeout_seconds,
                session_id=session_id,
                structured_output=True,
            )
        )
        ok = result.exit_code == 0
        final_text = self._task_primary_text(result)
        error_text = self._task_error_text(result)
        followups = self._chat_followups(session_id)
        return AgentResponse(
            kind="chat",
            ok=ok,
            final_text=final_text if ok else "",
            error_text="" if ok else error_text,
            session_id=session_id,
            exit_code=result.exit_code,
            runtime=AgentRuntimeContext(
                transport=request.transport,
                sandbox_mode=result.sandbox_mode,
                sandbox_name=result.sandbox_name,
                sandbox_image=result.sandbox_image,
                duration_seconds=result.duration_seconds,
            ),
            tool_traces=list(result.tool_traces),
            followups=followups,
        )

    async def prepare_repository(self, request: AgentRepositoryRequest) -> AgentResponse:
        result = await self.pi_agent.run_repository_task(
            PiRepositoryTaskRequest(
                repo_url=request.repo_url,
                prompt=request.prompt,
                base_branch=request.base_branch,
                branch_name=request.branch_name,
                pr_title=request.pr_title,
                pr_body=request.pr_body,
                provider=request.provider,
                model=request.model,
                thinking=request.thinking,
                append_system_prompt=request.append_system_prompt,
                timeout_seconds=request.timeout_seconds,
                tools=list(request.tools),
                requested_by=request.requested_by,
                allow_push=request.allow_push,
            )
        )
        artifacts = self._repo_artifacts(result)
        followups = self._repo_followups(result)
        ok = result.exit_code == 0
        return AgentResponse(
            kind="repo_prepare",
            ok=ok,
            final_text=self._repo_prepare_text(result) if ok else "",
            error_text="" if ok else self._repo_error_text(result),
            exit_code=result.exit_code,
            runtime=AgentRuntimeContext(
                transport=request.transport,
                sandbox_mode=result.sandbox_mode,
                sandbox_name=result.sandbox_name,
                sandbox_image=result.sandbox_image,
                duration_seconds=result.duration_seconds,
            ),
            artifacts=artifacts,
            followups=followups,
        )

    async def approve_repository_push(
        self,
        request: AgentRepositoryPushRequest,
    ) -> AgentResponse:
        result = await self.pi_agent.approve_repository_push(
            PiRepositoryPushRequest(
                workspace_id=request.workspace_id,
                pr_title=request.pr_title,
                pr_body=request.pr_body,
                base_branch=request.base_branch,
                requested_by=request.requested_by,
            )
        )
        artifacts = self._repo_push_artifacts(result)
        followups = self._repo_push_followups(result)
        ok = result.exit_code == 0
        return AgentResponse(
            kind="repo_push",
            ok=ok,
            final_text=self._repo_push_text(result) if ok else "",
            error_text="" if ok else self._repo_push_error_text(result),
            exit_code=result.exit_code,
            runtime=AgentRuntimeContext(
                transport=request.transport,
                sandbox_mode=result.sandbox_mode,
                sandbox_name=result.sandbox_name,
                sandbox_image=result.sandbox_image,
                duration_seconds=result.duration_seconds,
            ),
            artifacts=artifacts,
            followups=followups,
        )

    def _resolve_session_id(self, request: AgentMessageRequest) -> str | None:
        if request.session_id:
            return request.session_id
        if request.conversation_id and request.actor_id:
            return self.session_id_for_transport(
                transport=request.transport,
                conversation_id=request.conversation_id,
                actor_id=request.actor_id,
            )
        return None

    @staticmethod
    def _slug(value: str, *, default: str) -> str:
        cleaned = re.sub(r"[^a-z0-9-]+", "-", value.lower()).strip("-")
        return cleaned or default

    @staticmethod
    def _task_primary_text(result: object) -> str:
        assistant_response = (getattr(result, "assistant_response", "") or "").strip()
        if assistant_response:
            return assistant_response
        stdout = (getattr(result, "stdout", "") or "").strip()
        return stdout or "Completed without a text response."

    @staticmethod
    def _task_error_text(result: object) -> str:
        stderr = (getattr(result, "stderr", "") or "").strip()
        stdout = (getattr(result, "stdout", "") or "").strip()
        return stderr or stdout or "No error output was returned."

    def _transport_system_prompt(self, transport: str) -> str:
        sections = [
            f"You are responding through the {transport} interface.",
            "Lead with the direct answer or outcome.",
            "Be explicit about commands you ran, files you changed, missing dependencies, approval gates, and which runtime handled the work.",
        ]
        if transport in {"discord", "telegram"}:
            sections.append("Keep the answer concise enough for chat.")
        else:
            sections.append("Keep the answer concise but complete.")
        return "\n".join(sections)

    def _compose_transport_system_prompt(
        self,
        transport: str,
        extra_prompt: str | None,
    ) -> str:
        sections = [self._transport_system_prompt(transport)]
        if extra_prompt and extra_prompt.strip():
            sections.append(extra_prompt.strip())
        return "\n\n".join(section for section in sections if section)

    @staticmethod
    def _chat_followups(session_id: str | None) -> list[AgentFollowUp]:
        followups = [AgentFollowUp(action="continue_session", label="Reply to continue")]
        if session_id:
            followups.append(
                AgentFollowUp(
                    action="clear_session",
                    label="Clear session",
                    data={"session_id": session_id},
                )
            )
        return followups

    @staticmethod
    def _repo_prepare_text(result: object) -> str:
        pr_url = getattr(result, "pr_url", None)
        if pr_url:
            return f"Prepared repository changes and opened a pull request: {pr_url}"
        if not getattr(result, "changes_detected", False):
            return "Completed without producing repository changes."
        if getattr(result, "push_pending", False):
            return "Prepared repository changes and committed them in sandbox. Push is waiting for explicit approval."
        return "Prepared repository changes and committed them in sandbox."

    @staticmethod
    def _repo_error_text(result: object) -> str:
        stderr = (getattr(result, "stderr", "") or "").strip()
        stdout = (getattr(result, "stdout", "") or "").strip()
        return stderr or stdout or "No repository error output was returned."

    @staticmethod
    def _repo_artifacts(result: object) -> list[AgentArtifact]:
        artifacts: list[AgentArtifact] = []
        workspace_id = getattr(result, "workspace_id", None)
        branch_name = getattr(result, "branch_name", None)
        commit_sha = getattr(result, "commit_sha", None)
        pr_url = getattr(result, "pr_url", None)
        if workspace_id:
            artifacts.append(
                AgentArtifact(kind="workspace", label="Workspace", value=str(workspace_id))
            )
        if branch_name:
            artifacts.append(
                AgentArtifact(kind="branch", label="Branch", value=str(branch_name))
            )
        if commit_sha:
            artifacts.append(
                AgentArtifact(kind="commit", label="Commit", value=str(commit_sha))
            )
        if pr_url:
            artifacts.append(
                AgentArtifact(
                    kind="pull_request",
                    label="Pull request",
                    value=str(pr_url),
                    url=str(pr_url),
                )
            )
        return artifacts

    @staticmethod
    def _repo_followups(result: object) -> list[AgentFollowUp]:
        followups: list[AgentFollowUp] = []
        workspace_id = getattr(result, "workspace_id", None)
        pr_url = getattr(result, "pr_url", None)
        if getattr(result, "push_pending", False) and workspace_id:
            followups.append(
                AgentFollowUp(
                    action="approve_repo_push",
                    label="Approve repository push",
                    data={"workspace_id": str(workspace_id)},
                )
            )
        if pr_url:
            followups.append(
                AgentFollowUp(
                    action="review_pull_request",
                    label="Review pull request",
                    data={"url": str(pr_url)},
                )
            )
        return followups

    @staticmethod
    def _repo_push_text(result: object) -> str:
        pr_url = getattr(result, "pr_url", None)
        if pr_url:
            return f"Push approved and completed. Pull request created: {pr_url}"
        return "Push approved and completed."

    @staticmethod
    def _repo_push_error_text(result: object) -> str:
        stderr = (getattr(result, "stderr", "") or "").strip()
        stdout = (getattr(result, "stdout", "") or "").strip()
        return stderr or stdout or "No repository push error output was returned."

    @staticmethod
    def _repo_push_artifacts(result: object) -> list[AgentArtifact]:
        artifacts: list[AgentArtifact] = []
        branch_name = getattr(result, "branch_name", None)
        pr_url = getattr(result, "pr_url", None)
        workspace_id = getattr(result, "workspace_id", None)
        if workspace_id:
            artifacts.append(
                AgentArtifact(kind="workspace", label="Workspace", value=str(workspace_id))
            )
        if branch_name:
            artifacts.append(
                AgentArtifact(kind="branch", label="Branch", value=str(branch_name))
            )
        if pr_url:
            artifacts.append(
                AgentArtifact(
                    kind="pull_request",
                    label="Pull request",
                    value=str(pr_url),
                    url=str(pr_url),
                )
            )
        return artifacts

    @staticmethod
    def _repo_push_followups(result: object) -> list[AgentFollowUp]:
        pr_url = getattr(result, "pr_url", None)
        if not pr_url:
            return []
        return [
            AgentFollowUp(
                action="review_pull_request",
                label="Review pull request",
                data={"url": str(pr_url)},
            )
        ]
