# Blaxel Deployment Notes

## Recommended split

- Use the FastAPI app when you want a manually triggerable HTTP endpoint or local development workflow.
- Use the one-shot job entrypoint when you want the Hacker News digest to run on a schedule.
- Do not rely on the in-process APScheduler for Blaxel-hosted scheduling. Run `personal-agent-hn-once` from a Blaxel batch job instead.

## Why this split exists

- The API app includes a background scheduler for always-on environments.
- Blaxel agent hosting is a better fit for request-driven HTTP workloads.
- Scheduled digest delivery is more reliable as a job that runs once, publishes, and exits.

## Discord delivery on Blaxel

- Prefer Discord webhooks for scheduled publishing.
- Configure one webhook per digest channel with:
  - `PERSONAL_AGENT_DISCORD_SUMMARY_WEBHOOK_URL`
  - `PERSONAL_AGENT_DISCORD_INTERESTING_WEBHOOK_URL`
  - `PERSONAL_AGENT_DISCORD_OPPORTUNITIES_WEBHOOK_URL`
- The long-lived Discord gateway bot is still useful for local or always-on deployments, but it should not be the only publish path for a Blaxel job.

## Useful environment variables

- `PERSONAL_AGENT_ENVIRONMENT=production`
- `PERSONAL_AGENT_HN_FETCH_LIMIT=60`
- `PERSONAL_AGENT_INTERESTING_TOP_N=12`
- `PERSONAL_AGENT_SUMMARY_TOPIC_COUNT=5`
- `PERSONAL_AGENT_LLM_PROVIDER=nebius`
- `PERSONAL_AGENT_LLM_API_KEY=...`
- `PERSONAL_AGENT_LLM_MODEL=moonshotai/Kimi-K2.5-fast`

## Commands

- Local API: `uv run personal-agent-api`
- Local one-shot digest: `uv run personal-agent-hn-once`
- Manual API trigger: `POST /workflows/hacker-news/run`
- Example manifests: `deploy/blaxel/agent.blaxel.toml.example` and `deploy/blaxel/job.blaxel.toml.example`

## Scheduling

- For a 6-hour cadence, use a cron like `0 */6 * * *`.
- For a 7-hour cadence, use `0 */7 * * *`.
- If you want stable times through the day, prefer 6 hours.
