from __future__ import annotations

from dataclasses import dataclass

from personal_agent.config.settings import Settings
from personal_agent.discord.bot import PersonalAgentDiscordBot
from personal_agent.discord.webhooks import DiscordWebhookSender
from personal_agent.graph.main import HNWorkflow
from personal_agent.graph.nodes.hn import HNWorkflowNodes
from personal_agent.hn.categorizer import StoryCategorizer
from personal_agent.hn.client import HackerNewsClient
from personal_agent.hn.fetcher import HNFetcher
from personal_agent.hn.formatters import DiscordDigestFormatter
from personal_agent.hn.publisher import DigestPublisher
from personal_agent.hn.scorer import StoryScorer
from personal_agent.hn.service import HNService
from personal_agent.hn.summarizer import StorySummarizer
from personal_agent.hn.summary_providers import (
    HeuristicStorySummaryProvider,
    NebiusStorySummaryProvider,
)
from personal_agent.scheduler.jobs import SchedulerService
from personal_agent.storage.db import Database
from personal_agent.storage.repositories import HNRunRepository, ProcessedStoryRepository


@dataclass(slots=True)
class ServiceContainer:
    """Central dependency wiring for the application runtime."""

    settings: Settings
    database: Database
    processed_story_repository: ProcessedStoryRepository
    run_repository: HNRunRepository
    hacker_news_client: HackerNewsClient
    hn_fetcher: HNFetcher
    story_scorer: StoryScorer
    story_categorizer: StoryCategorizer
    story_summarizer: StorySummarizer
    digest_formatter: DiscordDigestFormatter
    digest_publisher: DigestPublisher
    workflow_nodes: HNWorkflowNodes
    hn_workflow: HNWorkflow
    hn_service: HNService
    scheduler_service: SchedulerService
    discord_bot: PersonalAgentDiscordBot | None


def build_summary_provider(settings: Settings) -> HeuristicStorySummaryProvider | NebiusStorySummaryProvider:
    heuristic_provider = HeuristicStorySummaryProvider()
    if settings.llm_provider.lower() == "nebius":
        return NebiusStorySummaryProvider(
            model=settings.effective_llm_model or "moonshotai/Kimi-K2.5-fast",
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key_value,
            fallback_provider=heuristic_provider,
        )
    return heuristic_provider


def build_container(settings: Settings) -> ServiceContainer:
    database = Database(settings.database_path)
    database.initialize()

    processed_story_repository = ProcessedStoryRepository(database)
    run_repository = HNRunRepository(database)
    hacker_news_client = HackerNewsClient()
    hn_fetcher = HNFetcher(hacker_news_client, settings)
    story_scorer = StoryScorer()
    story_categorizer = StoryCategorizer(settings)
    summary_provider = build_summary_provider(settings)
    story_summarizer = StorySummarizer(
        summary_provider,
        summary_topic_count=settings.summary_topic_count,
    )
    digest_formatter = DiscordDigestFormatter()
    digest_publisher = DigestPublisher(digest_formatter)

    workflow_nodes = HNWorkflowNodes(
        fetcher=hn_fetcher,
        processed_story_repository=processed_story_repository,
        run_repository=run_repository,
        scorer=story_scorer,
        categorizer=story_categorizer,
        summarizer=story_summarizer,
        publisher=digest_publisher,
    )
    hn_workflow = HNWorkflow(workflow_nodes)
    hn_service = HNService(hn_workflow)
    scheduler_service = SchedulerService(settings, hn_service)

    webhook_sender = None
    if settings.discord_webhooks_enabled:
        webhook_sender = DiscordWebhookSender(settings.channel_webhook_urls)
        workflow_nodes.discord_sender = webhook_sender.send_digest_message

    discord_bot = None
    if settings.discord_enabled:
        discord_bot = PersonalAgentDiscordBot(
            settings=settings,
            hn_service=hn_service,
            run_repository=run_repository,
        )
        if webhook_sender is None:
            workflow_nodes.discord_sender = discord_bot.send_digest_message

    return ServiceContainer(
        settings=settings,
        database=database,
        processed_story_repository=processed_story_repository,
        run_repository=run_repository,
        hacker_news_client=hacker_news_client,
        hn_fetcher=hn_fetcher,
        story_scorer=story_scorer,
        story_categorizer=story_categorizer,
        story_summarizer=story_summarizer,
        digest_formatter=digest_formatter,
        digest_publisher=digest_publisher,
        workflow_nodes=workflow_nodes,
        hn_workflow=hn_workflow,
        hn_service=hn_service,
        scheduler_service=scheduler_service,
        discord_bot=discord_bot,
    )
