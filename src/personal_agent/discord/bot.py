from __future__ import annotations

import logging
from typing import Any

import discord
from discord.ext import commands

from personal_agent.automation.models import PiRepositoryTaskRequest
from personal_agent.automation.pi_agent import PiCodingAgentService
from personal_agent.config.settings import Settings
from personal_agent.discord.messages import DISCORD_MESSAGE_CHAR_LIMIT, split_discord_message_content
from personal_agent.hn.service import HNService
from personal_agent.storage.repositories import HNRunRepository

logger = logging.getLogger(__name__)


class PersonalAgentDiscordBot(commands.Bot):
    """Discord bot that forwards commands into the application services."""

    def __init__(
        self,
        *,
        settings: Settings,
        hn_service: HNService,
        run_repository: HNRunRepository,
        pi_coding_agent_service: PiCodingAgentService,
    ) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(command_prefix=settings.discord_command_prefix, intents=intents)
        self.settings = settings
        self.hn_service = hn_service
        self.run_repository = run_repository
        self.pi_coding_agent_service = pi_coding_agent_service

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
                self.format_pi_status_message(self.pi_coding_agent_service.status())
            )

        @self.command(name="code")
        async def code(
            ctx: commands.Context[Any],
            repo_url: str,
            *,
            prompt: str,
        ) -> None:
            await ctx.typing()
            result = await self.pi_coding_agent_service.run_repository_task(
                PiRepositoryTaskRequest(
                    repo_url=repo_url,
                    prompt=prompt,
                    requested_by=str(ctx.author),
                )
            )
            await ctx.send(self.format_pi_repo_result_message(result))
            if result.pr_url is not None:
                await ctx.send(
                    self.format_pr_review_message(
                        pr_url=result.pr_url,
                        author_mention=ctx.author.mention,
                    )
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
            f"- sandbox: {status.get('sandbox_mode')}\n"
            f"- repo workflow: {status.get('repo_workflow_available')}\n"
            f"- workspace root: {status.get('workspace_root')}"
        )

    @staticmethod
    def format_pi_repo_result_message(result: object) -> str:
        pr_url = getattr(result, "pr_url", None)
        if pr_url:
            return (
                f"Pi finished in sandbox mode `{getattr(result, 'sandbox_mode', 'unknown')}` "
                f"and opened a pull request: {pr_url}"
            )
        if getattr(result, "exit_code", 0) != 0:
            details = PersonalAgentDiscordBot._trim_output(
                getattr(result, "stderr", "") or getattr(result, "stdout", "")
            )
            return (
                f"Pi failed with exit code {getattr(result, 'exit_code', 'unknown')}.\n"
                f"{details or 'No error output was returned.'}"
            )
        if not getattr(result, "changes_detected", False):
            return (
                "Pi finished in the isolated repo sandbox but did not leave file changes."
            )
        return (
            "Pi finished in the isolated repo sandbox and committed changes, "
            "but no pull request URL was created.\n"
            f"{PersonalAgentDiscordBot._trim_output(getattr(result, 'stderr', ''))}"
        )

    @staticmethod
    def format_pr_review_message(*, pr_url: str, author_mention: str) -> str:
        return f"{author_mention} review this pull request in command-center before merge: {pr_url}"

    @staticmethod
    def _trim_output(text: str, *, limit: int = 600) -> str:
        cleaned = " ".join(text.split())
        if len(cleaned) <= limit:
            return cleaned
        return cleaned[: limit - 3] + "..."

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
        await self.invoke(context)
