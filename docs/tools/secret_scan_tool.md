# Secret Scan Tool

**Code:** [`src/software_factory/tools/secret_scan.py`](../../src/software_factory/tools/secret_scan.py) · **Used by:** `security_validation` in [`quality_nodes.py`](../../src/software_factory/orchestration/nodes/quality_nodes.py) · **Tests:** `tests/unit/test_build_tools.py`, `tests/workflow/test_build_workflow.py` · **Spec:** §17, §68.12

## Purpose

Deterministic secret detection for **generated source code**. It is stricter than log
redaction ([`observability/redaction.py`](../../src/software_factory/observability/redaction.py)):
in source code only *literal* values count. So `password = request.form["password"]`
is fine, while `password = "hunter2hunter2"` is a finding.

It is a set of plain functions, not a `ToolKit` tool. No agent calls it; the
[security validation step](../agents/security_validation_agent.md) does.

## API

```python
scan_text(path: str, text: str) -> list[SecretMatch]   # line-by-line scan
is_test_path(path: str) -> bool
is_sensitive_file(path: str) -> bool

@dataclass(frozen=True)
class SecretMatch:
    file: str
    line: int           # 1-based
    kind: str           # pattern name, see below
    in_test_code: bool  # is_test_path(file)
```

## Patterns

| Kind | Matches |
|---|---|
| `private key` | `-----BEGIN ... PRIVATE KEY-----` |
| `sk- style API key` | `sk-` + 20 or more `[A-Za-z0-9_-]` |
| `AWS access key id` | `AKIA` + 16 `[0-9A-Z]` |
| `GitHub token` | `ghp_` / `gho_` / `ghu_` / `ghs_` / `ghr_` + 30 or more alphanumerics |
| `Google API key` | `AIza` + 35 characters |
| `Slack token` | `xoxa-` / `xoxb-` / `xoxp-` / `xoxr-` / `xoxs-` + 10 or more characters |
| `hard-coded credential` | An identifier *containing* `password`, `passwd`, `pwd`, `secret`, `api_key`/`api-key`/`apikey`, `access_token` or `auth_token` (case-insensitive, e.g. `ADMIN_PASSWORD`), then `=` or `:`, then a quoted literal of 6 or more non-space characters |

Each line is checked against every pattern, so one line can produce several matches.

## Placeholders are ignored

A match is dropped when the **matched text** contains (case-insensitive) `example`,
`changeme`, `placeholder`, `your_` / `your-`, `xxx`, `dummy`, or `<...>`. For example,
`password = "changeme-example"` is not reported.

## Test code

`is_test_path(path)` is true when any of these holds:

- a parent directory is named `test`, `tests`, `__tests__`, `spec` or `fixtures`;
- the file name starts with `test_`;
- the file name contains `.test.` or `.spec.`.

The security step uses this to **downgrade** a `hard-coded credential` in test code
from `high` (blocking) to `medium` (not blocking). The other kinds (real key/token
formats) stay `critical` even in tests.

## Sensitive files

`is_sensitive_file(path)` is true for these file names: `.env`, `id_rsa`, `id_ed25519`,
`credentials.json`. It is also true for these suffixes: `.pem`, `.key`, `.p12`, `.pfx`.
`.env.example` is **not** sensitive. The security step reports a tracked sensitive file
as `critical` and does not scan its content.

## Examples

```python
scan_text("app/config.py", 'ADMIN_PASSWORD = "hunter2hunter2"')[0].kind  # "hard-coded credential"
scan_text("app/auth.py", 'PASSWORD = os.environ["PASSWORD"]')           # []
scan_text("tests/test_auth.py", 'password = "hunter2hunter2"')[0].in_test_code  # True
is_sensitive_file("certs/server.pem")   # True
```

## Known limitations

- Pattern-based only: no entropy detection, and secrets in unusual formats (e.g. an unquoted value or a split string) are missed.
- Placeholder filtering can hide a real secret that contains a placeholder word.
- The credential pattern can flag harmless literals assigned to names such as `pwd_hint = "enter text"`.
- The file-size (1 MB) and UTF-8 limits are applied by the caller, not here.
