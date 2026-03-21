# Personal Agent Status

## What is done

- Created the initial Python project scaffold with FastAPI, LangGraph, Discord bot integration points, scheduler support, configuration management, and SQLite persistence.
- Added a readable LangGraph orchestration flow for the Hacker News workflow with explicit nodes for fetching, deduplication, scoring, categorization, summarization, publishing, and persistence.
- Implemented Hacker News ingestion from top and new stories, with optional best stories support through configuration.
- Added heuristic scoring for summary, interesting, and opportunities channels to keep future LLM usage constrained to shortlisted posts.
- Added a Nebius-compatible OpenAI client provider for shortlist-only Hacker News summarization, with heuristic fallback when the API key or response is unavailable.
- Added Discord digest formatting and publishing abstractions, plus command handlers for `ping`, `hn`, and `status`.
- Added a FastAPI runtime with health, status, and manual Hacker News workflow trigger endpoints.
- Added a placeholder Blaxel execution provider abstraction for future remote sandbox execution.
- Added a small initial test suite for core scoring/categorization and FastAPI health checks.

## What is left

- Replace the placeholder summarizer with a real LLM-backed summarization provider and keep the shortlist-only invocation rule.
- Improve Discord delivery by handling channel cache misses more robustly and splitting oversized messages automatically.
- Add proper slash commands in addition to the prefix-based command interface.
- Add richer error handling, retries, telemetry, and run failure recording across the workflow nodes.
- Add a real Blaxel integration for sandboxed execution and hosting-specific deployment configuration.
- Decide whether SQLite is sufficient for the deployment environment or whether a persistent external database is needed.
- Expand test coverage to include the full Hacker News workflow, scheduler behavior, and Discord publishing behavior.

## Suggestions

1. Add a provider abstraction for summarization next, so the current heuristic summarizer can remain as a safe fallback.
2. Introduce repository-backed configuration for ranking keywords and domain weights once you start tuning the digests from real usage.
3. Keep the LangGraph orchestration layer explicit and documented in code comments as more workflows are added.
4. Consider separating the API process and long-running Discord bot worker if hosting constraints on Blaxel make a single combined process less reliable.
5. Add persistent observability early: structured logs, run IDs, and metrics around fetch counts, shortlisted counts, and publish outcomes.

#what else it did 

• Implemented the behavior you described.

  - The summary digest now rolls up all fresh HN titles into a short “what HN talked about” section, and the read list is
    the unique interesting shortlist, widened to a default of 12 and configurable up to 20. See /media/bhaves/Volume%202/
    personal-agent/src/personal_agent/hn/rollups.py:51, /media/bhaves/Volume%202/personal-agent/src/personal_agent/hn/
    summarizer.py:22, and /media/bhaves/Volume%202/personal-agent/src/personal_agent/hn/formatters.py:11.
  - I also changed persistence so every fetched story is marked seen after a run, not just the published ones, which keeps
    future digests unique instead of resurfacing leftovers. See /media/bhaves/Volume%202/personal-agent/src/personal_agent/
    graph/nodes/hn.py:72.
  - Scheduling defaults are now 6 hours for always-on deployments, and the app now honors Blaxel-style HOST/PORT. Webhook-
    based Discord publishing was added so scheduled jobs can post without a long-lived Discord gateway bot. See /media/
    bhaves/Volume%202/personal-agent/src/personal_agent/config/settings.py:24, /media/bhaves/Volume%202/personal-agent/src/
    personal_agent/discord/webhooks.py:13, and /media/bhaves/Volume%202/personal-agent/src/personal_agent/app.py:17.
  - For Blaxel, I added a one-shot job entrypoint plus copyable manifests and notes: /media/bhaves/Volume%202/personal-
    agent/src/personal_agent/jobs/hn_digest.py:13, /media/bhaves/Volume%202/personal-agent/deploy/blaxel/
    job.blaxel.toml.example:1, /media/bhaves/Volume%202/personal-agent/deploy/blaxel/agent.blaxel.toml.example:1, and /
    media/bhaves/Volume%202/personal-agent/docs/blaxel.md:3.

  How to run it:

  - If this is on an always-on box, set PERSONAL_AGENT_HN_POLL_HOURS=6 and keep using the internal scheduler.
  - If this is on Blaxel, use the job path, not APScheduler. Set PERSONAL_AGENT_INTERESTING_TOP_N=10 to 20, add the three
    PERSONAL_AGENT_DISCORD_*_WEBHOOK_URL secrets, copy the job example to blaxel.toml, and deploy it with bl deploy.
  - If you also want a manual HTTP trigger, deploy the agent example separately; the job should own the 6-hour cadence.

  Reasoning for Blaxel, based on current docs I checked on March 21, 2026:

  - Inference: Blaxel agents are request-driven serverless endpoints, so an in-process scheduler inside the API is not the
    reliable scheduler there. Use a Blaxel job with a cron trigger instead.
  - Docs: agent quickstart and host/port requirements: https://docs.blaxel.ai/Agents/Quickstart-agent
  - Docs: job deployment and cron triggers: https://docs.blaxel.ai/Jobs/Deploy-a-job
  - Docs: agent deployment behavior and runtime limits: https://docs.blaxel.ai/Agents/Deploy-an-agent
  - Docs: GitHub deployment currently from main: https://docs.blaxel.ai/Agents/Github-integration

  Verification: .venv/bin/pytest on the targeted suite passed with 17 passed, and I also checked /health successfully
  through ASGI transport.