---
feature: Foundation — package skeleton, local quality gate, CI, supply-chain hygiene and documentation guards
status: in progress
date: 2026-11-27
related:
  - 001-foundation/requirements.md
origin: conception
---

# 001 — Foundation — design

## Overview

The foundation is three layers that share one definition of "ready". The **package** is a
standard-library-only Python distribution built with Hatchling, whose README is rewritten at build
time so its links work on PyPI. The **quality sequence** (`ruff → black --check → mypy → pytest
--cov`) is configured once in `pyproject.toml` and invoked in two places that must stay identical:
the versioned git pre-commit hook and the CI `quality` job. The **supply chain** (a locked graph
with a 7-day cooldown, weekly Dependabot, weekly `pip-audit`, CodeQL, dependency review and
SHA-pinned Actions) keeps the dependency-free promise honest over time. Two smaller layers sit
beside them: the Claude Code hooks move the same checks earlier (format on every edit, validate
when a turn ends), and the documentation tests keep the two READMEs, their links and the
impersonal voice from drifting.

Nothing here has monitoring logic. The design goal is that later specs add modules under
`src/tlsvigil/` and tests under `tests/` without touching any of this, and that a change to the
foundation is visible in the diff of exactly one of the files listed below. The product decisions
that every later spec builds on (ADR-1 to ADR-6) are recorded here because they were taken with
the stack, before the first feature; the spec that implements each one refines it in its own
design.

## Module layout

```text
pyproject.toml                       # metadata, dev group, README rewrite for PyPI, tool config
uv.lock                              # exact graph of the dev tooling (generated; never edited by hand)
.python-version                      # the Python of a developer clone (newest stable)
.gitignore  .gitattributes           # local config and secrets ignored; LF everywhere
src/tlsvigil/
  __init__.py                        # __version__ from package metadata, with a dev fallback
  __main__.py                        # python -m tlsvigil
  cli.py                             # build_parser() and main(argv) -> int
  py.typed                           # PEP 561 marker
tests/
  test_cli.py                        # RF-01, RF-02
  test_readme_pt_br.py               # RF-08: the two READMEs keep the same shape
  test_readme_links.py               # RF-08: links resolve on GitHub and after the PyPI rewrite
  test_impersonality.py              # RF-08: the maintainer's name only in authorship
.githooks/pre-commit                 # local gate (bash), activated by core.hooksPath
.claude/settings.json                # registers the four hooks
.claude/hooks/                       # session_start, guard_generated, project_format, validate
.github/workflows/ci.yml             # quality (3.12 to 3.15, Linux and Windows), package, security, shell
.github/workflows/codeql.yml         # CodeQL over python and actions
.github/workflows/dependency-review.yml   # new vulnerable dependencies in a PR
.github/workflows/pip-audit.yml      # weekly CVE audit of uv.lock
.github/dependabot.yml               # uv + github-actions, weekly, 7-day cooldown, grouped
.github/ISSUE_TEMPLATE/  pull_request_template.md  CODEOWNERS   # RF-09
README.md  README.pt-BR.md           # English (PyPI) and Brazilian Portuguese
CONTRIBUTING.md  CODE_OF_CONDUCT.md  SECURITY.md   # RF-09
CLAUDE.md  docs/stack.md  specs/     # contributor and design documentation
```

## Data model

None. The foundation holds no data. The one value it computes is `__version__: str`, read from
the installed distribution's metadata (`importlib.metadata.version("tlsvigil")`) and falling back
to `"0.0.0.dev0"` on `PackageNotFoundError` (RF-01).

## Components

### Package and entry point (RF-01, RF-02, RNF-01)

`pyproject.toml` declares `dependencies = []`, `requires-python = ">=3.12"` and the console script
`tlsvigil = "tlsvigil.cli:main"`; `__main__.py` calls the same `main` for `python -m tlsvigil`.
`cli.build_parser()` returns an `argparse.ArgumentParser` with `--version`;
`cli.main(argv: Sequence[str] | None = None) -> int` parses and returns `0`. Taking `argv`
explicitly keeps the CLI testable without a subprocess. `argparse` already exits with `2` and
prints the usage on standard error for an unknown flag, so RF-02 needs no code, only a test.

### Quality sequence (RF-03, RF-04, RNF-02)

The four tools read their configuration from `pyproject.toml` only. `.githooks/pre-commit` runs
them in check mode (`ruff check .`, `black --check --quiet .`, `mypy`, `pytest -q --cov`) under
`set -euo pipefail`, labelling each step, so the first failure aborts the commit and names itself.
The CI `quality` job runs the same four commands, in the same order, after `uv sync --locked`.
`[tool.pytest]` turns warnings into errors and lists the 15 slowest tests, so a slow or noisy test
shows up in the run that adds it. The matrix is every Python of the classifiers (3.12 to 3.15) on
`ubuntu-26.04` and on `windows-2025`; the Linux jobs keep the name `Quality (Python X)`, which the
ruleset requires for 3.12 and 3.15.

### Package verification (RF-05)

The CI `package` job builds with `uv build`, runs `twine check --strict` on both files, fails if
the wheel's `METADATA` has a `Requires-Dist` line, and installs the wheel in a fresh virtual
environment to run `tlsvigil --version`. The sdist file list is fixed by
`[tool.hatch.build.targets.sdist] include`: the package, `README.md`, `LICENSE` and `SECURITY.md`.

### Supply chain (RF-06)

`[tool.uv] exclude-newer = "7 days"` keeps a release younger than a week out of the lock, the same
cooldown Dependabot applies, so a hijacked release is usually pulled before it can reach either.
Dependabot watches `uv` and `github-actions` on Sundays at 09:00 (São Paulo), grouping minor and
patch updates. The `security` job and the weekly `pip-audit.yml` export the lock and audit it.
CodeQL runs as a workflow over `python` and `actions` on `main`, pull requests and weekly;
dependency review fails a pull request that adds a dependency with a known vulnerability of high
severity or above. Every `uses:` is a full SHA with the version in a comment, every checkout sets
`persist-credentials: false`, and the default token permission is `contents: read`.

### Claude Code hooks (RF-07)

`.claude/settings.json` registers four Python hooks that call the project's `.venv` tooling and
never the network: `guard_generated` (PreToolUse, blocks writes to `uv.lock` with exit `2`),
`project_format` (PostToolUse, `ruff check --fix` and `black` on the edited `.py`), `validate`
(Stop, ruff and mypy when a `.py` under the checked folders changed; pytest stays in the
pre-commit hook because it can be red in the middle of a feature) and `session_start` (warns about
a stale `.venv` or an inactive `core.hooksPath`, and swallows its own errors).

### Documentation guards (RF-08)

Three test files read text and write nothing. `test_readme_pt_br.py` compares the heading levels
of the two READMEs, the rows of the check table and of the comparison table, the cross-links in
the first four lines, and checks that each relative link of the Portuguese README names a real
path. `test_readme_links.py` checks the relative links of `README.md`, then applies the
`fancy-pypi-readme` substitutions read from `pyproject.toml` and asserts that no relative link is
left. `test_impersonality.py` asks `git ls-files` for the versioned files and checks where the
maintainer's first name appears and that `CLAUDE.md` and `.claude/` point at no personal tool.

### Community and security files (RF-09)

`SECURITY.md` follows the shape of a security tool's policy: supported versions, private
vulnerability reporting with a 7-day acknowledgement, coordinated disclosure within 90 days,
authorized use of a passive monitor, and a scope that separates a vulnerability in TLSVigil from
an expiring certificate it reports. `CODE_OF_CONDUCT.md` is the Contributor Covenant 3.0 with
private vulnerability reporting as the reporting channel. The issue forms add the warnings that
matter for this tool: never paste a token, and describe a private host instead of naming it.
`CODEOWNERS` asks for the maintainer's review on automation, hooks, agent instructions and the
dependency graph.

## Interfaces

| Contract | Shape |
|---|---|
| `tlsvigil --version` | stdout `tlsvigil <version>\n`, exit `0` |
| `python -m tlsvigil --version` | the same line and exit code |
| `tlsvigil` (no args) | no output, exit `0` (placeholder until spec 004 defines the commands) |
| `tlsvigil --unknown` | usage and error on stderr, exit `2` |
| `tlsvigil.__version__` | `str`; `"0.0.0.dev0"` outside an install |
| `tlsvigil.cli.main(argv)` | `Sequence[str] \| None -> int`; `None` reads `sys.argv` |
| Hook input (Claude Code) | JSON on stdin; `tool_input.file_path` for edit hooks, `stop_hook_active` for Stop |
| Hook output | exit `2` + message on stderr blocks; exit `0` allows |

`--version` and the exit codes become public contract at the first release; the no-argument
behaviour does not, and spec 004 replaces it.

## ADRs

### ADR-1 — Zero runtime dependencies

**Decision.** The package uses the Python standard library only (`dependencies = []`), and the CI
`package` job fails if the wheel declares a dependency.
**Alternatives.** (a) `cryptography` to parse certificates in detail. (b) `requests` or `httpx`
for RDAP and the notification APIs. (c) `click` or `typer` for the command line.
**Why.** TLSVigil runs in the user's CI with a token that can write to the user's repository, so
every dependency is code that runs with that token. The standard library covers the job: a chain
that verifies gives the date (`ssl`), RDAP and the APIs are a few HTTPS requests (`urllib`), and
`argparse` and `tomllib` cover the command line and the configuration. A chain that fails
verification is itself the warning, so no certificate parser is needed to read an unverified one.
**Trade-off.** No detail from a certificate that fails verification (its date, its issuer), and
more hand-written code for HTTP and the configuration checks.

### ADR-2 — Domain expiry over RDAP, through the IANA bootstrap, with no WHOIS

**Decision.** The domain check asks the RDAP server that the IANA bootstrap names for the
top-level domain, reads the `expiration` event, treats it as optional, and never falls back to
WHOIS. The detail is designed in spec `003`.
**Alternatives.** (a) WHOIS over port 43. (b) A paid domain API. (c) Leaving domains out.
**Why.** RDAP is JSON over HTTPS with a standard shape; WHOIS is free text in a different format
per registry, over plain TCP. The bootstrap says which server is authoritative, so the tool never
picks a server on its own.
**Trade-off.** Top-level domains with no RDAP service (`.de`, `.io`, `.co`, `.eu` and others) and
records without an `expiration` event get no domain warning; the result says the date is unknown.

### ADR-3 — The default channel is one issue in the user's repository

**Decision.** The warning goes to one GitHub issue, through the workflow's `GITHUB_TOKEN`, opened
when something needs attention, updated on later runs and closed when everything is fine again.
Telegram and SMTP e-mail are optional channels. The detail is designed in spec `005`.
**Alternatives.** (a) E-mail as the default. (b) A webhook per chat service. (c) Only the exit code,
leaving the alert to the user.
**Why.** The token already exists in every workflow run and needs no secret; GitHub notifies the
repository's watchers by e-mail and in the app; one issue that updates itself is a history and
never a flood.
**Trade-off.** The default needs GitHub; a user of the command line alone relies on the exit code,
the JSON output or an optional channel.

### ADR-4 — Configuration in TOML

**Decision.** The hosts, the domains and the warning threshold live in a TOML file read with
`tomllib`. The detail is designed in spec `004`.
**Alternatives.** (a) YAML, the format of the workflows. (b) Only action inputs and command-line
flags. (c) JSON.
**Why.** `tomllib` is in the standard library (ADR-1) and only reads, so loading the file runs no
code; TOML allows comments, which JSON does not; a list of hosts does not fit well in one action
input.
**Trade-off.** A second format beside the workflow's YAML.

### ADR-5 — Python floor 3.12

**Decision.** `requires-python = ">=3.12"`; the CI tests every version from 3.12 to the newest
stable release (3.15 today), on Linux and on Windows.
**Alternatives.** (a) 3.13, which adds `SSLSocket.get_unverified_chain()`. (b) Only the newest
release.
**Why.** 3.12 is the system Python of Ubuntu 24.04 LTS, where `pipx install` runs on many servers,
and it has `tomllib`. Nothing in ADR-1 needs 3.13: the date is read from a verified chain only.
**Trade-off.** Syntax and library features newer than 3.12 wait; ruff, black and mypy target 3.12
so they cannot slip in. Reviewed when 3.12 reaches end of life (2028-10-31).

### ADR-6 — A composite Action that installs the code at its own commit

**Decision.** `action.yml` at the repository root describes a composite Action that installs the
package from `${{ github.action_path }}`, so the commit SHA in the user's `uses:` line pins the
code that runs. The final shape is decided in spec `006`.
**Alternatives.** (a) A Docker Action. (b) A JavaScript Action. (c) A composite Action that runs
`pipx install tlsvigil` from PyPI.
**Why.** The SHA in `uses:` already identifies every file of the Action, so installing from that
checkout adds no second thing to trust; a Docker image is a second artefact to build and sign, and
JavaScript would bring a runtime and dependencies of its own.
**Trade-off.** The Action needs a Python on the runner (GitHub-hosted runners have one) and builds
the package on every run.

### ADR-7 — Hatchling, with the README rewritten for PyPI at build time

**Decision.** Hatchling builds the package, and `hatch-fancy-pypi-readme` rewrites every relative
image and link of `README.md` to an absolute GitHub URL when the metadata is built.
**Alternatives.** (a) `uv_build`, the default of `uv init`. (b) Absolute links in `README.md`.
**Why.** Relative links are the ones GitHub resolves on every branch and fork, and the rewrite
gives PyPI working links from the same file; `uv_build` has no metadata hooks.
**Trade-off.** A build plugin to keep up to date, and the rewrite has to be tested (RF-08).

### ADR-8 — A 7-day cooldown on the lock and on Dependabot

**Decision.** `exclude-newer = "7 days"` in `[tool.uv]`, a 7-day `cooldown` in Dependabot, and the
Dependabot ecosystem `uv`, not `pip`.
**Alternatives.** (a) Take every release the day it comes out. (b) Pin by hand with no automation.
**Why.** Most hijacked releases are found and pulled within days; waiting a week costs little for
dev tooling. The `uv` ecosystem updates `uv.lock`, which `pip` would ignore.
**Trade-off.** A security fix younger than a week needs `exclude-newer-package` for its one
package.

### ADR-9 — The CI of a public repository, with only the quality jobs required

**Decision.** CI runs on pull requests, on pushes to `main` and by hand; CodeQL runs as a workflow
and dependency review on pull requests. The `proteger-main` ruleset requires a pull request, a
linear history and the `Quality (Python 3.12)` and `Quality (Python 3.15)` checks only.
**Alternatives.** (a) CodeQL's default setup. (b) Requiring every job, `pip-audit` and CodeQL
included.
**Why.** Minutes are free for a public repository, so the merge commit is checked too. A workflow
file is reviewed and versioned like code, which the default setup is not. A new CVE or a CodeQL
outage must not block the very pull request that fixes it, so only the deterministic quality jobs
are required.
**Trade-off.** A red `package`, `security` or CodeQL run does not block a merge by itself; the pull
request checklist and the reviewer catch it.

### ADR-10 — The pre-commit hook checks, it does not fix

**Decision.** The hook runs every tool in check mode and changes no file.
**Alternatives.** (a) Run `ruff --fix` and `black` in the hook and re-stage.
**Why.** A hook that rewrites files can commit a change nobody looked at; formatting happens while
editing (the Claude Code hook, or the editor), and the commit only verifies.
**Trade-off.** A formatting slip costs one more `black .` and another `git add`.

### ADR-11 — mypy strict over `src`, `tests` and `.claude/hooks`

**Decision.** `mypy` runs in strict mode over the package, the tests and the hooks.
**Alternatives.** (a) Strict over `src` only.
**Why.** The tests and the hooks are small, and typing them catches a wrong fixture or a hook that
reads the wrong JSON key at the cost of a few annotations.
**Trade-off.** A test helper needs full annotations like production code.

### ADR-12 — Two READMEs kept in step by structural tests

**Decision.** `README.md` (English) and `README.pt-BR.md` (Brazilian Portuguese) exist from the
first release, and a test compares their structure, never their wording.
**Alternatives.** (a) English only until there is traction. (b) A Portuguese section inside the
English README. (c) A translation check by hand.
**Why.** A repository is found by searches in both languages, and a Portuguese README that falls
behind describes an older tool. Comparing headings and table rows catches a section that was
added to one file only, without making every rewording a test failure.
**Trade-off.** Every change to the README is written twice; a change of wording in one language
only is not caught.

## Impact on existing code

The foundation already exists (pull request #1); this spec formalises it. Gaps found while writing
the design (2026-11-27), closed by the tasks, not by new design:

1. **RF-10 is not fully covered.** `tests/test_cli.py` has three tests (`--version`, the version
   from the metadata, no arguments). Missing: the unknown flag exits `2` (RF-02), `python -m
   tlsvigil --version` (RF-01), and the `__version__` fallback when the distribution is not
   installed (RF-01).
2. **RF-05's sdist content is unverified.** The `include` list is right on paper, but no run has
   listed the files of the built sdist.
3. **The new documentation tests have not been seen failing.** Each one has to go red once against
   a broken README, link or name before it is trusted.

No public interface changes; nothing needs migration.

## Test strategy

- **Unit:** `tests/test_cli.py`, calling `main(argv)` and asserting on `capsys` and
  `SystemExit.code`. The `__version__` fallback is tested by making `importlib.metadata.version`
  raise `PackageNotFoundError` and reloading the module (`monkeypatch` and `importlib.reload`),
  with a reload in teardown so other tests see the real value. `python -m tlsvigil` is run with
  `runpy.run_module`, in-process, so no subprocess is needed.
- **Documentation:** the three documentation test files read the READMEs, `pyproject.toml` and the
  output of `git ls-files`, and skip when those are missing (an sdist). Each negative check has a
  positive control: the link tests assert that they found links before asserting that none is
  broken, and the impersonality test asserts that the `CLAUDE.md` and `.claude/` selection is not
  empty.
- **Seen failing:** each new test is broken on purpose once before it is trusted: a heading
  removed from `README.pt-BR.md`, a link to a missing file, a substitution deleted from
  `pyproject.toml`, the name written into a versioned file, a personal command written into
  `CLAUDE.md`, the `argparse` exit code changed in the test's expectation. The result goes into
  the tasks and the pull request.
- **Not unit-testable:** the workflows and hooks are verified by their runs (the CI of every pull
  request, the hooks fed JSON on stdin), and the sdist by listing the archive after `uv build`.
- **No network:** none of these tests opens a socket; the guard that enforces it for every later
  test is spec `002`.

## Implementation notes

- **Suite:** 15 tests (6 for the CLI, 5 for the two READMEs, 2 for the links, 2 for
  impersonality), 100% coverage of `src/tlsvigil`; the full local sequence takes 4.2 s on Python
  3.15.2 (RNF-02).
- **The impersonality test reads the Git index**, not the working tree: a new file is checked from
  the moment it is staged. A name or a personal command written into an untracked file goes
  unnoticed until `git add`, which the pre-commit hook always follows.
- **The version fallback test reloads the package twice** (once with the metadata lookup patched,
  once after undoing it), so `tlsvigil.__version__` is the installed version again for the tests
  that run after it.
- **`python -m tlsvigil` runs through `runpy.run_module`** with `tlsvigil.__main__` removed from
  `sys.modules` first, because `runpy` warns about a module that is already imported and the suite
  turns warnings into errors.
- **The sdist** holds the package, `README.md`, `LICENSE`, `SECURITY.md`, `pyproject.toml`, and the
  `PKG-INFO` and `.gitignore` that Hatchling always adds.
