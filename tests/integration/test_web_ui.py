"""Local web UI: the full journey through the browser pages, plus its safety rules.

Background actions run synchronously here (JobRunner(synchronous=True)); the
scripted provider answers for the agents; builds and tests run for real.
"""

from __future__ import annotations

import io
import re
import zipfile

import pytest
from fastapi.testclient import TestClient

from software_factory.core.factory_service import FactoryService
from software_factory.web.app import create_app
from software_factory.web.jobs import JobRunner
from tests.support import fixtures as fx


@pytest.fixture
def client(factory: FactoryService) -> TestClient:
    return TestClient(create_app(factory, JobRunner(synchronous=True)))


def _token(html: str) -> str:
    match = re.search(r'name="csrf" value="([^"]+)"', html)
    assert match, "form token missing"
    return match.group(1)


def _post(client: TestClient, path: str, **data: str):
    token = _token(client.get("/new").text)
    return client.post(path, data={"csrf": token, **data})


def test_full_journey_in_the_browser(client, script):
    script.add(
        fx.REQUIREMENT_AGENT, fx.requirements_needing_clarification(), fx.clear_requirements()
    )
    script.add(fx.ARCHITECTURE_AGENT, fx.architecture())
    script.add(fx.TECHNOLOGY_AGENT, fx.technology())
    script.add(fx.PLANNING_AGENT, fx.calculator_plan())
    script.add(fx.CODING_AGENT, fx.task1(), fx.task2())
    script.add(fx.REVIEW_AGENT, fx.clean_review())

    assert "No projects yet" in client.get("/").text

    page = _post(client, "/new", name="Calc", requirements="A calculator.", start="1")
    assert page.url.path == "/p/calc"
    assert "A few questions before designing" in page.text
    assert "Should this be a web application" in page.text

    page = _post(client, "/p/calc/answers", answer_Q1="A library", answer_Q2="Only me")
    assert "Review the requirements" in page.text
    assert "<h2>Functional requirements</h2>" in page.text  # Markdown rendered as HTML

    for stage in ("requirements", "architecture", "technology"):
        page = _post(client, "/p/calc/approve", stage=stage)
    assert "Review the plan" in page.text
    assert "Commands the factory will run" in page.text

    page = _post(client, "/p/calc/approve", stage="implementation_plan")
    assert "Approve the release" in page.text  # coded, built, tested, reviewed, validated

    archive = client.get("/p/calc/download")
    assert archive.headers["content-type"] == "application/zip"
    names = zipfile.ZipFile(io.BytesIO(archive.content)).namelist()
    assert "calc/calc.py" in names and not any("/.git/" in n for n in names)

    page = _post(client, "/p/calc/approve", stage="release")
    assert "Released." in page.text and "Download the app" in page.text

    index = client.get("/").text
    assert "Calc" in index and "Completed" in index
    history = client.get("/p/calc/history").text
    assert "T-2: Subtract function" not in history  # commits are in git, history has steps
    assert "coding-agent" in history
    assert "Validation" in client.get("/p/calc").text  # documents sidebar

    # Update the released app: version 2 starts at the requirements.
    assert "Update this app" in client.get("/p/calc").text
    script.add(fx.REQUIREMENT_AGENT, fx.clear_requirements())
    page = _post(client, "/p/calc/update", change="Add a multiply function")
    assert "Review the requirements" in page.text
    assert "Updating to version 2:" in page.text and "Add a multiply function" in page.text
    assert "Earlier versions" in page.text and "release-1" in page.text
    old = client.get("/p/calc/doc/release-notes.md?version=1")
    assert old.status_code == 200 and "version 1 (earlier release)" in old.text
    assert "release-notes.md?version=1" not in old.text  # current page is not a link
    assert client.get("/p/calc/doc/release-notes.md").status_code == 404  # not released yet


def test_reject_with_reason_and_failure_retry_with_hint(client, script):
    script.add(fx.REQUIREMENT_AGENT, fx.clear_requirements(), fx.clear_requirements())
    _post(client, "/new", name="Calc", requirements="A calculator.", start="1")
    page = _post(client, "/p/calc/reject", stage="requirements", reason="Add division")
    assert "Review the requirements" in page.text  # revised and waiting again
    assert "Add division" in " ".join(
        r.messages[1].content for r in script.calls_for(fx.REQUIREMENT_AGENT)
    )

    page = _post(client, "/p/calc/approve", stage="requirements")  # architecture not scripted
    assert "The factory needs your help" in page.text
    script.add(fx.ARCHITECTURE_AGENT, fx.architecture())
    page = _post(client, "/p/calc/retry", hint="Keep it to one module")
    assert "Review the architecture" in page.text


def test_forms_need_the_page_token(client):
    response = client.post("/new", data={"csrf": "forged", "name": "x", "requirements": "y"})
    assert response.status_code == 403


def test_model_written_html_is_not_executed(client, script):
    hostile = fx.clear_requirements()
    hostile["summary"] = "<script>alert('x')</script> <img src=x onerror=alert(1)>"
    script.add(fx.REQUIREMENT_AGENT, hostile)
    page = _post(client, "/new", name="Calc", requirements="A calculator.", start="1")
    assert "<script>alert" not in page.text and "<img src=x" not in page.text
    assert "&lt;script&gt;" in page.text


def test_documents_are_whitelisted_and_unknown_projects_404(client, script):
    script.add(fx.REQUIREMENT_AGENT, fx.clear_requirements())
    _post(client, "/new", name="Calc", requirements="A calculator.", start="1")
    assert client.get("/p/calc/doc/requirements.md").status_code == 200
    assert client.get("/p/calc/doc/state.json").status_code == 404
    assert client.get("/p/calc/doc/..%2F..%2Fconfig%2Ffactory.yaml").status_code == 404
    assert client.get("/p/nope").status_code == 404
    status = client.get("/p/calc/status.json").json()
    assert status["lifecycle_status"] == "WAITING_FOR_REQUIREMENT_APPROVAL"
    assert status["running"] is False


def test_create_errors_are_shown_on_the_form(client):
    _post(client, "/new", name="Calc", requirements="A calculator.")
    page = _post(client, "/new", name="Calc", requirements="Again.")
    assert "already exists" in page.text and "Again." in page.text


def test_health_check_page(client):
    page = client.get("/doctor").text
    assert "Health check" in page and "Python" in page and "PASS" in page


def test_background_runner_allows_one_action_per_project():
    import threading

    runner = JobRunner()
    release = threading.Event()
    assert runner.submit("p", "long", release.wait)
    assert runner.is_running("p")
    assert runner.submit("p", "second", lambda: None) is False
    release.set()
    runner.last("p").future.result(timeout=5)
    assert not runner.is_running("p")


def test_delete_button_and_missing_folder_page(client, factory, script):
    import shutil

    _post(client, "/new", name="Calc", requirements="A calculator.")
    assert "Delete project" in client.get("/p/calc").text
    page = _post(client, "/p/calc/delete")
    assert page.url.path == "/" and "Calc" not in page.text

    _post(client, "/new", name="Gone", requirements="x")
    shutil.rmtree(factory.projects.project_dir("gone"))
    assert "Folder missing" in client.get("/").text
    missing = client.get("/p/gone").text
    assert "no longer exists" in missing and "Remove from the list" in missing
    assert not factory.projects.project_dir("gone").exists()
    page = _post(client, "/p/gone/delete")
    assert "Gone" not in page.text
