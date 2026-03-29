# Pi And Automation

## Pi setup

The app now exposes two Pi paths:

- `/agents/pi/run` for direct prompt-driven coding tasks that run in a fresh Blaxel execution sandbox and are deleted afterward.
- `/agents/pi/repos/run` for the per-repo persistent sandbox workflow. Each request gets its own Git worktree inside the repo sandbox, Pi edits there, and the result is committed in that sandbox-backed workspace.
- `/agents/pi/repos/push` for the explicit approval step that pushes a prepared sandbox branch and opens a GitHub pull request.
- `/automation/computer-use/status` and `/automation/computer-use/provision` for the currently empty computer-use sandbox contract.

Default Pi runtime:

- `PERSONAL_AGENT_PI_COMMAND="npx -y @mariozechner/pi-coding-agent"`
- `PERSONAL_AGENT_PI_PROVIDER=nebius`
- `PERSONAL_AGENT_PI_MODEL=moonshotai/Kimi-K2.5-fast`
- `PERSONAL_AGENT_PI_BASE_URL=https://api.tokenfactory.us-central1.nebius.com/v1/`
- `PERSONAL_AGENT_PI_API_KEY=...` or `PERSONAL_AGENT_LLM_API_KEY=...`
- `PERSONAL_AGENT_PI_GITHUB_TOKEN=...`

Quick check:

```bash
curl http://localhost:8000/agents/pi/status
```

Blaxel-compatible inference endpoint:

```bash
curl -X POST http://localhost:8000/ \
  -H "content-type: application/json" \
  -d '{
    "inputs": "Inspect the repository and summarize the current API surface."
  }'
```

Sandboxed repo run:

```bash
curl -X POST http://localhost:8000/agents/pi/repos/run \
  -H "content-type: application/json" \
  -d '{
    "repo_url": "https://github.com/example/repo",
    "prompt": "Fix the failing test, run the relevant checks, and prepare a pull request."
  }'
```

Approve push + open PR for a prepared workspace:

```bash
curl -X POST http://localhost:8000/agents/pi/repos/push \
  -H "content-type: application/json" \
  -d '{
    "workspace_id": "personal-agent-repo-example__ws-20260328123456-fix-tests"
  }'
```

Discord command-center usage:

- Configure `PERSONAL_AGENT_DISCORD_COMMAND_CHANNEL_ID` to your command-center channel.
- Use `!pi-status` to inspect the configured model and sandbox mode.
- Use `!code <instruction>` for direct code writing/execution tasks.
- Use `!repo <repo-url> <instruction>` to prepare repo changes in an isolated workspace (no push yet).
- Use `!repo-push <workspace-id>` to approve push and open the PR.
- When a PR is created, the bot posts a review request back into the same command-center channel.

Important behavior:

- The repo workflow does not attach local files to Pi.
- Direct Pi runs create a short-lived execution sandbox after ensuring the persistent orchestrator sandbox exists.
- Repo edits run inside a persistent per-repo sandbox and use unique worktrees as `workspace_id` values for later approval.
- Pi runs inside sandbox-local `HOME`, config, cache, and temp directories instead of the API host filesystem.
- Repo pushes are approval-gated by default (`allow_push=false` on `/agents/pi/repos/run`).
- The root `POST /` endpoint accepts Blaxel-style `{"inputs": ...}` payloads and forwards them to Pi.
- The computer-use sandbox is provisionable now, but it intentionally has no enabled actions yet.

## HN small-model analysis

Enable semantic opportunity validation plus small-model scoring and summaries with:

- `PERSONAL_AGENT_OPPORTUNITY_EMBEDDING_ENABLED=true`
- `PERSONAL_AGENT_STORY_ANALYSIS_ENABLED=true`
- `PERSONAL_AGENT_LLM_PROVIDER=nebius`
- `PERSONAL_AGENT_SMALL_LLM_MODEL=NousResearch/Hermes-4-70B`
- `NEBIUS_API_KEY=...`

The Hacker News pipeline now:

1. validates opportunities against semantic job-post queries instead of the old keyword-only matching path,
2. uses `NousResearch/Hermes-4-70B` as the default Nebius chat model for story scoring and shortlist summaries,
3. fetches linked pages when possible,
4. records whether the summary was verified against that link.

## Job application automation

Profile settings:

- `PERSONAL_AGENT_CANDIDATE_FULL_NAME`
- `PERSONAL_AGENT_CANDIDATE_EMAIL`
- `PERSONAL_AGENT_CANDIDATE_PHONE`
- `PERSONAL_AGENT_CANDIDATE_LOCATION`
- `PERSONAL_AGENT_CANDIDATE_LINKEDIN_URL`
- `PERSONAL_AGENT_CANDIDATE_GITHUB_URL`
- `PERSONAL_AGENT_CANDIDATE_PORTFOLIO_URL`
- `PERSONAL_AGENT_CANDIDATE_RESUME_PATH`
- `PERSONAL_AGENT_CANDIDATE_COVER_LETTER_PATH`

Optional computer-use hook:

- `PERSONAL_AGENT_COMPUTER_USE_COMMAND="/path/to/browser-driver"`

If that command is configured, `/automation/jobs/apply` sends JSON over stdin with:

- job URL and metadata,
- submit intent,
- the resolved candidate profile,
- a Pi-generated fit summary.

Example:

```bash
curl -X POST http://localhost:8000/automation/jobs/apply \
  -H "content-type: application/json" \
  -d '{
    "job_url": "https://jobs.example.com/backend",
    "company_name": "Example",
    "role_title": "Backend Engineer",
    "submit": false
  }'
```
