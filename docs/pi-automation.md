# Pi And Automation

## Pi setup

The app now exposes two Pi paths:

- `/agents/pi/run` for direct local workdir tasks.
- `/agents/pi/repos/run` for the isolated repo workflow that clones a repository into `/tmp/personal-agent/pi`, lets Pi edit it, commits the result, and opens a GitHub pull request when a token is configured.

Default Pi runtime:

- `PERSONAL_AGENT_PI_COMMAND="npx -y @mariozechner/pi-coding-agent"`
- `PERSONAL_AGENT_PI_PROVIDER=openai`
- `PERSONAL_AGENT_PI_MODEL=openai/gpt-5.4-mini`
- `PERSONAL_AGENT_PI_API_KEY=...`
- `PERSONAL_AGENT_PI_GITHUB_TOKEN=...`

Quick check:

```bash
curl http://localhost:8000/agents/pi/status
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

Discord command-center usage:

- Configure `PERSONAL_AGENT_DISCORD_COMMAND_CHANNEL_ID` to your command-center channel.
- Use `!pi-status` to inspect the configured model and sandbox mode.
- Use `!code <repo-url> <instruction>` to run the isolated clone/edit/PR workflow.
- When a PR is created, the bot posts a review request back into the same command-center channel.

Important behavior:

- The repo workflow does not attach local files to Pi.
- Pi runs inside a temp cloned workspace with a scrubbed `HOME`, config, and cache directory.
- This is not a Blaxel remote-execution sandbox today. It is an isolated temp workspace inside the deployed runtime.

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
