from __future__ import annotations

from dataclasses import dataclass

from personal_agent.agent.service import AgentOrchestratorService
from personal_agent.automation.computer_use import ComputerUseService
from personal_agent.automation.job_apply import JobApplicationService
from personal_agent.automation.pi_agent import PiCodingAgentService
from personal_agent.config.settings import Settings
from personal_agent.discord.bot import PersonalAgentDiscordBot
from personal_agent.discord.webhooks import DiscordWebhookSender
from personal_agent.execution.blaxel import BlaxelSandboxService
from personal_agent.graph.main import HNWorkflow
from personal_agent.graph.nodes.hn import HNWorkflowNodes
from personal_agent.hn.categorizer import StoryCategorizer
from personal_agent.hn.client import HackerNewsClient
from personal_agent.hn.fetcher import HNFetcher
from personal_agent.hn.formatters import DiscordDigestFormatter
from personal_agent.hn.link_fetcher import LinkContentFetcher
from personal_agent.hn.opportunity_embeddings import NebiusOpportunityEmbedder
from personal_agent.hn.publisher import DigestPublisher
from personal_agent.hn.scorer import StoryScorer
from personal_agent.hn.service import HNService
from personal_agent.hn.story_analysis import NebiusStoryAnalysisProvider
from personal_agent.hn.summarizer import StorySummarizer
from personal_agent.hn.summary_providers import (
    HeuristicStorySummaryProvider,
    NebiusStorySummaryProvider,
)
from personal_agent.scheduler.jobs import SchedulerService
from personal_agent.storage.db import Database
from personal_agent.storage.repositories import (
    HNRunRepository,
    ProcessedStoryRepository,
)


@dataclass(slots=True)
class ServiceContainer:
    """Central dependency wiring for the application runtime."""

    settings: Settings
    database: Database
    processed_story_repository: ProcessedStoryRepository
    run_repository: HNRunRepository
    hacker_news_client: HackerNewsClient
    hn_fetcher: HNFetcher
    link_content_fetcher: LinkContentFetcher
    story_scorer: StoryScorer
    story_categorizer: StoryCategorizer
    story_summarizer: StorySummarizer
    digest_formatter: DiscordDigestFormatter
    digest_publisher: DigestPublisher
    workflow_nodes: HNWorkflowNodes
    hn_workflow: HNWorkflow
    hn_service: HNService
    scheduler_service: SchedulerService
    blaxel_sandbox_service: BlaxelSandboxService
    pi_coding_agent_service: PiCodingAgentService
    agent_orchestrator_service: AgentOrchestratorService
    computer_use_service: ComputerUseService
    job_application_service: JobApplicationService
    discord_bot: PersonalAgentDiscordBot | None


def build_summary_provider(
    settings: Settings,
) -> HeuristicStorySummaryProvider | NebiusStorySummaryProvider:
    heuristic_provider = HeuristicStorySummaryProvider()
    if settings.llm_provider.lower() == "nebius":
        return NebiusStorySummaryProvider(
            model=settings.summary_model_value or "NousResearch/Hermes-4-70B",
            base_url=settings.summary_base_url_value,
            api_key=settings.summary_api_key_value,
            fallback_provider=heuristic_provider,
        )
    return heuristic_provider


def build_opportunity_embedder(settings: Settings) -> NebiusOpportunityEmbedder | None:
    if not settings.opportunity_embedding_enabled:
        return None
    return NebiusOpportunityEmbedder(
        model=settings.opportunity_embedding_model,
        base_url=settings.opportunity_embedding_base_url,
        api_key=settings.opportunity_embedding_api_key_value,
    )


def build_story_analysis_provider(
    settings: Settings,
    *,
    link_content_fetcher: LinkContentFetcher,
) -> NebiusStoryAnalysisProvider | None:
    if not settings.story_analysis_enabled:
        return None
    if settings.story_analysis_model_value is None:
        return None
    return NebiusStoryAnalysisProvider(
        model=settings.story_analysis_model_value,
        base_url=settings.story_analysis_base_url_value,
        api_key=settings.story_analysis_api_key_value,
        link_fetcher=link_content_fetcher,
        concurrency_limit=settings.story_analysis_concurrency_limit,
        verify_links=settings.story_analysis_verify_links,
    )


def build_container(settings: Settings) -> ServiceContainer:
    database = Database(settings.database_path)
    database.initialize()

    processed_story_repository = ProcessedStoryRepository(database)
    run_repository = HNRunRepository(database)
    hacker_news_client = HackerNewsClient()
    hn_fetcher = HNFetcher(hacker_news_client, settings)
    link_content_fetcher = LinkContentFetcher(
        timeout_seconds=settings.story_analysis_link_timeout_seconds,
        char_limit=settings.story_analysis_link_char_limit,
    )

    opportunity_embedder = build_opportunity_embedder(settings)
    story_analysis_provider = build_story_analysis_provider(
        settings,
        link_content_fetcher=link_content_fetcher,
    )
    story_scorer = StoryScorer(
        opportunity_embedder=opportunity_embedder,
        story_analysis_provider=story_analysis_provider,
        job_post_queries=settings.opportunity_job_post_queries,
        non_job_keywords=settings.opportunity_non_job_queries,
        opportunity_min_similarity=settings.opportunity_min_similarity,
        opportunity_min_margin=settings.opportunity_min_margin,
    )

    story_categorizer = StoryCategorizer(settings)
    summary_provider = build_summary_provider(settings)
    story_summarizer = StorySummarizer(
        summary_provider,
        summary_topic_count=settings.summary_topic_count,
        concurrency_limit=settings.summary_concurrency_limit,
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
    blaxel_sandbox_service = BlaxelSandboxService(settings)
    pi_coding_agent_service = PiCodingAgentService(
        settings,
        sandbox_service=blaxel_sandbox_service,
    )
    agent_orchestrator_service = AgentOrchestratorService(
        settings=settings,
        pi_agent=pi_coding_agent_service,
    )
    computer_use_service = ComputerUseService(
        settings=settings,
        sandbox_service=blaxel_sandbox_service,
    )
    job_application_service = JobApplicationService(
        settings=settings,
        pi_agent=pi_coding_agent_service,
        link_fetcher=link_content_fetcher,
    )

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
            agent_orchestrator_service=agent_orchestrator_service,
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
        link_content_fetcher=link_content_fetcher,
        story_scorer=story_scorer,
        story_categorizer=story_categorizer,
        story_summarizer=story_summarizer,
        digest_formatter=digest_formatter,
        digest_publisher=digest_publisher,
        workflow_nodes=workflow_nodes,
        hn_workflow=hn_workflow,
        hn_service=hn_service,
        scheduler_service=scheduler_service,
        blaxel_sandbox_service=blaxel_sandbox_service,
        pi_coding_agent_service=pi_coding_agent_service,
        agent_orchestrator_service=agent_orchestrator_service,
        computer_use_service=computer_use_service,
        job_application_service=job_application_service,
        discord_bot=discord_bot,
    )
