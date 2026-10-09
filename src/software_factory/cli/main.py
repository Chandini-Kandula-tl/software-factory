"""``factory`` command-line interface.

Thin presentation layer: parses arguments, calls ``FactoryService``, prints
results. No workflow decisions are made here.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console
from rich.table import Table

from software_factory import WORKFLOW_VERSION, __version__
from software_factory.config.settings import load_config
from software_factory.core.doctor import CheckStatus, run_doctor
from software_factory.core.factory_service import FactoryService, ProjectStatus
from software_factory.domain.errors import FactoryError

app = typer.Typer(
    help="AI Software Factory — drive a software idea through a controlled lifecycle.",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()
err_console = Console(stderr=True)

ConfigOption = Annotated[
    Path | None,
    typer.Option(
        "--config", "-c", help="Config file (default: $FACTORY_CONFIG or ./config/factory.yaml)"
    ),
]
ProjectArg = Annotated[str, typer.Argument(help="Project id (see `factory list`)")]

_state: dict[str, Any] = {"config_path": None}


@app.callback()
def main(config: ConfigOption = None) -> None:
    _state["config_path"] = config


def _service() -> FactoryService:
    return FactoryService(load_config(_state["config_path"]))


def _fail(error: FactoryError) -> None:
    err_console.print(f"[bold red]Error:[/] {error}")
    if error.hint:
        err_console.print(f"[yellow]Hint:[/] {error.hint}")
    raise typer.Exit(code=1)


def _run(action):
    try:
        return action()
    except FactoryError as error:
        _fail(error)
    except KeyboardInterrupt:
        err_console.print(
            "[yellow]Interrupted.[/] Progress up to the last completed step is checkpointed; "
            "run `factory continue <project>` to resume."
        )
        raise typer.Exit(code=130) from None


def _read_requirements(text: str | None, file: Path | None) -> str | None:
    if text and file:
        raise FactoryError("Use either --requirements or --requirements-file, not both")
    if file:
        if not file.exists():
            raise FactoryError(f"Requirements file not found: {file}")
        return file.read_text(encoding="utf-8")
    return text


def _print_status(status: ProjectStatus, service: FactoryService) -> None:
    project = status.project
    table = Table(show_header=False, box=None, pad_edge=False)
    table.add_row("Project", f"{project.name} ([cyan]{project.project_id}[/])")
    table.add_row("Mode", project.mode.value)
    table.add_row(
        "Status", f"[bold]{status.lifecycle_status}[/]" + (" (paused)" if project.paused else "")
    )
    if status.version > 1 or status.versions:
        change = (status.update_request or {}).get("change", "")
        table.add_row("Version", f"{status.version}" + (f" · change: {change}" if change else ""))
    table.add_row("Completed", ", ".join(status.completed_stages) or "—")
    if status.development:
        done = [t["task_id"] for t in status.development.get("completed_tasks", [])]
        queue = status.development.get("task_queue", [])
        table.add_row(
            "Tasks",
            f"{len(done)}/{len(done) + len(queue)} done"
            + (f" · next: {queue[0]}" if queue else "")
            + f" · code: {status.development.get('source_path', '—')}",
        )
    table.add_row("Next action", status.pending_action)
    table.add_row("Artifacts", str(service.projects.project_dir(project.project_id)))
    console.print(table)
    if status.stopped_by_pause:
        console.print("[yellow]Run stopped because the project is paused.[/]")
    if status.note:
        console.print(f"[dim]{status.note}[/]")
    if status.failure:
        console.print(
            f"[red]Failed in {status.failure.get('stage')}:[/] {status.failure.get('message')}"
        )
        if status.failure.get("hint"):
            console.print(f"[yellow]Hint:[/] {status.failure['hint']}")
    if status.waiting_for == "clarification" and status.interrupt:
        _print_questions(status.interrupt["questions"], {})
    if (
        status.waiting_for == "approval"
        and status.interrupt
        and status.interrupt.get("stage") == "implementation_plan"
        and status.plan_checks
    ):
        console.print("[bold yellow]⚠ The plan's commands cannot run on this machine:[/]")
        for problem in status.plan_checks:
            console.print(f"  - {problem}")
        console.print("  Reject the plan with what to change, or install the programs first.")
    if status.waiting_for == "approval" and status.interrupt:
        artifact = service.projects.project_dir(project.project_id) / status.interrupt["artifact"]
        console.print(
            f"\nReview [bold]{artifact}[/] (revision {status.interrupt['revision']}), then:\n"
            f"  factory approve {project.project_id}\n"
            f'  factory reject {project.project_id} --reason "what should change"'
        )


def _print_questions(questions: list[dict[str, Any]], answers: dict[str, Any]) -> None:
    table = Table(title="Clarification questions")
    for column in ("Id", "Required", "Area", "Question", "Answer"):
        table.add_column(column, overflow="fold")
    for q in questions:
        table.add_row(
            q["id"],
            "yes" if q.get("required", True) else "no",
            q.get("impact_area", ""),
            q["question"],
            (answers.get(q["id"]) or {}).get("answer", "—"),
        )
    console.print(table)


# -- project commands -----------------------------------------------------------


@app.command()
def create(
    name: Annotated[str, typer.Argument(help="Project name")],
    requirements: Annotated[
        str | None, typer.Option("--requirements", "-r", help="Requirements text")
    ] = None,
    requirements_file: Annotated[Path | None, typer.Option("--requirements-file", "-f")] = None,
    description: Annotated[str, typer.Option("--description", "-d")] = "",
    repository: Annotated[
        Path | None, typer.Option("--repository", help="Existing repository to work on")
    ] = None,
) -> None:
    """Create an isolated project."""

    def action() -> None:
        service = _service()
        text = _read_requirements(requirements, requirements_file)
        project = service.create_project(
            name, text, description, str(repository) if repository else None
        )
        console.print(f"Created project [cyan]{project.project_id}[/] ({project.mode.value}).")
        if text:
            console.print(f"Next: factory start {project.project_id}")
        else:
            console.print(f"Next: factory requirements {project.project_id} --file requirements.md")

    _run(action)


@app.command("requirements")
def set_requirements(
    project: ProjectArg,
    text: Annotated[str | None, typer.Option("--text", "-t")] = None,
    file: Annotated[Path | None, typer.Option("--file", "-f")] = None,
) -> None:
    """Provide or replace requirements before the workflow starts."""

    def action() -> None:
        content = _read_requirements(text, file)
        if not content:
            raise FactoryError("Provide --text or --file")
        _service().set_requirements(project, content)
        console.print(f"Requirements saved. Next: factory start {project}")

    _run(action)


@app.command("list")
def list_projects() -> None:
    """List projects."""

    def action() -> None:
        table = Table("Id", "Name", "Mode", "Status", "Created")
        for p in _service().list_projects():
            table.add_row(p.project_id, p.name, p.mode.value, p.lifecycle_status, p.created_at)
        console.print(table)

    _run(action)


# -- workflow commands ----------------------------------------------------------


def _workflow_command(fn) -> None:
    def action() -> None:
        service = _service()
        with console.status("Working… (local models can take a few minutes per stage)"):
            status = fn(service)
        _print_status(status, service)

    _run(action)


@app.command()
def start(project: ProjectArg) -> None:
    """Start the workflow for a project."""
    _workflow_command(lambda s: s.start(project))


@app.command("continue")
def continue_(project: ProjectArg) -> None:
    """Resume a project from its persisted state."""
    _workflow_command(lambda s: s.continue_(project))


@app.command()
def status(project: ProjectArg) -> None:
    """Show where a project is and what it is waiting for."""

    def action() -> None:
        service = _service()
        _print_status(service.status(project), service)

    _run(action)


@app.command()
def pause(project: ProjectArg) -> None:
    """Pause a project (a running workflow stops after its current step)."""

    def action() -> None:
        service = _service()
        _print_status(service.pause(project), service)

    _run(action)


@app.command()
def approve(
    project: ProjectArg,
    stage: Annotated[str | None, typer.Option(help="Guard: expected stage being approved")] = None,
    comment: Annotated[str, typer.Option(help="Optional approval comment")] = "",
) -> None:
    """Approve the artifact the project is waiting on and continue."""
    _workflow_command(lambda s: s.approve(project, stage, comment))


@app.command()
def reject(
    project: ProjectArg,
    reason: Annotated[str, typer.Option("--reason", help="What should change")],
    stage: Annotated[str | None, typer.Option(help="Guard: expected stage being rejected")] = None,
) -> None:
    """Reject the pending artifact with a reason; the agent revises it."""
    _workflow_command(lambda s: s.reject(project, reason, stage))


@app.command()
def questions(project: ProjectArg) -> None:
    """Show pending clarification questions and saved answers."""

    def action() -> None:
        pending, answers = _service().questions(project)
        if not pending:
            console.print("No clarification questions are pending.")
            return
        _print_questions([q.model_dump(mode="json") for q in pending], answers)

    _run(action)


@app.command()
def answer(
    project: ProjectArg,
    question_id: Annotated[str, typer.Argument(help="Question id, e.g. Q1")],
    text: Annotated[str, typer.Argument(help="Your answer")],
) -> None:
    """Answer a clarification question (saved immediately)."""

    def action() -> None:
        remaining = _service().answer(project, question_id, text)
        if remaining:
            console.print(f"Saved. Required questions still open: {', '.join(remaining)}")
        else:
            console.print(
                f"Saved. All required questions answered — run `factory continue {project}`."
            )

    _run(action)


@app.command()
def retry(
    project: ProjectArg,
    hint: Annotated[
        str,
        typer.Option(help="Guidance for the agents, e.g. what you think the real problem is"),
    ] = "",
) -> None:
    """Retry the failed stage (or resume an interrupted run), optionally with a hint."""
    _workflow_command(lambda s: s.retry(project, hint))


@app.command()
def update(
    project: ProjectArg,
    text: Annotated[str | None, typer.Option("--text", "-t", help="The change to make")] = None,
    file: Annotated[Path | None, typer.Option("--file", "-f")] = None,
) -> None:
    """Start the next version of a completed app: describe what to add or change."""

    def change() -> str:
        content = _read_requirements(text, file)
        if not content:
            raise FactoryError("Describe the change with --text or --file")
        return content

    _workflow_command(lambda s: s.update(project, change()))


@app.command()
def delete(
    project: ProjectArg,
    yes: Annotated[bool, typer.Option("--yes", help="Do not ask for confirmation")] = False,
) -> None:
    """Delete a project and its workspace folder (never your own repository)."""

    def action() -> None:
        service = _service()
        details = service.projects.load(project)
        what = (
            "its documents and history (your repository is NOT touched)"
            if details.repository_path
            else "its documents, history and the generated code in source/"
        )
        if not yes and not typer.confirm(f"Delete project '{project}' with {what}?"):
            raise typer.Exit(code=1)
        service.delete_project(project)
        console.print(f"Deleted project [cyan]{project}[/].")

    _run(action)


@app.command()
def cancel(project: ProjectArg) -> None:
    """Cancel a failed project."""
    _workflow_command(lambda s: s.abort(project))


@app.command()
def history(
    project: ProjectArg,
    as_json: Annotated[bool, typer.Option("--json", help="Raw JSON")] = False,
    limit: Annotated[int, typer.Option(help="Show the last N entries")] = 30,
) -> None:
    """Show execution history (agents, tools, human decisions, errors)."""

    def action() -> None:
        entries = _service().history(project)[-limit:]
        if as_json:
            console.print_json(json.dumps(entries))
            return
        table = Table("Time", "Kind", "Stage", "Status", "Agent / model", "Duration", "Summary")
        for e in entries:
            who = e.get("agent") or ""
            if e.get("model"):
                who += f"\n{e.get('provider')}/{e['model']}"
            duration = f"{e['duration_ms'] / 1000:.1f}s" if e.get("duration_ms") else ""
            summary = e.get("summary", "") + (f"\n[red]{e['error']}[/]" if e.get("error") else "")
            table.add_row(
                e["started_at"], e["kind"], e["stage"], e["status"], who, duration, summary
            )
        console.print(table)

    _run(action)


# -- environment ------------------------------------------------------------------


@app.command()
def doctor(
    probe: Annotated[
        bool, typer.Option("--probe", help="Also send a tiny structured request to each profile")
    ] = False,
) -> None:
    """Check Python, dependencies, config, workspace, git, model server and models."""

    def action() -> None:
        results = run_doctor(load_config(_state["config_path"]), probe=probe)
        colors = {
            CheckStatus.PASS: "green",
            CheckStatus.WARN: "yellow",
            CheckStatus.FAIL: "red",
            CheckStatus.SKIP: "dim",
        }
        table = Table("Check", "Result", "Detail")
        for r in results:
            detail = r.detail + (f"\n[yellow]→ {r.hint}[/]" if r.hint else "")
            table.add_row(r.name, f"[{colors[r.status]}]{r.status.value}[/]", detail)
        console.print(table)
        if any(r.status is CheckStatus.FAIL for r in results):
            raise typer.Exit(code=1)

    _run(action)


@app.command()
def evaluate(
    case: Annotated[
        list[str] | None,
        typer.Option("--case", help="Run only these cases (repeatable)"),
    ] = None,
) -> None:
    """Check the configured model on small factory tasks (spec §68.17)."""

    def action() -> None:
        from software_factory.core.model_evaluation import CASES, CaseResult, run_evaluation

        service = _service()
        count = len(case) if case else len(CASES)
        console.print(f"Running {count} evaluation case(s) with the configured models…")

        def show(result: CaseResult) -> None:
            mark = "[green]PASS[/]" if result.passed else "[red]FAIL[/]"
            console.print(
                f"  {mark} {result.case} ({result.capability}, {result.duration_s}s): "
                f"{result.detail}"
            )

        report = run_evaluation(service.config, service.llm, case, on_result=show)
        console.print(
            f"\n{report.passed}/{len(report.results)} passed. Report saved in "
            f"{service.config.workspace_path / 'evaluations'}"
        )
        if report.passed < len(report.results):
            raise typer.Exit(code=1)

    _run(action)


@app.command()
def ui(
    port: Annotated[int, typer.Option(help="Port on this computer")] = 8000,
    open_browser: Annotated[
        bool, typer.Option("--open-browser/--no-browser", help="Open the page in a browser")
    ] = True,
) -> None:
    """Open the Software Factory in your web browser (runs only on this computer)."""

    def action() -> None:
        import threading
        import webbrowser

        import uvicorn

        from software_factory.web.app import create_app

        url = f"http://127.0.0.1:{port}"
        console.print(f"Software Factory is running at [bold]{url}[/] — press Ctrl+C to stop.")
        if open_browser:
            threading.Timer(1.0, webbrowser.open, args=(url,)).start()
        # 127.0.0.1 only: the page is not reachable from other computers.
        uvicorn.run(create_app(_service()), host="127.0.0.1", port=port, log_level="warning")

    _run(action)


@app.command()
def version() -> None:
    """Show factory and workflow versions."""
    console.print(f"software-factory {__version__} (workflow {WORKFLOW_VERSION})")


if __name__ == "__main__":
    app()
