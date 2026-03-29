from __future__ import annotations

import asyncio
import json
import shlex
from dataclasses import asdict, dataclass
from pathlib import Path

from personal_agent.automation.models import (
    CandidateProfile,
    JobApplicationRequest,
    JobApplicationResult,
    PiTaskRequest,
)
from personal_agent.automation.pi_agent import PiCodingAgentService
from personal_agent.config.settings import Settings
from personal_agent.hn.link_fetcher import LinkContentFetcher


@dataclass(slots=True)
class JobApplicationService:
    """Orchestrates resume-backed job application planning and automation hooks."""

    settings: Settings
    pi_agent: PiCodingAgentService
    link_fetcher: LinkContentFetcher

    def candidate_profile(self) -> CandidateProfile:
        return CandidateProfile(
            full_name=self.settings.candidate_full_name,
            email=self.settings.candidate_email,
            phone=self.settings.candidate_phone,
            location=self.settings.candidate_location,
            linkedin_url=self.settings.candidate_linkedin_url,
            github_url=self.settings.candidate_github_url,
            portfolio_url=self.settings.candidate_portfolio_url,
            resume_path=self.settings.candidate_resume_path,
            cover_letter_path=self.settings.candidate_cover_letter_path,
            extra_notes=self.settings.candidate_extra_notes,
        )

    def profile_status(self) -> dict[str, object]:
        profile = self.candidate_profile()
        resume_exists = bool(
            profile.resume_path and Path(profile.resume_path).expanduser().exists()
        )
        cover_letter_exists = bool(
            profile.cover_letter_path
            and Path(profile.cover_letter_path).expanduser().exists()
        )
        missing_fields = self._missing_fields(profile)
        return {
            "profile": asdict(profile),
            "missing_fields": missing_fields,
            "resume_exists": resume_exists,
            "cover_letter_exists": cover_letter_exists,
            "computer_use_command_configured": bool(self.settings.computer_use_command),
        }

    async def apply_to_job(
        self, request: JobApplicationRequest
    ) -> JobApplicationResult:
        profile = self.candidate_profile()
        missing_fields = self._missing_fields(profile)
        fit_summary = await self._build_fit_summary(request, profile)

        automation_result: dict[str, object] | None = None
        if self.settings.computer_use_command:
            automation_result = await self._run_computer_use_command(
                request,
                profile=profile,
                fit_summary=fit_summary,
            )

        status = "planned"
        if automation_result is not None:
            status = "submitted" if request.submit else "prepared"
        elif missing_fields:
            status = "needs_profile"

        return JobApplicationResult(
            status=status,
            fit_summary=fit_summary,
            automation_result=automation_result,
            profile_missing_fields=missing_fields,
        )

    async def _build_fit_summary(
        self,
        request: JobApplicationRequest,
        profile: CandidateProfile,
    ) -> str:
        files: list[str] = []
        if profile.resume_path and Path(profile.resume_path).expanduser().exists():
            files.append(str(Path(profile.resume_path).expanduser()))
        if profile.cover_letter_path and Path(profile.cover_letter_path).expanduser().exists():
            files.append(str(Path(profile.cover_letter_path).expanduser()))

        link_snapshot = await self.link_fetcher.fetch(request.job_url)
        job_excerpt = link_snapshot.excerpt if link_snapshot is not None else ""
        role_title = request.role_title or "Unknown role"
        company_name = request.company_name or "Unknown company"

        try:
            pi_result = await self.pi_agent.run_task(
                PiTaskRequest(
                    prompt=(
                        f"Summarize fit for this job in 4 short bullet-style sentences. "
                        f"Job URL: {request.job_url}. Company: {company_name}. Role: {role_title}. "
                        f"Candidate notes: {profile.extra_notes or 'none'}. "
                        f"User notes: {request.notes or 'none'}. "
                        f"Job excerpt: {job_excerpt[:2500] or 'No excerpt fetched.'}"
                    ),
                    files=files,
                    append_system_prompt=(
                        "You help prepare job applications. Be specific about fit, gaps, and what should be customized."
                    ),
                    timeout_seconds=min(self.settings.job_application_timeout_seconds, 300),
                )
            )
        except Exception as exc:  # noqa: BLE001
            return f"Unable to generate Pi fit summary: {exc}"

        if pi_result.exit_code != 0:
            return (
                "Pi fit summary failed. "
                + (pi_result.stderr.strip() or pi_result.stdout.strip() or "No error output.")
            )
        return pi_result.stdout.strip() or "Pi returned an empty fit summary."

    async def _run_computer_use_command(
        self,
        request: JobApplicationRequest,
        *,
        profile: CandidateProfile,
        fit_summary: str,
    ) -> dict[str, object]:
        command = shlex.split(self.settings.computer_use_command or "")
        payload = {
            "job_url": request.job_url,
            "company_name": request.company_name,
            "role_title": request.role_title,
            "notes": request.notes,
            "submit": request.submit,
            "fit_summary": fit_summary,
            "candidate_profile": asdict(profile),
        }
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            return {
                "command": command,
                "exit_code": 127,
                "stdout": "",
                "stderr": str(exc),
            }

        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(json.dumps(payload).encode("utf-8")),
                timeout=self.settings.job_application_timeout_seconds,
            )
        except TimeoutError:
            process.kill()
            await process.wait()
            return {
                "command": command,
                "exit_code": 124,
                "stdout": "",
                "stderr": "Computer-use command timed out.",
            }
        return {
            "command": command,
            "exit_code": process.returncode or 0,
            "stdout": stdout.decode("utf-8", errors="replace"),
            "stderr": stderr.decode("utf-8", errors="replace"),
        }

    @staticmethod
    def _missing_fields(profile: CandidateProfile) -> list[str]:
        required_fields = {
            "full_name": profile.full_name,
            "email": profile.email,
            "resume_path": profile.resume_path,
        }
        missing = [field for field, value in required_fields.items() if not value]
        if profile.resume_path and not Path(profile.resume_path).expanduser().exists():
            missing.append("resume_path_exists")
        return missing
