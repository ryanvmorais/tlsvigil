---
feature: Foundation — package skeleton, local quality gate, CI, supply-chain hygiene and documentation guards
status: in progress
date: 2026-11-27
related:
  - 001-foundation/requirements.md
  - 001-foundation/design.md
origin: conception
---

# 001 — Foundation — Tasks

The skeleton was built from a checklist before this spec existed, so Stage 0 records what shipped
in pull request #1 (already checked). Stage 1 is the documentation written with this spec, Stage 2
closes the gaps the design found, and Stage 3 is the acceptance on GitHub.

## Stage 0 — Skeleton as shipped (PR #1, squash `87c0f0f`)

- [x] `pyproject.toml`, `.python-version`, `uv.lock`: metadata, `dependencies = []`, dev group,
  README rewrite for PyPI, ruff/black/mypy/coverage/pytest configuration, Hatchling build.
  — RF-01, RF-04, RNF-01, ADR-1, ADR-5, ADR-7, ADR-8, ADR-11
- [x] `src/tlsvigil/{__init__,__main__,cli,py.typed}`: `__version__` with fallback,
  `python -m tlsvigil`, `build_parser()`, `main(argv)`. — RF-01, RF-02
- [x] `tests/test_cli.py`: `--version`, the version from the metadata, no arguments (3 tests).
  — RF-10
- [x] `.gitignore`, `.gitattributes`: local configuration and secrets ignored, LF endings. — RNF-03
- [x] `.githooks/pre-commit` (mode 100755) and `core.hooksPath`. — RF-03, ADR-10
- [x] `.claude/settings.json` and the four hooks. — RF-07
- [x] `.github/workflows/ci.yml`: `quality` (3.12 to 3.15, Linux and Windows), `package`,
  `security`, `shell`. — RF-03, RF-04, RF-05, RF-06, ADR-9
- [x] `.github/workflows/{codeql,dependency-review,pip-audit}.yml` and `.github/dependabot.yml`.
  — RF-06, ADR-8, ADR-9
- [x] `SECURITY.md`. — RF-09
- [x] Quality gate. — CI green on PR #1 (15 of 15 checks), merged on 2026-11-27

## Stage 1 — Documentation

- [x] `README.md` and `README.pt-BR.md`: the tool as specified, with the status note, the check
  table, the comparison checked against the official pages, the limits and the development
  commands. — RF-08, ADR-12
- [x] `tests/test_readme_pt_br.py`: heading levels, check and comparison table rows, cross-links in
  the first four lines, relative links of the Portuguese README. — RF-08, RF-10, ADR-12
- [x] `tests/test_readme_links.py`: relative links of `README.md` name real paths, and none is left
  after the `fancy-pypi-readme` substitutions. — RF-08, RF-10, ADR-7
- [x] `tests/test_impersonality.py`: the maintainer's name only in authorship, no personal tool in
  `CLAUDE.md` or `.claude/`. — RF-08, RF-10
- [x] `CLAUDE.md`, `docs/stack.md`, `specs/README.md` and this spec. — RF-08
- [x] `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, `.github/ISSUE_TEMPLATE/`,
  `.github/pull_request_template.md`, `.github/CODEOWNERS`. — RF-09
- [x] See each documentation test fail once: remove a heading from `README.pt-BR.md`, remove a row
  from its comparison table, point a link at a missing file, delete the link substitution from
  `pyproject.toml`, write the maintainer's first name into a versioned file, write a personal
  command into `CLAUDE.md`; each run goes red on the test that guards it, then the change is
  undone. — RF-08, RF-10. Done on 2026-11-27: all nine went red. The two impersonality checks
  passed at first because the new files were not yet in the Git index (`git ls-files` lists only
  what is tracked), and went red once the files were staged; the `pyproject.toml` one was run with
  `uv run --no-sync`, because a sync rebuilds the package from the broken file before pytest starts.
- [x] Quality gate (`uv run ruff check .`, `uv run black --check .`, `uv run mypy`,
  `uv run pytest --cov`). — green on Python 3.15.2 on 2026-11-27: ruff and black clean, mypy no
  issues, 12 passed

## Stage 2 — Close the gaps found by the design

- [x] `tests/test_cli.py`: add `test_unknown_flag_exits_two`: `main(["--nope"])` raises
  `SystemExit` with code `2`, and `capsys` shows the usage message on stderr and nothing on stdout.
  — RF-02, RF-10
- [x] `tests/test_cli.py`: add `test_python_m_prints_the_same_version_line`: `runpy.run_module`
  on `tlsvigil` with `sys.argv` set to `--version` exits `0` and prints `tlsvigil <version>`.
  — RF-01, RF-10
- [x] `tests/test_cli.py`: add `test_version_falls_back_without_install`: patch
  `importlib.metadata.version` to raise `PackageNotFoundError`, `importlib.reload(tlsvigil)`,
  assert `__version__ == "0.0.0.dev0"`, and reload again in teardown so the other tests see the
  real value. — RF-01, RF-10
- [x] See each new test fail once: expect exit `1` for the unknown flag, call `main` with a
  different version line, change the fallback string; each goes red, then the change is undone.
  — RF-10. Done on 2026-11-27 by breaking the code, not the expectation: `parse_known_args` in
  `cli.main`, `main(['--help'])` in `__main__.py` and `"0.0.0"` as the fallback; each test went
  red alone.
- [x] `uv build`, then list the sdist: only `src/tlsvigil/`, `README.md`, `LICENSE`, `SECURITY.md`
  and `pyproject.toml` (plus the metadata Hatchling adds). — RF-05. The archive holds those, plus
  `PKG-INFO` and `.gitignore`, which Hatchling always adds; no tests, specs or CI files.
- [x] Measure the full local sequence on a developer machine (under a minute). — RNF-02. 4.2 s
  for ruff, black, mypy and pytest with coverage.
- [x] Quality gate (`uv run ruff check .`, `uv run black --check .`, `uv run mypy`,
  `uv run pytest --cov`). — green on Python 3.15.2 on 2026-11-27: ruff and black clean, mypy no
  issues, 15 passed, 100% coverage of `src/tlsvigil`

## Stage 3 — Acceptance on GitHub

- [x] The pull request of Stages 1 and 2 passes every job: `Quality` on 3.12 to 3.15 on Linux and
  Windows, `Package`, `Security (pip-audit)`, `Shell (shellcheck)`, CodeQL and dependency review.
  — RF-03, RF-04, RF-05, RF-06. Done on PR #2 (CI run 46070547105): 15 of 15 checks passed.
- [x] Prove the `package` guard bites: on a throwaway branch add a runtime dependency to
  `[project].dependencies`, confirm the job fails with the dependency-free message, then delete the
  branch (no PR merged). — RF-05, RNF-01. Done on 2026-11-27: run 46070582838 on a throwaway
  branch with `packaging>=24` failed `Package` with that message after `Requires-Dist: packaging>=24`;
  the `Quality` jobs failed earlier, at `uv sync --locked`, because the lock was left as it was. The
  branch was deleted.
- [ ] On GitHub, the issue chooser shows the two forms and the private vulnerability link, and no
  blank issue. — RF-09
- [x] Quality gate: the CI of the pull request, with the run recorded here. — green: CI run
  46070547105 on PR #2, 15 of 15 checks
