# CLAUDE.md

This file guides Claude Code (claude.ai/code) when working in this repository.

## Project overview

TLSVigil is an open-source TLS certificate and domain expiry monitor: a command-line tool and a
GitHub Action that warn before the certificate of a host or the registration of a domain expires.
Python `>=3.12`, standard library only (zero runtime dependencies). The certificate is read from a
verified TLS handshake; the domain expiry comes from RDAP, through the IANA bootstrap. The default
channel is one GitHub issue in the user's own repository, opened, updated and closed through the
workflow's `GITHUB_TOKEN`; Telegram and SMTP are optional.

Not released yet (version `1.0.0.dev0`; the first release will be `1.0.0`, and the `v0.x` milestones
of the build are in the roadmap of [specs/README.md](specs/README.md)). Today the CLI runs `tlsvigil
check` (the checks of a `tlsvigil.toml` or of `--host`/`--domain`, printed as text or JSON, with
exit codes) and, with `--notify`, delivers the result to the GitHub issue, Telegram and e-mail; the
Action is not built yet. Everything is specified in [specs/](specs/) first. The repository has been public since day zero; releasing and announcing
are the maintainer's acts.

Everything in the repository is English, except `README.pt-BR.md` (see Conventions).

## Common commands

```bash
# Create/update the environment from pyproject.toml + uv.lock (includes the dev group)
uv sync

# Run the CLI from the source tree (exit 0 all fine, 1 needs attention, 2 nothing checked, 3 bug,
# 4 a notification not delivered)
uv run tlsvigil --version
uv run tlsvigil check --host example.com --domain example.com
uv run tlsvigil check --config tlsvigil.local.toml --json --lang pt-BR

# Deliver to the channels of [notifications] (their variables below must be set)
uv run tlsvigil check --config tlsvigil.local.toml --notify --state .tlsvigil-state.json

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

Configuration is a TOML file, `tlsvigil.toml` or `--config PATH` (spec 004). With `--notify`, the
channels listed in `[notifications]` read their secrets and addresses from the environment (spec
005, RF-03), and only those channels' variables are read; a key in the file that looks like one of
them is a configuration error.

| Variable | Channel | Form |
|---|---|---|
| `GITHUB_TOKEN` | `github` | the workflow's token, with `permissions: issues: write` |
| `GITHUB_REPOSITORY` | `github` | `owner/name`; set by GitHub Actions |
| `TLSVIGIL_TELEGRAM_TOKEN` | `telegram` | `<digits>:<letters, digits, _ and ->` |
| `TLSVIGIL_TELEGRAM_CHAT_ID` | `telegram` | an integer, possibly negative, or `@name` |
| `TLSVIGIL_SMTP_USERNAME`, `TLSVIGIL_SMTP_PASSWORD` | `smtp` | no control character |
| `TLSVIGIL_SMTP_FROM`, `TLSVIGIL_SMTP_TO` | `smtp` | one bare address; one or more, separated by commas |

In CI they come from the secret store, never from a file in the repository. A local
`tlsvigil.local.toml`, `.env` and the default state file `.tlsvigil-state.json` are ignored.

## Architecture

```text
src/tlsvigil/       # the package: __main__.py (python -m tlsvigil),
                    #   cli.py (argparse, check and --notify, exit codes 0 to 4, the Redactor)
                    #   config.py (tlsvigil.toml or the flags → a validated Config, every problem)
                    #   runner.py (run(): the checks in order, one shared RdapClient → a Report)
                    #   output.py (render_text(), render_json(), printable() for outside text)
                    #   i18n.py (the two languages: the catalogue, translate() and plural())
                    #   certificate.py (the verified TLS check, its result type and classify()),
                    #   rdap.py (the domain check: IANA bootstrap, RdapClient, its redirects)
                    #   https.py (request(): one bounded HTTPS exchange, any status, no redirect)
                    #   environment.py (the channels' variables → settings; the Redactor)
                    #   alerts.py (findings(), plan(): what a channel hears; the reminder days)
                    #   state.py (the state file: load_state(), save_state(), reset_for())
                    #   messages.py (the words of the issue, the comment, Telegram, e-mail)
                    #   github.py (GitHubChannel: find, tell, bring the issue up to date)
                    #   telegram.py (TelegramChannel: one plain-text sendMessage)
                    #   mail.py (MailChannel: smtplib bounded by one deadline and a line cap)
                    #   delivery.py (Delivery, DeliveryOutcome: what a channel did)
                    #   notify.py (notify(): state in, one plan per channel, state out)
tests/              # pytest; the CLI is driven through main(argv), and the docs have tests too
  runs.py           # the clock, the leaves and the RDAP registry shared by the run tests
  reports.py        # hand-built reports for the output and CLI tests (no network)
  conftest.py       # installs the network guard (autouse) and the CA and local-server fixtures
  network_guard.py  # resolve and connect on loopback only (older lookups too); else the test fails
  tls_server.py     # LocalServer: a TLS, silent or plain-text server on 127.0.0.1 and ::1
  http_server.py    # LocalHTTPServer: HTTPS routes (fixed, silent, drip, endless, handled)
  fake_apis.py      # FakeGitHub and FakeTelegram: state in memory, headers and token checked
  smtp_server.py    # LocalSMTPServer: TLS or STARTTLS, AUTH, the commands recorded, misbehaving modes
  data/rdap/        # the hand-written RDAP answers the tests read and serve
  certs.py          # the test certificates trustme does not make (self-signed, no AKI)
specs/              # spec-driven development: requirements → design → tasks per feature
docs/               # stack.md: each technology and why it was chosen
.githooks/          # versioned pre-commit hook (core.hooksPath)
.claude/            # Claude Code hooks: format on edit, validate at turn end, guard uv.lock
.github/            # CI, CodeQL, dependency review, weekly pip-audit, Dependabot, templates
uv.lock             # never edit by hand (generated; a hook blocks it)
```

`tlsvigil check` (spec 004) loads and validates the whole configuration before any connection,
runs the certificate checks (spec 002) and then the domain checks over RDAP (spec 003), and
renders the report. With `--notify` (spec 005), it also reads the channels' settings from the
environment before any check, and after the run `notify()` loads the state, asks `alerts.py` what
each channel has to hear, delivers, and saves the state once. The composite Action in `action.yml`
(spec 006) is not built yet.

### Before you touch…

| You are changing | Read first |
|---|---|
| `pyproject.toml` (`dependencies`, the README rewrite for PyPI) | spec `001` (ADR-1 and the packaging decisions) |
| `src/tlsvigil/certificate.py` | spec `002` (ADR-2 on the explicit context, ADR-3 on the reasons) |
| `src/tlsvigil/rdap.py` | spec `003` (ADR-2 on `http.client`, ADR-3 on the deadline, ADR-4 on the size caps, ADR-5 on redirects) |
| `src/tlsvigil/https.py` | spec `005` (ADR-1) and spec `003` (ADR-3, ADR-4); the read loop never calls `settimeout` after the body ends |
| `tests/conftest.py`, `tests/network_guard.py`, `tests/tls_server.py` | spec `002` (ADR-4, ADR-5 and deviation 7 on `::1`) |
| `tests/http_server.py`, `tests/data/rdap/` | spec `003` (ADR-7 and deviation 3 on the routes); spec `005` (RF-14) for `POST`, `PATCH` and `Route.handled` |
| `src/tlsvigil/alerts.py` (a status, an event, a reminder day) | spec `005` (RF-04 to RF-06, ADR-8; the two tables of "Findings and events") |
| `src/tlsvigil/state.py` (a field, the version, the write) | spec `005` (RF-07, ADR-2); `tests/test_state.py` lists the fields a saved file may hold |
| `src/tlsvigil/messages.py` (a message, an escape, a cap) | spec `005` (RF-11, ADR-5); `tests/test_messages.py` fails when a notification sentence holds a Markdown character |
| `src/tlsvigil/github.py` (a step, a request, a failure) | spec `005` (RF-08, ADR-3, ADR-4; the table of "The GitHub issue"); four requests at most |
| `src/tlsvigil/telegram.py` (the request, a refusal) | spec `005` (RF-09, ADR-5); no `parse_mode`, and the URL holds the token, so no part of it reaches a message |
| `src/tlsvigil/mail.py` (the session, a failure) | spec `005` (RF-10, ADR-6); `getreply` is copied from `smtplib` and must follow it; STARTTLS is required, never opportunistic |
| `tests/smtp_server.py` | spec `005` (ADR-11); it offers `AUTH` in clear on purpose, so a client that skips STARTTLS is caught |
| `src/tlsvigil/environment.py` (a variable, a form, the Redactor) | spec `005` (RF-03, ADR-7, ADR-10); `config.py` names the same variables in its secret-like keys |
| `src/tlsvigil/notify.py` (the order, the state, a channel's failure) | spec `005` (RF-01, RF-07, ADR-2; deviation 4 on `warn`); the state is saved once, in a `finally` |
| `src/tlsvigil/cli.py` (flags, exit codes, streams) | spec `004` (ADR-3 on exit `3`, ADR-7 on the test seams, deviation 5 on `--config` with `--host`); spec `005` (ADR-7 on the Redactor over every stream, ADR-9 on the new seams, exit `4`) |
| `src/tlsvigil/config.py` (keys, defaults, places) | spec `004` (ADR-2 on the validators' codes, ADR-6 on the size cap, ADR-8 on host forms); both READMEs show the keys |
| `src/tlsvigil/output.py`, the JSON shape | spec `004` (ADR-4 on escaping outside text, ADR-5 on the JSON schema version); spec `005` (RF-12) for the delivery lines and the `notifications` key |
| `src/tlsvigil/i18n.py` (a sentence or a key) | spec `004` (ADR-1); `tests/test_i18n.py` fails when a language or a key is missing |
| `README.md` or `README.pt-BR.md` | the two READMEs rule below, `tests/test_readme_pt_br.py` (headings and table rows, the variables too) and `tests/test_readme_config.py` (the one `toml` block, `[notifications]` included) |
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
  headings and tables, `tests/test_readme_config.py` loads the example `tlsvigil.toml` of both,
  and `tests/test_readme_links.py` checks that the links of `README.md` still
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
