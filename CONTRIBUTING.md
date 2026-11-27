# Contributing to TLSVigil

Thanks for your interest in improving TLSVigil. By taking part you agree to follow the
[Code of Conduct](CODE_OF_CONDUCT.md).

TLSVigil is not released yet, and its commands and configuration can still change; from `1.0.0`
it follows [Semantic Versioning](https://semver.org/). Open an issue before starting anything
large.

## Development

TLSVigil is managed with [uv](https://docs.astral.sh/uv/). `uv sync` installs the project and the
`dev` dependency group, which pins the tooling (`ruff`, `black`, `mypy`, `pytest`, `pytest-cov`).
The package itself has **zero runtime dependencies** and must stay that way: it runs in other
people's CI, so its supply chain is the interpreter and this code, and the CI fails if the built
wheel declares a dependency. TLS, RDAP and the notifications use the standard library only.

The setup and the quality gate commands are in the README's [Development](README.md#development)
section.

## Before opening a pull request

Run the full quality gate and make sure it is green: the pre-commit hook and the CI run the same
sequence. Activate the hook once per clone with `git config core.hooksPath .githooks`.

## Conventions

- **Language:** all code, comments, docstrings, commit messages and documentation are in English,
  except `README.pt-BR.md`. See [CLAUDE.md](CLAUDE.md).
- **Two READMEs:** `README.md` and `README.pt-BR.md` change together, in the same pull request;
  `tests/test_readme_pt_br.py` compares their headings and tables.
- **Commits:** [Conventional Commits](https://www.conventionalcommits.org/) (`feat:`, `fix:`,
  `docs:`, `refactor:`, `test:`, `chore:`). Pull requests are squash-merged.
- **Issues and PRs:** a bug or a feature starts as an issue (forms in `.github/ISSUE_TEMPLATE/`);
  the PR that resolves it says `Closes #N`, so the issue closes on merge. A trivial fix can go
  straight to a PR.
- **Design changes:** non-trivial features are designed as specs under [specs/](specs/) before
  implementation (requirements, design, tasks).

## Tests never reach the network

A test that talks to a real host or a real RDAP server is slow, breaks when that service changes
and sends traffic to someone else's infrastructure. Tests use a local server on `127.0.0.1` or a
recorded response, built in the test. Never put a real token or password in a test, a fixture or
an issue.

## Claude Code hooks

The repository ships [Claude Code](https://claude.com/claude-code) hooks in `.claude/`. If you open
the project with Claude Code, they format the file you edit, run ruff and mypy at the end of a turn
and block edits to `uv.lock`. They only call the local tooling, with no network access. They are
optional: nothing else depends on them.

## Reporting security issues

Do not open public issues for vulnerabilities in TLSVigil itself: see [SECURITY.md](SECURITY.md).
