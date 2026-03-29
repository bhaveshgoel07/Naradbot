# Blaxel Deployment Notes

## Recommended split

- Use the FastAPI app when you want a manually triggerable HTTP endpoint or local development workflow.
- Use the one-shot job entrypoint when you want the Hacker News digest to run on a schedule.
- Use Blaxel sandboxes for isolated code execution sessions that need shell/file/process control.
- Use Blaxel MCP server hosting (`type = "function"`) for custom tool servers.
- Do not rely on the in-process APScheduler for Blaxel-hosted scheduling. Run `personal-agent-hn-once` from a Blaxel batch job instead.

## Why this split exists

- The API app includes a background scheduler for always-on environments.
- Blaxel agent hosting is a better fit for request-driven HTTP workloads.
- Blaxel sandboxes can scale to zero and resume quickly, making them good for reusable coding sessions.
- Scheduled digest delivery is more reliable as a job that runs once, publishes, and exits.

## Service mapping for this project

- **Primary API (`type = "agent"`):** host `personal-agent-api` and Discord command handling.
- **Pi orchestrator sandbox:** keep one named persistent sandbox warm for the Pi control plane and sandbox coordination metadata.
- **Execution sandboxes:** create a fresh sandbox per direct Pi code task, then delete it after completion.
- **Repo coding workflow:** keep one persistent sandbox per repo, then create one Git worktree per request and require explicit approval before push/PR.
- **Computer-use automation:** provision a dedicated sandbox template now and expose status/provision endpoints, but keep the action set empty until tasks are defined.
- **Custom tools / MCP:** deploy each MCP server on Blaxel Functions (`type = "function"`), then connect from the agent with HTTP MCP URLs.
- **Scheduled tasks:** deploy `personal-agent-hn-once` as a Blaxel Job (`type = "job"`), with cron triggers in `blaxel.toml`.

## Sandbox lifecycle guidance

- Use one persistent orchestrator sandbox for Pi runtime coordination.
- For untrusted user code, create a sandbox per task and delete it after completion.
- Reuse one persistent sandbox per repository, but isolate each request in its own Git worktree.
- Provision a separate computer-use sandbox so browser or desktop tooling stays isolated from coding sandboxes.
- Persist important artifacts on Volumes; sandbox root filesystem is fast but not your long-term durability layer.

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
- `PERSONAL_AGENT_OPPORTUNITY_EMBEDDING_ENABLED=true`
- `PERSONAL_AGENT_STORY_ANALYSIS_ENABLED=true`
- `PERSONAL_AGENT_LLM_PROVIDER=nebius`
- `PERSONAL_AGENT_LLM_API_KEY=...`
- `PERSONAL_AGENT_LLM_MODEL=NousResearch/Hermes-4-70B`
- `PERSONAL_AGENT_PI_COMMAND=pi`
- `PERSONAL_AGENT_PI_PROVIDER=nebius`
- `PERSONAL_AGENT_PI_MODEL=moonshotai/Kimi-K2.5-fast`
- `PERSONAL_AGENT_PI_BASE_URL=https://api.tokenfactory.us-central1.nebius.com/v1/`
- `PERSONAL_AGENT_PI_API_KEY=...` or `NEBIUS_API_KEY=...`
- `PERSONAL_AGENT_PI_GITHUB_TOKEN=...`
- `PERSONAL_AGENT_PI_WORKSPACE_ROOT=/tmp/personal-agent/pi`
- `PERSONAL_AGENT_BLAXEL_SANDBOXES_ENABLED=true`
- `PERSONAL_AGENT_BLAXEL_REGION=us-pdx-1`
- `PERSONAL_AGENT_BLAXEL_ORCHESTRATOR_SANDBOX_NAME=personal-agent-pi-orchestrator`
- `PERSONAL_AGENT_BLAXEL_ORCHESTRATOR_SANDBOX_IMAGE=personal-agent-pi-orchestrator-template`
- `PERSONAL_AGENT_BLAXEL_EXECUTION_SANDBOX_IMAGE=personal-agent-pi-workspace-template`
- `PERSONAL_AGENT_BLAXEL_REPO_SANDBOX_IMAGE=personal-agent-pi-workspace-template`
- `PERSONAL_AGENT_BLAXEL_COMPUTER_USE_SANDBOX_NAME=personal-agent-computer-use`
- `PERSONAL_AGENT_BLAXEL_COMPUTER_USE_SANDBOX_IMAGE=personal-agent-computer-use-template`
- `PERSONAL_AGENT_BLAXEL_COMPUTER_USE_PREVIEW_PORT=3000`

Notes:

- `BL_API_KEY` is for Blaxel SDK/control-plane access. Pi model calls still need a model-provider key such as `PERSONAL_AGENT_PI_API_KEY`, `PERSONAL_AGENT_LLM_API_KEY`, or `NEBIUS_API_KEY`.
- The checked-in Dockerfile preinstalls the `pi` CLI, so the Blaxel agent config should use `PERSONAL_AGENT_PI_COMMAND=pi` instead of `npx ...` in production.

## Commands

- Local API: `uv run personal-agent-api`
- Local one-shot digest: `uv run personal-agent-hn-once`
- Blaxel-compatible Pi prompt: `POST /` with `{"inputs":"..."}` 
- Manual API trigger: `POST /workflows/hacker-news/run`
- Direct Pi task in a throwaway sandbox: `POST /agents/pi/run`
- Sandboxed repo edit (approval-gated push): `POST /agents/pi/repos/run`
- Push approved repo workspace + open PR: `POST /agents/pi/repos/push`
- Computer-use sandbox status: `GET /automation/computer-use/status`
- Computer-use sandbox provision: `POST /automation/computer-use/provision`
- Example manifests: `deploy/blaxel/agent.blaxel.toml.example`, `deploy/blaxel/job.blaxel.toml.example`, and the sandbox templates under `deploy/blaxel/templates/`

## Build note

- The repo uses a `src/` layout, so Blaxel should run the packaged script entrypoints with `uv run ...` instead of `python -m personal_agent...`.
- The checked-in manifests now use `uv run personal-agent-api` and `uv run personal-agent-hn-once` for production entrypoints to avoid the import-path failure that happens when `src/` is not on `PYTHONPATH`.
- The checked-in Dockerfile avoids Blaxel's auto-generated Python build path, which was running `uv sync` before `src/` was copied into the image.

## Scheduling

- For a 6-hour cadence, use a cron like `0 */6 * * *`.
- For a 7-hour cadence, use `0 */7 * * *`.
- If you want stable times through the day, prefer 6 hours.
