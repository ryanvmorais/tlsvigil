---
feature: Foundation — package skeleton, local quality gate, CI, supply-chain hygiene and documentation guards
status: done
date: 2026-11-27
related: []
origin: conception
---

# 001 — Foundation

## Context and problem

TLSVigil will be an open-source monitor that warns before a TLS certificate or a domain expires,
from a command line and from a GitHub Action that runs in the user's own repository. Before any of
that exists, every later spec needs the same ground to stand on: a package that installs, a single
quality sequence that means the same thing on a laptop and in CI, and a pipeline that keeps the
dependency-free promise honest. The tool will run in other people's CI with access to their
repository token, so the supply chain is part of the product, not an afterthought.

Today (2026-11-27) the repository holds that ground in its first form: a `src/tlsvigil` package
with a `--version` entry point and its tests, the `ruff → black → mypy → pytest` configuration in
`pyproject.toml`, a versioned pre-commit hook, Claude Code hooks, a CI workflow with `quality`,
`package`, `security` and `shell` jobs, CodeQL, dependency review, a weekly `pip-audit` workflow,
Dependabot, and the two READMEs with their tests. It was built from a checklist and has never been
written down as a contract. This spec states what the foundation guarantees, so that later specs
can rely on it and a change to it is a deliberate one.

The repository is public from the first commit: Actions minutes are free, and the `proteger-main`
ruleset requires a pull request and the `Quality` checks before anything reaches `main`.

## Goals

- A **zero-dependency, typed package** that installs from a wheel and runs `tlsvigil --version`.
- **One quality sequence** (ruff, black, mypy strict, pytest with coverage) that runs identically
  as a git pre-commit hook and in CI.
- A **tested Python range**: every version the classifiers claim, from the floor to the newest
  stable release, runs the full sequence on Linux and on Windows.
- A **package check** in CI that proves the wheel is what a release would ship, including that it
  declares no runtime dependency and that its README renders on PyPI.
- **Dependency and supply-chain hygiene**: a locked graph with a 7-day cooldown, weekly update PRs,
  a weekly CVE audit, CodeQL, dependency review, and every action pinned by commit SHA.
- **Documentation that cannot drift silently**: the two READMEs keep the same shape, the README
  links resolve on GitHub and on PyPI, and versioned files speak to whoever cloned the repository.
- **Guard rails for contributors and for Claude Code**: the lock file cannot be hand-edited, edited
  Python is formatted, and lint or type errors are caught before a turn ends.

## Non-goals

- Any monitoring logic (the certificate check, RDAP, the configuration, the output, the
  notifications, the Action): specs `002` to `006`.
- The offline network fixture for tests (a local TLS server and a guard that refuses any address
  other than `127.0.0.1`): spec `002`, the first spec whose code opens a socket.
- Publishing: PyPI, GitHub Releases, the Marketplace listing, the release workflow, trusted
  publishing. Publishing is the maintainer's act and belongs to the release preparation.
- A logo and a social preview image.
- A `CHANGELOG.md` (only worth having once a version is published).

## Personas

- As a **maintainer**, I want a local gate and a CI that agree, so that a green check means the
  same as a clean checkout and a broken change never reaches `main`.
- As a **contributor**, I want one documented command sequence and a hook that runs it, so that I
  can tell whether my change is ready without reading the CI configuration.
- As a **user**, I want to install one small wheel with nothing else pulled in, so that the code
  running in my CI with my repository token is the interpreter and this package only.
- As a **Portuguese-speaking reader**, I want the Portuguese README to describe the same tool as
  the English one, so that I am not reading about an older version.

## Functional requirements

### RF-01 — Installable, typed, dependency-free package

- **Given** a clean environment
  **When** the built wheel is installed
  **Then** the `tlsvigil` command exists, `tlsvigil --version` prints `tlsvigil <version>` on
  standard output and exits with `0`.
- **Given** the built wheel
  **When** its metadata is inspected
  **Then** it contains no `Requires-Dist` line, and the package ships `py.typed`.
- **Given** a source tree without an installed package
  **When** `tlsvigil.__version__` is read
  **Then** it falls back to a development version instead of raising.
- **Given** an installed package
  **When** `python -m tlsvigil --version` runs
  **Then** it prints the same line as the `tlsvigil` script.

### RF-02 — Testable command-line entry point

- **Given** the entry point `tlsvigil.cli.main`
  **When** it is called with an explicit `argv` list and no arguments
  **Then** it returns `0` without reading `sys.argv`.
- **Given** an unknown flag
  **When** `main` runs
  **Then** the process exits with code `2` and the usage message goes to standard error.

### RF-03 — One quality sequence

- **Given** the project configuration in `pyproject.toml`
  **When** the sequence runs
  **Then** it executes, in this order: `ruff check .`, `black --check .`, `mypy` (strict, over
  `src`, `tests` and `.claude/hooks`), `pytest --cov`, and the first failing step stops it with a
  non-zero exit code.
- **Given** a staged change that fails any step
  **When** `git commit` runs with `core.hooksPath` set to `.githooks`
  **Then** the pre-commit hook aborts the commit and prints which step failed.
- **Given** the same commit
  **When** the CI `quality` job runs it
  **Then** it runs the same four steps in the same order.
- **Given** a warning raised during the tests
  **When** pytest runs
  **Then** the run fails (`filterwarnings = ["error"]`), and the 15 slowest tests are listed at the
  end.

### RF-04 — Tested Python range

- **Given** `requires-python = ">=3.12"` and the classifiers for 3.12 to 3.15
  **When** the CI `quality` job runs
  **Then** its matrix covers each of those versions on Linux (`ubuntu-26.04`) and on Windows, and
  every combination must pass.
- **Given** syntax newer than Python 3.12
  **When** ruff, black or mypy runs
  **Then** it fails, because their targets are set to the floor (`py312`, `python_version =
  "3.12"`).

### RF-05 — Package verification in CI

- **Given** a pull request
  **When** the CI `package` job runs
  **Then** it builds the sdist and the wheel, `twine check --strict` passes on both, the wheel
  declares no runtime dependency, and the wheel installs in a clean environment where
  `tlsvigil --version` runs.
- **Given** a change that adds a runtime dependency by accident
  **When** the `package` job runs
  **Then** it fails with a message saying TLSVigil must stay dependency-free.
- **Given** the sdist
  **When** its file list is read
  **Then** it holds the package, `README.md`, `LICENSE` and `SECURITY.md`, and no tests, specs, CI
  files or local caches.

### RF-06 — Dependency and supply-chain hygiene

- **Given** `uv.lock`
  **When** `uv lock --check` runs
  **Then** it passes: manifest and lock are committed together and aligned, and the CI installs
  with `uv sync --locked`.
- **Given** a release younger than 7 days
  **When** `uv lock` resolves
  **Then** it is left out (`exclude-newer = "7 days"`).
- **Given** the repository on GitHub
  **When** a week passes
  **Then** Dependabot has looked at `uv` and `github-actions`, grouping minor and patch updates and
  waiting 7 days after a release before proposing it.
- **Given** the lock
  **When** the weekly `pip-audit` workflow (Mondays, 09:00 in São Paulo) or the CI `security` job
  runs
  **Then** it audits the whole locked graph, and a known vulnerability makes the run fail.
- **Given** a pull request or a push to `main`
  **When** CodeQL and dependency review run
  **Then** CodeQL analyses the Python code and the workflows, and dependency review fails a pull
  request that adds a dependency with a known vulnerability of high severity or above.
- **Given** any workflow file
  **When** it is read
  **Then** every `uses:` is pinned to a full commit SHA with the version in a comment, every Linux
  job runs on `ubuntu-26.04` (never `ubuntu-latest`), the default permissions are
  `contents: read`, and checkout does not persist the token.

### RF-07 — Generated files and edits are guarded

- **Given** a Claude Code session in this repository
  **When** an edit or write targets `uv.lock`
  **Then** the `guard_generated` hook exits with code `2` and says to use `uv lock`, `uv add` or
  `uv remove`.
- **Given** an edited `.py` file
  **When** the edit is saved
  **Then** the `project_format` hook runs `ruff check --fix` and `black` on that file.
- **Given** a turn that changed a `.py` file under `src/`, `tests/` or `.claude/hooks/`
  **When** the turn ends
  **Then** the `validate` hook runs ruff and mypy and hands any error back before the turn closes.
- **Given** a new session
  **When** `.venv` is missing or older than `uv.lock`, or `core.hooksPath` is not `.githooks`
  **Then** the `session_start` hook prints a warning and never blocks the session.

### RF-08 — Documentation guards

- **Given** `README.md` and `README.pt-BR.md`
  **When** the tests run
  **Then** both have the same heading levels in the same order, the same number of rows in the
  check table and in the comparison table, and each links to the other in its first four lines.
- **Given** a relative link in `README.pt-BR.md` or `README.md`
  **When** the tests run
  **Then** it names a path that exists in the repository.
- **Given** the substitutions in `[tool.hatch.metadata.hooks.fancy-pypi-readme]`
  **When** the tests apply them to `README.md` the way the build does
  **Then** no relative link is left, so every link works on PyPI.
- **Given** the versioned files
  **When** the tests run
  **Then** the maintainer's first name appears only in authorship (`LICENSE`, `pyproject.toml`),
  and `CLAUDE.md` and `.claude/` point at no tool or configuration that exists only outside the
  repository.

### RF-09 — Community and security files

- **Given** the public repository
  **When** a visitor looks for how to contribute or report a problem
  **Then** `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md` (Contributor Covenant 3.0) and `SECURITY.md`
  exist; the issue forms cover bugs and features, blank issues are off, and the vulnerability link
  goes to private vulnerability reporting.
- **Given** `SECURITY.md`
  **When** it is read
  **Then** it states the supported versions, the private reporting channel and its response times,
  the disclosure policy, the authorized use of a passive monitor and the scope (a vulnerability in
  TLSVigil, not an expiring certificate it reports).

## Non-functional requirements

### RNF-01 — Zero runtime dependencies

The package shall declare `dependencies = []`. TLS, RDAP, the configuration, the output and the
notifications use the standard library only. A change to this is a new ADR, never a side effect.

### RNF-02 — Fast local gate

The full sequence shall run in under a minute on a developer machine with the foundation's test
suite, so that the pre-commit hook is not skipped for being slow.

### RNF-03 — Tests leave no trace and reach no network

The foundation's tests shall read files only (the READMEs, `pyproject.toml`, `git ls-files`), write
nothing, and open no socket. A test that needs a file the sdist does not ship is skipped there.

## Open questions

None.

## Resolved decisions

1. **Public from day zero** (2026-11-27): the security baseline, the community files and the
   ruleset exist before the first feature, because the first commit of a public repository is
   already visible.
2. **Two READMEs from the start** (2026-11-27): `README.md` in English, which PyPI shows, and
   `README.pt-BR.md` for the Brazilian audience, kept in step by RF-08.
3. **The README describes the tool as specified** (2026-11-27), with a status note saying that only
   `tlsvigil --version` runs until the first release; each later spec updates both READMEs when it
   delivers.

## Tests

### RF-10 — Foundation test suite

The suite shall cover RF-01, RF-02 and RF-08 with tests that run in-process: the version line and
exit code, the version taken from the package metadata, the no-argument call, the unknown flag, the
two-README comparison, the link checks and the impersonality checks. RF-03 to RF-07 and RF-09 are
checked by the CI run itself and by reading the files, and the design records how each one is
verified.
