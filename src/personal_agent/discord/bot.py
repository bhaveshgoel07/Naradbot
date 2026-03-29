from __future__ import annotations

import json
import logging
import re
from typing import Any

import discord
from discord.ext import commands

from personal_agent.agent.models import (
    AgentMessageRequest,
    AgentRepositoryPushRequest,
    AgentRepositoryRequest,
)
from personal_agent.agent.service import AgentOrchestratorService
from personal_agent.config.settings import Settings
from personal_agent.discord.messages import DISCORD_MESSAGE_CHAR_LIMIT, split_discord_message_content
from personal_agent.hn.service import HNService
from personal_agent.storage.repositories import HNRunRepository

logger = logging.getLogger(__name__)

_RAW_TOKEN_PATTERN = re.compile(
    r"<\|(?:tool_calls?_section_begin|tool_calls?_section_end|tool_call_begin|"
    r"tool_call_end|tool_call_argument_begin|tool_call_argument_end|"
    r"tool_result_begin|tool_result_end|im_start|im_end|endoftext)[^|]*\|>"
)


class PersonalAgentDiscordBot(commands.Bot):
    """Discord bot that forwards commands into the application services."""

    def __init__(
        self,
        *,
        settings: Settings,
        hn_service: HNService,
        run_repository: HNRunRepository,
        agent_orchestrator_service: AgentOrchestratorService,
    ) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(command_prefix=settings.discord_command_prefix, intents=intents)
        self.settings = settings
        self.hn_service = hn_service
        self.run_repository = run_repository
        self.agent_orchestrator_service = agent_orchestrator_service

    async def setup_hook(self) -> None:
        @self.command(name="ping")
        async def ping(ctx: commands.Context[Any]) -> None:
            await ctx.send("pong")

        @self.command(name="hn")
        async def hacker_news(ctx: commands.Context[Any]) -> None:
            await ctx.typing()
            result = await self.hn_service.run(
                trigger_source="discord_command",
                requested_by=str(ctx.author),
                publish_to_discord=True,
            )
            await ctx.send(
                "Hacker News workflow completed. "
                f"Stories processed: {result['story_count']}. "
                f"Published channels: {', '.join(result['published_channels']) or 'none'}."
            )

        @self.command(name="status")
        async def status(ctx: commands.Context[Any]) -> None:
            recent_runs = self.run_repository.recent_runs(limit=3)
            if not recent_runs:
                await ctx.send("No Hacker News runs recorded yet.")
                return
            lines = ["Recent Hacker News runs:"]
            for run in recent_runs:
                lines.append(
                    f"- {run['status']} via {run['trigger_source']} with {run['story_count']} stories at {run['finished_at']}"
                )
            await ctx.send("\n".join(lines))

        @self.command(name="pi-status")
        async def pi_status(ctx: commands.Context[Any]) -> None:
            await ctx.send(
                self.format_pi_status_message(self.agent_orchestrator_service.status())
            )

        @self.command(name="code")
        async def code(ctx: commands.Context[Any], *, prompt: str) -> None:
            await self._handle_pi_prompt(message=ctx.message, prompt=prompt)

        @self.command(name="code-reset")
        async def code_reset(ctx: commands.Context[Any]) -> None:
            cleared = await self.agent_orchestrator_service.clear_session(
                self._discord_session_id(ctx.message)
            )
            if cleared:
                await ctx.send(
                    "Cleared your Pi chat history for this channel. Send another message or `!code ...` to start fresh."
                )
                return
            await ctx.send(
                "There was no saved Pi chat history for you in this channel. Send another message or `!code ...` to start one."
            )

        @self.command(name="repo")
        async def repo(
            ctx: commands.Context[Any],
            repo_url: str,
            *,
            prompt: str,
        ) -> None:
            await ctx.typing()
            result = await self.agent_orchestrator_service.prepare_repository(
                AgentRepositoryRequest(
                    repo_url=repo_url,
                    prompt=prompt,
                    transport="discord",
                    requested_by=str(ctx.author),
                    allow_push=False,
                )
            )
            await ctx.send(self.format_pi_repo_result_message(result))
            for message in self.format_followup_messages(
                result,
                author_mention=ctx.author.mention,
            ):
                await ctx.send(
                    message
                )

        @self.command(name="repo-push")
        async def repo_push(
            ctx: commands.Context[Any],
            workspace_id: str,
            *,
            pr_title: str | None = None,
        ) -> None:
            await ctx.typing()
            result = await self.agent_orchestrator_service.approve_repository_push(
                AgentRepositoryPushRequest(
                    workspace_id=workspace_id,
                    transport="discord",
                    pr_title=pr_title,
                    requested_by=str(ctx.author),
                )
            )
            await ctx.send(self.format_pi_repo_push_result_message(result))
            for message in self.format_followup_messages(
                result,
                author_mention=ctx.author.mention,
            ):
                await ctx.send(
                    message
                )

        logger.info(
            "Discord commands registered with prefix=%r commands=%s",
            self.settings.discord_command_prefix,
            sorted(self.all_commands.keys()),
        )

    @staticmethod
    def format_pi_status_message(status: dict[str, object]) -> str:
        return (
            "Pi status:\n"
            f"- available: {status.get('available')}\n"
            f"- provider/model: {status.get('default_provider') or 'default'} / {status.get('default_model') or 'default'}\n"
            f"- cloud mode: {status.get('cloud_agent_mode') or 'unknown'}\n"
            f"- task runtime: {status.get('sandbox_mode')}\n"
            f"- execution image: {status.get('blaxel_execution_sandbox_image') or 'n/a'}\n"
            f"- repo image: {status.get('blaxel_repo_sandbox_image') or 'n/a'}\n"
            f"- repo workflow: {status.get('repo_workflow_available')}\n"
            f"- workspace root: {status.get('workspace_root')}"
        )

    @staticmethod
    def format_pi_task_result_message(result: object) -> str:
        is_error = not getattr(result, "ok", getattr(result, "exit_code", 0) == 0)
        if is_error:
            details = PersonalAgentDiscordBot._strip_raw_tokens(
                PersonalAgentDiscordBot._trim_output(
                    PersonalAgentDiscordBot._result_error_text(result)
                )
            )
            return (
                f"**Pi failed** (exit code {getattr(result, 'exit_code', 'unknown')})\n"
                f"```\n{details or 'No error output was returned.'}\n```"
            )
        output = PersonalAgentDiscordBot._strip_raw_tokens(
            PersonalAgentDiscordBot._trim_output(
                PersonalAgentDiscordBot._result_final_text(result)
            )
        )
        if not output:
            output = "Completed without textual output."
        return f"**Pi** {output}"

    @staticmethod
    def format_pi_chat_messages(result: object) -> list[str]:
        tool_traces = list(getattr(result, "tool_traces", []) or [])
        duration = PersonalAgentDiscordBot._runtime_field(result, "duration_seconds")
        session_id = getattr(result, "session_id", None)
        sandbox_mode = PersonalAgentDiscordBot._runtime_field(result, "sandbox_mode")
        sandbox_name = PersonalAgentDiscordBot._runtime_field(result, "sandbox_name")
        sandbox_image = PersonalAgentDiscordBot._runtime_field(result, "sandbox_image")
        is_error = not getattr(result, "ok", getattr(result, "exit_code", 0) == 0)

        lines: list[str] = []

        if is_error:
            lines.append(
                f"**Pi Error** (exit code {getattr(result, 'exit_code', 'unknown')})"
            )
            details = PersonalAgentDiscordBot._strip_raw_tokens(
                PersonalAgentDiscordBot._trim_output(
                    PersonalAgentDiscordBot._result_error_text(result)
                )
            )
            if details:
                lines.append(f"```\n{details}\n```")
            else:
                lines.append("No error output was returned.")
        else:
            primary_output = PersonalAgentDiscordBot._strip_raw_tokens(
                PersonalAgentDiscordBot._result_final_text(result)
                or PersonalAgentDiscordBot._primary_pi_response(result)
            )
            if primary_output:
                lines.append("**Pi Response**")
                lines.append(primary_output)
            else:
                lines.append("**Pi Response** completed without a text response.")

        lines.append("")
        lines.append("**Execution Summary**")
        lines.append(f"- status: {'failed' if is_error else 'success'}")
        if duration is not None:
            lines.append(f"- duration: {duration:.1f}s")
        lines.append(f"- tool calls: {len(tool_traces)}")
        if session_id:
            lines.append(f"- session: `{session_id}`")
        if sandbox_mode:
            lines.append(f"- runtime: `{sandbox_mode}`")
        if sandbox_name:
            lines.append(f"- sandbox: `{sandbox_name}`")
        if sandbox_image:
            lines.append(f"- image: `{sandbox_image}`")

        if tool_traces:
            lines.append("")
            lines.append("**Tool Trace**")
            for index, trace in enumerate(tool_traces, start=1):
                tool_name = getattr(trace, "tool_name", "tool")
                trace_is_error = getattr(trace, "is_error", False)
                lines.append(
                    f"{index}. `{tool_name}` ({'error' if trace_is_error else 'ok'})"
                )

                preview = PersonalAgentDiscordBot._tool_argument_preview(getattr(trace, "arguments", {}))
                if preview is not None:
                    _label, value = preview
                    lines.append("```")
                    lines.append(value)
                    lines.append("```")

                output = PersonalAgentDiscordBot._strip_raw_tokens(
                    PersonalAgentDiscordBot._trim_output(
                        getattr(trace, "output", ""),
                        limit=500,
                    )
                )
                if output:
                    lines.append("output:")
                    lines.append("```")
                    lines.append(PersonalAgentDiscordBot._escape_inline_code(output))
                    lines.append("```")
                elif trace_is_error:
                    lines.append("output: Tool call failed with no output")
                lines.append("")

        footer_parts = ["Reply to continue"]
        for followup in list(getattr(result, "followups", []) or []):
            action = getattr(followup, "action", "")
            if action == "clear_session":
                footer_parts.append("`!code-reset` to start fresh")
        lines.append("-# " + " \u2022 ".join(footer_parts))

        return split_discord_message_content("\n".join(lines))

    @staticmethod
    def format_pi_repo_result_message(result: object) -> str:
        if getattr(result, "kind", "") == "repo_prepare":
            repo_context = PersonalAgentDiscordBot._repo_runtime_summary(result)
            text = getattr(result, "final_text", "") if getattr(result, "ok", False) else getattr(result, "error_text", "")
            text = text or "Repository task completed without a summary."
            return f"{text}\nRuntime: {repo_context}"
        repo_context = PersonalAgentDiscordBot._repo_runtime_summary(result)
        pr_url = getattr(result, "pr_url", None)
        if pr_url:
            return (
                f"Pi finished {repo_context} "
                f"and opened a pull request: {pr_url}"
            )
        if getattr(result, "exit_code", 0) != 0:
            details = PersonalAgentDiscordBot._trim_output(
                getattr(result, "stderr", "") or getattr(result, "stdout", "")
            )
            return (
                f"Pi failed {repo_context} with exit code {getattr(result, 'exit_code', 'unknown')}.\n"
                f"{details or 'No error output was returned.'}"
            )
        if not getattr(result, "changes_detected", False):
            return (
                f"Pi finished {repo_context} but did not leave file changes."
            )
        if getattr(result, "push_pending", False):
            return (
                f"Pi prepared repository changes {repo_context} and committed them in sandbox, "
                "waiting for explicit push approval."
            )
        return (
            f"Pi finished {repo_context} and committed changes, "
            "but no pull request URL was created.\n"
            f"{PersonalAgentDiscordBot._trim_output(getattr(result, 'stderr', ''))}"
        )

    @staticmethod
    def format_repo_push_instruction_message(*, workspace_id: str, author_mention: str) -> str:
        return (
            f"{author_mention} approve push when ready with "
            f"`!repo-push {workspace_id}` to publish branch and open a PR."
        )

    @staticmethod
    def format_pi_repo_push_result_message(result: object) -> str:
        if getattr(result, "kind", "") == "repo_push":
            repo_context = PersonalAgentDiscordBot._repo_runtime_summary(result)
            text = getattr(result, "final_text", "") if getattr(result, "ok", False) else getattr(result, "error_text", "")
            text = text or "Repository push completed without a summary."
            return f"{text}\nRuntime: {repo_context}"
        repo_context = PersonalAgentDiscordBot._repo_runtime_summary(result)
        if getattr(result, "exit_code", 0) != 0:
            details = PersonalAgentDiscordBot._trim_output(
                getattr(result, "stderr", "") or getattr(result, "stdout", "")
            )
            return (
                f"Repo push failed {repo_context} with exit code {getattr(result, 'exit_code', 'unknown')}.\n"
                f"{details or 'No error output was returned.'}"
            )
        pr_url = getattr(result, "pr_url", None)
        if pr_url:
            return f"Push approved and completed {repo_context}. Pull request created: {pr_url}"
        return (
            f"Push completed {repo_context} but pull request creation did not return a URL.\n"
            f"{PersonalAgentDiscordBot._trim_output(getattr(result, 'stderr', ''))}"
        )

    @staticmethod
    def format_pr_review_message(*, pr_url: str, author_mention: str) -> str:
        return f"{author_mention} review this pull request in command-center before merge: {pr_url}"

    @staticmethod
    def format_followup_messages(result: object, *, author_mention: str) -> list[str]:
        messages: list[str] = []
        for followup in list(getattr(result, "followups", []) or []):
            action = getattr(followup, "action", "")
            data = getattr(followup, "data", {}) or {}
            if action == "approve_repo_push" and data.get("workspace_id"):
                messages.append(
                    PersonalAgentDiscordBot.format_repo_push_instruction_message(
                        workspace_id=data["workspace_id"],
                        author_mention=author_mention,
                    )
                )
            if action == "review_pull_request" and data.get("url"):
                messages.append(
                    PersonalAgentDiscordBot.format_pr_review_message(
                        pr_url=data["url"],
                        author_mention=author_mention,
                    )
                )
        return messages

    @staticmethod
    def _strip_raw_tokens(text: str) -> str:
        if not text:
            return text
        cleaned = _RAW_TOKEN_PATTERN.sub("", text)
        cleaned = re.sub(r"call_\d+\s*", "", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        return cleaned

    @staticmethod
    def _trim_output(text: str, *, limit: int = 600) -> str:
        cleaned = " ".join(text.split())
        if len(cleaned) <= limit:
            return cleaned
        return cleaned[: limit - 3] + "..."

    @staticmethod
    def _result_final_text(result: object) -> str:
        final_text = getattr(result, "final_text", None)
        if isinstance(final_text, str) and final_text.strip():
            return final_text.strip()
        assistant_response = getattr(result, "assistant_response", None)
        if isinstance(assistant_response, str) and assistant_response.strip():
            return assistant_response.strip()
        stdout = getattr(result, "stdout", None)
        if isinstance(stdout, str):
            return stdout.strip()
        return ""

    @staticmethod
    def _result_error_text(result: object) -> str:
        error_text = getattr(result, "error_text", None)
        if isinstance(error_text, str) and error_text.strip():
            return error_text.strip()
        stderr = getattr(result, "stderr", None)
        if isinstance(stderr, str) and stderr.strip():
            return stderr.strip()
        stdout = getattr(result, "stdout", None)
        if isinstance(stdout, str):
            return stdout.strip()
        return ""

    @staticmethod
    def _runtime_field(result: object, field_name: str) -> object | None:
        runtime = getattr(result, "runtime", None)
        if runtime is not None:
            value = getattr(runtime, field_name, None)
            if value is not None:
                return value
        return getattr(result, field_name, None)

    @staticmethod
    def _primary_pi_response(result: object) -> str:
        assistant_response = getattr(result, "assistant_response", "") or ""
        stripped = PersonalAgentDiscordBot._strip_raw_tokens(assistant_response)
        if stripped and not PersonalAgentDiscordBot._is_generic_pi_response(stripped):
            return PersonalAgentDiscordBot._trim_output(stripped, limit=900)
        for trace in reversed(list(getattr(result, "tool_traces", []) or [])):
            output = PersonalAgentDiscordBot._strip_raw_tokens(
                getattr(trace, "output", "") or ""
            )
            if output:
                return PersonalAgentDiscordBot._trim_output(output, limit=900)
        stdout = PersonalAgentDiscordBot._strip_raw_tokens(
            getattr(result, "stdout", "") or ""
        )
        return PersonalAgentDiscordBot._trim_output(stdout, limit=900)

    @staticmethod
    def _tool_argument_preview(arguments: object) -> tuple[str, str] | None:
        if not isinstance(arguments, dict) or not arguments:
            return None
        if isinstance(arguments.get("command"), str):
            return (
                "command",
                PersonalAgentDiscordBot._escape_inline_code(
                    PersonalAgentDiscordBot._trim_output(arguments["command"], limit=500)
                ),
            )
        return (
            "args",
            PersonalAgentDiscordBot._escape_inline_code(
                PersonalAgentDiscordBot._trim_output(
                    json.dumps(arguments, sort_keys=True),
                    limit=500,
                )
            ),
        )

    @staticmethod
    def _escape_inline_code(text: str) -> str:
        return text.replace("`", "'")

    @staticmethod
    def _repo_runtime_summary(result: object) -> str:
        parts = [f"`{PersonalAgentDiscordBot._runtime_field(result, 'sandbox_mode') or 'unknown'}`"]
        workspace_id = PersonalAgentDiscordBot._artifact_value(result, "workspace") or getattr(result, "workspace_id", None)
        branch_name = PersonalAgentDiscordBot._artifact_value(result, "branch") or getattr(result, "branch_name", None)
        if workspace_id:
            parts.append(f"workspace `{workspace_id}`")
        if branch_name:
            parts.append(f"branch `{branch_name}`")
        return " | ".join(parts)

    @staticmethod
    def _artifact_value(result: object, kind: str) -> str | None:
        for artifact in list(getattr(result, "artifacts", []) or []):
            if getattr(artifact, "kind", None) == kind:
                value = getattr(artifact, "value", None)
                if isinstance(value, str) and value:
                    return value
        return None

    @staticmethod
    def _is_generic_pi_response(text: str) -> bool:
        normalized = text.strip().lower().rstrip(".!")
        return normalized in {
            "done",
            "completed",
            "complete",
            "success",
            "successful",
        }

    def _discord_session_id(self, message: discord.Message) -> str:
        return self.agent_orchestrator_service.session_id_for_transport(
            transport="discord",
            conversation_id=str(message.channel.id),
            actor_id=str(message.author.id),
        )

    async def _handle_pi_prompt(self, *, message: discord.Message, prompt: str) -> None:
        cleaned_prompt = prompt.strip()
        if not cleaned_prompt:
            await message.channel.send("Send a prompt for Pi, or use `!code-reset` to clear the current chat.")
            return
        thinking_msg = await message.channel.send(
            f"-# \u2699\ufe0f Working on: *{self._trim_output(cleaned_prompt, limit=80)}*"
        )
        try:
            async with message.channel.typing():
                result = await self.agent_orchestrator_service.handle_message(
                    AgentMessageRequest(
                        prompt=cleaned_prompt,
                        transport="discord",
                        session_id=self._discord_session_id(message),
                        conversation_id=str(message.channel.id),
                        actor_id=str(message.author.id),
                        requested_by=str(message.author),
                    )
                )
        except Exception:
            await thinking_msg.delete()
            raise
        try:
            await thinking_msg.delete()
        except discord.HTTPException:
            pass
        for part in self.format_pi_chat_messages(result):
            await message.channel.send(part)

    async def send_digest_message(self, channel_key: str, message: str) -> None:
        channel_id = self.settings.channel_ids.get(channel_key)
        logger.info("Preparing Discord digest delivery for %s to channel_id=%s", channel_key, channel_id)
        if channel_id is None:
            logger.warning("No Discord channel configured for %s", channel_key)
            return
        channel = self.get_channel(channel_id)
        if channel is None:
            logger.warning("Discord channel %s not found in cache for %s", channel_id, channel_key)
            return
        message_parts = split_discord_message_content(message)
        logger.info(
            "Sending Discord digest to channel_id=%s name=%s guild=%s parts=%s",
            channel_id,
            getattr(channel, "name", "unknown"),
            getattr(getattr(channel, "guild", None), "name", None),
            len(message_parts),
        )
        for index, part in enumerate(message_parts, start=1):
            logger.info(
                "Sending Discord digest chunk %s/%s to channel_id=%s for %s length=%s",
                index,
                len(message_parts),
                channel_id,
                channel_key,
                len(part),
            )
            await channel.send(part)
        logger.info("Sent Discord digest to channel_id=%s for %s", channel_id, channel_key)

    async def on_ready(self) -> None:
        logger.info("Discord bot logged in as %s", self.user)
        logger.info(
            "Discord bot connected to guilds: %s",
            ", ".join(f"{guild.name}({guild.id})" for guild in self.guilds) or "none",
        )
        logger.info(
            "Discord configuration prefix=%r command_channel_id=%s summary_channel_id=%s interesting_channel_id=%s opportunities_channel_id=%s",
            self.settings.discord_command_prefix,
            self.settings.discord_command_channel_id,
            self.settings.discord_summary_channel_id,
            self.settings.discord_interesting_channel_id,
            self.settings.discord_opportunities_channel_id,
        )
        if self.settings.discord_command_channel_id is not None:
            command_channel = self.get_channel(self.settings.discord_command_channel_id)
            if command_channel is None:
                logger.warning(
                    "Configured Discord command channel %s was not found in cache during startup",
                    self.settings.discord_command_channel_id,
                )
            else:
                logger.info(
                    "Configured Discord command channel resolved in cache: id=%s name=%s guild=%s",
                    self.settings.discord_command_channel_id,
                    getattr(command_channel, "name", "unknown"),
                    getattr(getattr(command_channel, "guild", None), "name", None),
                )

    async def on_message(self, message: discord.Message) -> None:
        logger.info(
            "Received Discord message channel_id=%s guild_id=%s author=%s content=%r",
            message.channel.id,
            getattr(message.guild, "id", None),
            message.author,
            message.content,
        )
        if message.author == self.user:
            logger.info("Ignoring message from the bot itself in channel_id=%s", message.channel.id)
            return
        if self.settings.discord_command_channel_id and message.channel.id != self.settings.discord_command_channel_id:
            logger.info(
                "Ignoring message from channel_id=%s; expected command_channel_id=%s",
                message.channel.id,
                self.settings.discord_command_channel_id,
            )
            return
        context = await self.get_context(message)
        if context.valid:
            logger.info(
                "Recognized Discord command name=%s author=%s channel_id=%s",
                context.command.qualified_name if context.command is not None else context.invoked_with,
                message.author,
                message.channel.id,
            )
        else:
            logger.info(
                "Message did not match a registered command prefix=%r content=%r",
                self.settings.discord_command_prefix,
                message.content,
            )
            stripped_content = message.content.strip()
            if not stripped_content:
                return
            if stripped_content.startswith(self.settings.discord_command_prefix):
                return
            await self._handle_pi_prompt(message=message, prompt=stripped_content)
            return
        await self.invoke(context)
