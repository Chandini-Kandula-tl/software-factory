"""Secret scanning, source-context selection, and build-phase state machine rules."""

from __future__ import annotations

import pytest

from software_factory.domain.errors import InvalidTransitionError
from software_factory.domain.lifecycle import LifecycleStatus as S
from software_factory.domain.lifecycle import transition
from software_factory.tools.secret_scan import is_sensitive_file, is_test_path, scan_text
from software_factory.tools.source_workspace import (
    list_source_files,
    manifest_fingerprint,
    mentioned_files,
    read_files,
)


@pytest.mark.parametrize(
    ("line", "kind"),
    [
        ('ADMIN_PASSWORD = "hunter2hunter2"', "hard-coded credential"),
        ("api_key: 'abcdef123456'", "hard-coded credential"),
        ("token = 'sk-" + "a" * 30 + "'", "sk- style API key"),
        ("-----BEGIN RSA PRIVATE KEY-----", "private key"),
        ("aws = 'AKIA" + "B" * 16 + "'", "AWS access key id"),
    ],
)
def test_secret_scan_finds_literals(line, kind):
    matches = scan_text("app/config.py", line)
    assert [m.kind for m in matches][:1] == [kind]


@pytest.mark.parametrize(
    "line",
    [
        'password = request.form["password"]',  # not a literal secret
        'PASSWORD = os.environ["PASSWORD"]',
        'password = "changeme-example"',  # placeholder
        "def check_password(value):",
    ],
)
def test_secret_scan_ignores_non_secrets(line):
    assert scan_text("app/auth.py", line) == []


def test_test_code_and_sensitive_files():
    assert scan_text("tests/test_auth.py", 'password = "hunter2hunter2"')[0].in_test_code
    assert is_test_path("src/app.test.ts") and not is_test_path("src/app.ts")
    assert is_sensitive_file(".env") and is_sensitive_file("certs/server.pem")
    assert not is_sensitive_file(".env.example")


def test_source_context_helpers(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("print('app')\n")
    (tmp_path / "big.py").write_text("x" * 5000)
    (tmp_path / "package-lock.json").write_text("{}" * 1000)
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "dep.js").write_text("ignored")
    tree = list_source_files(tmp_path)
    assert tree == ["big.py", "package-lock.json", "src/app.py"]

    files = read_files(tmp_path, ["big.py", "package-lock.json", "src/app.py"], budget_chars=100)
    assert list(files) == ["src/app.py"]  # big file skipped, lock file never read

    assert mentioned_files('File "src/app.py", line 1', tree) == ["src/app.py"]

    before = manifest_fingerprint(tmp_path)
    (tmp_path / "requirements.txt").write_text("requests\n")
    assert manifest_fingerprint(tmp_path) != before


def test_build_phase_transitions():
    assert transition(S.TESTING, S.DEVELOPMENT) is S.DEVELOPMENT
    assert transition(S.BUG_FIXING, S.WAITING_FOR_CHANGE_REQUEST_APPROVAL)
    assert transition(S.WAITING_FOR_CHANGE_REQUEST_APPROVAL, S.TECHNOLOGY_SELECTION)
    assert transition(S.WAITING_FOR_RELEASE_APPROVAL, S.RELEASE) is S.RELEASE
    for current, target in [
        (S.DEVELOPMENT, S.CODE_REVIEW),  # code must be built and tested first
        (S.CODE_REVIEW, S.RELEASE),  # security + validation + approval first
        (S.FINAL_VALIDATION, S.RELEASE),  # human release approval first
        (S.BUILDING, S.CODE_REVIEW),
    ]:
        with pytest.raises(InvalidTransitionError):
            transition(current, target)
