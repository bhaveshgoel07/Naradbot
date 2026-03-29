# Personal Agent Workspace Notes

## Project Shape

- Python 3.11+ app using FastAPI, LangGraph, SQLite, and Discord integrations.
- Main package lives in `src/personal_agent/`.
- Tests live in `tests/`.

## Useful Commands

- `uv run pytest`
- `uv run personal-agent-api`
- `uv run personal-agent-hn-once`

## Hacker News Workflow

- The HN pipeline fetches stories, scores them, categorizes them, summarizes them, publishes digests, and persists run details.
- Opportunity posts can be validated with embeddings.
- Optional all-story LLM analysis can score, summarize, and verify stories against linked pages before digest generation.

## Pi Coding Agent

- Pi can be invoked through the API at `/agents/pi/status` and `/agents/pi/run`.
- Default command is `npx -y @mariozechner/pi-coding-agent`, but you can override it with `PERSONAL_AGENT_PI_COMMAND`.
- The default non-interactive tool set is `read,bash,edit,write,grep,find,ls`.

## Job Automation

- Candidate profile data is read from settings like `PERSONAL_AGENT_CANDIDATE_FULL_NAME`, `PERSONAL_AGENT_CANDIDATE_EMAIL`, and `PERSONAL_AGENT_CANDIDATE_RESUME_PATH`.
- The API exposes `/automation/profile` and `/automation/jobs/apply`.
- If `PERSONAL_AGENT_COMPUTER_USE_COMMAND` is configured, the app will send job-application payloads to that command over stdin as JSON.
