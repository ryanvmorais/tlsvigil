# CLAUDE.md

This file guides Claude Code (claude.ai/code) when working in this repository.

## Project overview

TLSVigil is an open-source TLS certificate and domain expiry monitor: a command-line tool and a
GitHub Action that warn before the certificate of a host or the registration of a domain expires.
Python `>=3.12`, standard library only (zero runtime dependencies). The certificate is read from a
verified TLS handshake; the domain expiry comes from RDAP, through the IANA bootstrap. The default
channel is one GitHub issue in the user's own repository, opened, updated and closed through the
workflow's `GITHUB_TOKEN`; Telegram and SMTP are optional.

Not released yet (version `1.0.0.dev0`; the first release will be `1.0.0`, and the `v0.x`
milestones of the build are in the roadmap of [specs/README.md](specs/README.md)). Today the CLI
answers `tlsvigil --version` only. Everything is specified in [specs/](specs/) first. The
repository has been public since day zero; releasing and announcing are the maintainer's acts.

Everything in the repository is English, except `README.pt-BR.md` (see Conventions).

## Common commands

```bash
# Create/update the environment from pyproject.toml + uv.lock (includes the dev group)
uv sync

# Run the CLI from the source tree
uv run tlsvigil --version

# Run the tests (one file, then one test)
uv run pytest
uv run pytest tests/test_cli.py
uv run pytest tests/test_cli.py::test_version_prints_the_installed_version_and_exits_zero

# The quality gate, in the same order as the pre-commit hook and the CI
uv run ruff check .
uv run black --check .
uv run mypy
uv run pytest --cov

# Format what you changed (the Claude hook does this on every edit)
uv run ruff check --fix . && uv run black .

# Activate the versioned pre-commit hook (once per clone)
git config core.hooksPath .githooks

# Build the sdist and wheel locally (the CI `package` job does the same)
uv build
```

## Environment variables

None yet. Configuration will be a TOML file (spec 004); secrets (a Telegram bot token, an SMTP
password) come from the CI's secret store, never from a file in the repository. A local
`tlsvigil.local.toml` and `.env` are ignored.

## Architecture

```text
src/tlsvigil/       # the package: cli.py (argparse entry point), __main__.py (python -m tlsvigil),
                    #   certificate.py (the verified TLS check, its result type and classify()),
                    #   rdap.py (the domain check: IANA bootstrap, bounded HTTPS, RdapClient)
                    #   i18n.py (the two languages: the catalogue, translate() and plural())
tests/              # pytest; the CLI is driven through main(argv), and the docs have tests too
  conftest.py       # installs the network guard (autouse) and the CA and local-server fixtures
  network_guard.py  # resolve and connect on loopback only; anything else fails the test
  tls_server.py     # LocalServer: a TLS, silent or plain-text server on 127.0.0.1 and ::1
  http_server.py    # LocalHTTPServer: HTTPS routes (RDAP documents, silent, drip, endless)
  data/rdap/        # the hand-written RDAP answers the tests read and serve
  certs.py          # the test certificates trustme does not make (self-signed, no AKI)
specs/              # spec-driven development: requirements → design → tasks per feature
docs/               # stack.md: each technology and why it was chosen
.githooks/          # versioned pre-commit hook (core.hooksPath)
.claude/            # Claude Code hooks: format on edit, validate at turn end, guard uv.lock
.github/            # CI, CodeQL, dependency review, weekly pip-audit, Dependabot, templates
uv.lock             # never edit by hand (generated; a hook blocks it)
```

The certificate check (spec 002) and the domain check over RDAP (spec 003) are library
functions; no command calls them. The specs to come add, in this order: the configuration, the
output, `--json`, `--lang` and the exit codes (004), the notifications without duplicates (005),
and the composite Action in `action.yml` (006).

### Before you touch…

| You are changing | Read first |
|---|---|
| `pyproject.toml` (`dependencies`, the README rewrite for PyPI) | spec `001` (ADR-1 and the packaging decisions) |
| `src/tlsvigil/certificate.py` | spec `002` (ADR-2 on the explicit context, ADR-3 on the reasons) |
| `src/tlsvigil/rdap.py` | spec `003` (ADR-2 on `http.client`, ADR-3 on the deadline, ADR-4 on the size caps, ADR-5 on redirects) |
| `tests/conftest.py`, `tests/network_guard.py`, `tests/tls_server.py` | spec `002` (ADR-4, ADR-5 and deviation 7 on `::1`) |
| `tests/http_server.py`, `tests/data/rdap/` | spec `003` (ADR-7 and deviation 3 on the routes) |
| `README.md` or `README.pt-BR.md` | the two READMEs rule below and `tests/test_readme_pt_br.py` |
| `.github/workflows/` | the comments at the top of each workflow; the ruleset requires the `Quality` checks by name |

### Do not regress

- ⚠️ **Zero runtime dependencies** is the product promise: `dependencies = []`, and the CI
  `package` job fails if the wheel declares one (ADR-1).
- ⚠️ **The certificate date comes from a verified chain only.** A failed verification is a
  finding, never a reason to retry without verification (`CERT_NONE`) to read the date.
- ⚠️ **The network reaches only what the configuration names**: the hosts listed, and the RDAP
  server the IANA bootstrap names for the top-level domain, over HTTPS, with a timeout and a cap on
  the size of the answer, and no redirect to another host.
- ⚠️ **Secrets never reach an output**: the GitHub token, a Telegram token or an SMTP password
  appear in no text, JSON, log line, issue or error message.
- ⚠️ **External text is data**: what comes from a certificate, an RDAP answer or the configuration
  reaches an issue or a notification as plain text, never as markup, a link or a mention.
- ⚠️ **Tests never reach the network**: a local server on `127.0.0.1` or a recorded answer.
  The autouse guard in `tests/network_guard.py` fails any other lookup or connection.
- ⚠️ **Every `ssl.SSLContext` sets `minimum_version` explicitly**, in the tests too (CodeQL
  `py/insecure-protocol`); the TLS 1.1 test triggers that alert on purpose.

## Conventions

- **Language:** code, commits, branches, docstrings and docs in English (open source). User-facing
  CLI text: English (default) and Brazilian Portuguese, chosen by `--lang` (spec 004). The one
  documentation exception is `README.pt-BR.md`, the Portuguese README for the Brazilian audience:
  keep it in step with `README.md` in the same PR; `tests/test_readme_pt_br.py` compares their
  headings and tables, and `tests/test_readme_links.py` checks that the links of `README.md` still
  resolve after the build rewrites them for PyPI.
- **Impersonal text:** what is versioned speaks to whoever cloned the repository; the maintainer's
  name appears only in authorship (`tests/test_impersonality.py`).
- **File style:** follow the file you are editing: docstrings with `Args:` / `Returns:`, full
  annotations and `# why` comments.
- **Python floor:** 3.12, tested in CI with 3.13, 3.14 and the newest stable release (3.15), on
  Linux and on Windows. Ruff and black target `py312` and mypy runs with `python_version = "3.12"`,
  so syntax newer than the floor fails the lint. Review when 3.12 reaches end of life
  (2028-10-31).
- **Commits:** Conventional Commits (`feat`, `fix`, `docs`, `chore`, `ci`, `deps`…), squash-merged.

## Quality and automation

- **Gate:** `ruff` → `black --check` → `mypy` (strict, over `src`, `tests` and `.claude/hooks`) →
  `pytest --cov`. The same sequence runs in `.githooks/pre-commit` and in
  `.github/workflows/ci.yml`. The repository allows squash merges only, and the `proteger-main`
  ruleset requires a pull request, linear history and the `Quality (Python 3.12)` and
  `Quality (Python 3.15)` checks; `pip-audit`, CodeQL and dependency review are not required, so a
  new CVE cannot block the PR that fixes it.
- **Claude Code hooks** (`.claude/`, run with `python`): `project_format` (PostToolUse: ruff
  `--fix` + black on the edited `.py`), `validate` (Stop: ruff + mypy when a `.py` changed; pytest
  is left to the pre-commit hook), `guard_generated` (PreToolUse: blocks editing `uv.lock`),
  `session_start` (warns about a missing or stale `.venv` or an inactive `core.hooksPath`).
- **CI minutes:** free, because the repository is public. CI runs on pull requests, on pushes to
  `main` and by hand; CodeQL (as a workflow) on `main`, PRs and weekly; dependency review on PRs.
- **Weekly `pip-audit.yml`** audits `uv.lock` every Monday 09:00 (São Paulo). A red run means a
  known CVE in the lock: update the locked version of the affected dependency.

## Spec-driven workflow

Features are written down in `specs/NNN-name/` (`requirements.md` → `design.md` → `tasks.md`)
with a human approval gate between phases (see [specs/README.md](specs/README.md)). Flow per task:
issue → branch → PR. A change to the architecture, the commands or the configuration updates both
READMEs, this file and `docs/stack.md` in the same PR.

## Dependencies

`uv` manages everything: `pyproject.toml` holds the floors (`>=`) and `uv.lock` the exact
versions of the dev tooling. Commit both together (`uv lock --check` must pass).
`exclude-newer = "7 days"` keeps a release younger than a week out of the lock, and the CI runs
`uv sync --locked`. Dependabot (`uv` and `github-actions`, Sundays, 7-day cooldown) opens the
routine PRs.

> Publishing (PyPI, GitHub Releases, the Marketplace) is the maintainer's act. A vulnerability in a
> published version is reported privately (private vulnerability reporting), never in a public
> issue, PR or commit.
