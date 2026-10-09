"""Local web interface for the Software Factory (``factory ui``).

A thin presentation layer over ``FactoryService`` — the same facade the CLI
uses — so no workflow logic lives here. Pages are rendered on the server with
Jinja2; a few lines of plain JavaScript refresh the page while a background
action runs. The server binds to 127.0.0.1 only, and every form carries a
per-process token so other web pages cannot trigger factory actions.
"""

from __future__ import annotations

import secrets
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markdown_it import MarkdownIt

from software_factory import WORKFLOW_VERSION, __version__
from software_factory.core.doctor import run_doctor
from software_factory.core.factory_service import FactoryService, ProjectStatus
from software_factory.domain.errors import FactoryError, ProjectFolderMissingError
from software_factory.web.jobs import JobRunner

HERE = Path(__file__).parent
STAGE_LABELS = [
    ("requirements", "Requirements"),
    ("repository_analysis", "Repository analysis"),
    ("architecture", "Architecture"),
    ("technology", "Technology"),
    ("implementation_plan", "Plan"),
    ("development", "Coding & tests"),
    ("code_review", "Code review"),
    ("security_validation", "Security"),
    ("final_validation", "Final check"),
    ("release", "Release"),
]
APPROVAL_TITLES = {
    "requirements": "Review the requirements",
    "architecture": "Review the architecture",
    "technology": "Review the technology choice",
    "implementation_plan": "Review the plan",
    "change_request": "A change to the approved design was requested",
    "release": "Approve the release",
}
# Raw HTML in model-written Markdown is never rendered (html=False).
_markdown = MarkdownIt("commonmark", {"html": False, "linkify": False}).enable("table")


def render_markdown(text: str) -> str:
    return _markdown.render(text)


def create_app(service: FactoryService, runner: JobRunner | None = None) -> FastAPI:
    app = FastAPI(title="Software Factory", docs_url=None, redoc_url=None, openapi_url=None)
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    templates = Jinja2Templates(directory=HERE / "templates")
    templates.env.filters["markdown"] = render_markdown
    jobs = runner or JobRunner()
    token = secrets.token_urlsafe(24)

    def page(request: Request, template: str, **context: Any) -> HTMLResponse:
        return templates.TemplateResponse(
            request,
            template,
            {"csrf": token, "version": __version__, "workflow": WORKFLOW_VERSION, **context},
        )

    def check(csrf: str) -> None:
        if not secrets.compare_digest(csrf, token):
            raise HTTPException(status_code=403, detail="Invalid form token — reload the page")

    def back(project_id: str) -> RedirectResponse:
        return RedirectResponse(f"/p/{project_id}", status_code=303)

    def run(project_id: str, action: str, work: Any) -> RedirectResponse:
        jobs.clear_error(project_id)
        jobs.submit(project_id, action, work)
        return back(project_id)

    def status_or_404(project_id: str) -> ProjectStatus:
        try:
            return service.status(project_id)
        except FactoryError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    def activity(project_id: str, limit: int = 8) -> list[dict[str, Any]]:
        return list(reversed(service.history(project_id)[-limit:]))

    # -- pages ------------------------------------------------------------------

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request) -> HTMLResponse:
        projects = service.list_projects()
        running = {p.project_id for p in projects if jobs.is_running(p.project_id)}
        return page(request, "index.html", projects=projects, running=running)

    @app.get("/new", response_class=HTMLResponse)
    def new_form(request: Request) -> HTMLResponse:
        return page(request, "new.html", error=None, form={})

    @app.post("/new")
    def create(
        request: Request,
        csrf: str = Form(...),
        name: str = Form(...),
        requirements: str = Form(...),
        repository: str = Form(""),
        start: str = Form(""),
    ) -> Response:
        check(csrf)
        try:
            project = service.create_project(
                name.strip(), requirements, repository_path=repository.strip() or None
            )
        except FactoryError as error:
            form = {"name": name, "requirements": requirements, "repository": repository}
            return page(request, "new.html", error=error, form=form)
        if start:
            return run(project.project_id, "Starting", lambda: service.start(project.project_id))
        return back(project.project_id)

    @app.get("/p/{project_id}", response_class=HTMLResponse)
    def project_page(request: Request, project_id: str) -> HTMLResponse:
        try:
            status = service.status(project_id)
        except ProjectFolderMissingError as error:
            return page(request, "missing.html", p=service.projects.load(project_id), error=error)
        except FactoryError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        pending_questions, answers = service.questions(project_id)
        approval = status.interrupt if status.waiting_for == "approval" else None
        artifact_html = None
        if approval and approval.get("artifact") in service.documents(project_id):
            artifact_html = render_markdown(service.read_document(project_id, approval["artifact"]))
        completed_html = None
        if status.lifecycle_status == "COMPLETED" and "release-notes.md" in service.documents(
            project_id
        ):
            completed_html = render_markdown(service.read_document(project_id, "release-notes.md"))
        requirements_input = service.projects.requirements_input(project_id)
        return page(
            request,
            "project.html",
            s=status,
            p=status.project,
            running=jobs.is_running(project_id),
            job=jobs.last(project_id),
            stages=STAGE_LABELS,
            questions=pending_questions,
            answers=answers,
            approval=approval,
            approval_title=APPROVAL_TITLES.get((approval or {}).get("stage", ""), "Approve"),
            artifact_html=artifact_html,
            completed_html=completed_html,
            documents=service.documents(project_id),
            activity=activity(project_id),
            requirements_input=requirements_input,
        )

    @app.get("/p/{project_id}/status.json")
    def status_json(project_id: str) -> JSONResponse:
        status = status_or_404(project_id)
        job = jobs.last(project_id)
        latest = activity(project_id, 1)
        return JSONResponse(
            {
                "lifecycle_status": status.lifecycle_status,
                "running": jobs.is_running(project_id),
                "action": job.action if job else None,
                "activity": latest[0]["summary"] if latest else "",
                "completed_stages": status.completed_stages,
            }
        )

    @app.get("/p/{project_id}/doc/{name}", response_class=HTMLResponse)
    def document(
        request: Request, project_id: str, name: str, version: int | None = None
    ) -> HTMLResponse:
        status = status_or_404(project_id)
        try:
            text = service.read_document(project_id, name, version)
        except FactoryError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        return page(
            request,
            "document.html",
            p=status.project,
            name=name,
            html=render_markdown(text),
            documents=service.documents(project_id, version),
            doc_version=version,
        )

    @app.get("/p/{project_id}/history", response_class=HTMLResponse)
    def history(request: Request, project_id: str) -> HTMLResponse:
        status = status_or_404(project_id)
        return page(
            request,
            "history.html",
            p=status.project,
            entries=list(reversed(service.history(project_id))),
        )

    @app.get("/p/{project_id}/download")
    def download(project_id: str) -> Response:
        status_or_404(project_id)
        try:
            filename, data = service.source_archive(project_id)
        except FactoryError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        return Response(
            data,
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @app.get("/doctor", response_class=HTMLResponse)
    def doctor(request: Request, probe: int = 0) -> HTMLResponse:
        results = run_doctor(service.config, probe=bool(probe))
        return page(request, "doctor.html", results=results, probe=bool(probe))

    # -- actions ----------------------------------------------------------------

    @app.post("/p/{project_id}/start")
    def start(project_id: str, csrf: str = Form(...), requirements: str = Form("")) -> Response:
        check(csrf)
        status_or_404(project_id)
        if requirements.strip() and not service.status(project_id).started:
            service.set_requirements(project_id, requirements)
        return run(project_id, "Starting", lambda: service.start(project_id))

    @app.post("/p/{project_id}/continue")
    def continue_(project_id: str, csrf: str = Form(...)) -> Response:
        check(csrf)
        status_or_404(project_id)
        return run(project_id, "Continuing", lambda: service.continue_(project_id))

    @app.post("/p/{project_id}/answers")
    async def answers(request: Request, project_id: str) -> Response:
        form = await request.form()
        check(str(form.get("csrf", "")))
        status_or_404(project_id)
        errors = []
        for key, value in form.items():
            if key.startswith("answer_") and str(value).strip():
                try:
                    service.answer(project_id, key.removeprefix("answer_"), str(value))
                except FactoryError as error:
                    errors.append(str(error))
        if errors:
            raise HTTPException(status_code=400, detail="; ".join(errors))
        return run(project_id, "Using your answers", lambda: service.continue_(project_id))

    @app.post("/p/{project_id}/approve")
    def approve(
        project_id: str, csrf: str = Form(...), stage: str = Form(""), comment: str = Form("")
    ) -> Response:
        check(csrf)
        status_or_404(project_id)
        return run(
            project_id,
            "Approved — continuing",
            lambda: service.approve(project_id, stage or None, comment),
        )

    @app.post("/p/{project_id}/reject")
    def reject(
        project_id: str, csrf: str = Form(...), stage: str = Form(""), reason: str = Form(...)
    ) -> Response:
        check(csrf)
        status_or_404(project_id)
        return run(
            project_id,
            "Rejected — revising",
            lambda: service.reject(project_id, reason, stage or None),
        )

    @app.post("/p/{project_id}/update")
    def update(project_id: str, csrf: str = Form(...), change: str = Form(...)) -> Response:
        check(csrf)
        status_or_404(project_id)
        return run(project_id, "Starting the update", lambda: service.update(project_id, change))

    @app.post("/p/{project_id}/retry")
    def retry(project_id: str, csrf: str = Form(...), hint: str = Form("")) -> Response:
        check(csrf)
        status_or_404(project_id)
        return run(project_id, "Retrying", lambda: service.retry(project_id, hint))

    @app.post("/p/{project_id}/cancel")
    def cancel(project_id: str, csrf: str = Form(...)) -> Response:
        check(csrf)
        status_or_404(project_id)
        return run(project_id, "Cancelling", lambda: service.abort(project_id))

    @app.post("/p/{project_id}/delete")
    def delete(project_id: str, csrf: str = Form(...)) -> Response:
        check(csrf)
        if jobs.is_running(project_id):
            raise HTTPException(status_code=409, detail="The project is working — pause it first")
        try:
            service.delete_project(project_id)
        except FactoryError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        return RedirectResponse("/", status_code=303)

    @app.post("/p/{project_id}/pause")
    def pause(project_id: str, csrf: str = Form(...)) -> Response:
        check(csrf)
        status_or_404(project_id)
        service.pause(project_id)
        return back(project_id)

    return app
