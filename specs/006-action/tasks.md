---
feature: The GitHub Action and the template repository — a daily check in the user's own repository, with no server and no secret to start
status: in progress
date: 2026-11-28
related:
  - 006-action/requirements.md
  - 006-action/design.md
origin: conception
---

# 006 — Action — Tasks

Stage 0 adds the YAML reader to the dev tooling and the pure part of `tlsvigil.action`: the inputs,
the channels' environment and the step's result. Stage 1 adds the run around the command: the tee,
the workflow commands, the summary and the outputs. Stage 2 writes `action.yml` and the CI job that
runs it on Linux, Windows and macOS. Stage 3 writes the template's files under `template/`, pinned to
the commit of `main` that Stage 2 merged, so it starts once Stage 2 is merged. Stage 4 documents the
Action, checks it once by hand in a scratch repository and closes the spec. Each stage adds the
sentences it needs to the catalogue, so `tests/test_i18n.py` keeps both languages complete.
Publishing the template repository from `template/` belongs to the release, not to this spec.

The quality gate of every stage is, in order: `uv run ruff check .`, `uv run black --check .`,
`uv run mypy`, `uv run pytest --cov`.

## Stage 0 — The inputs and the step's result

- [x] `pyproject.toml`, `uv.lock`: `pyyaml` and `types-pyyaml` in the dev group, with a comment
  that they read the workflow files in the tests only; `uv lock --check` passes and the `package`
  job's check that the wheel declares no dependency is unchanged. — ADR-6, RNF-01
- [x] `src/tlsvigil/action.py`: the module docstring (the glue of `action.yml`, not a command),
  `INPUT_PREFIX`, `STATE_DIR`, `STATE_FILE`, `MAX_SUMMARY_BYTES`, `CHANNEL_INPUTS`,
  `SECRET_INPUTS`, `INPUTS`, `Inputs` and `InputError`. — RF-02, RF-03, ADR-8
- [x] `src/tlsvigil/action.py`: `read_inputs(environ)`: `config` and `lang` empty as `None`, `lang`
  read like `--lang` (shared with `cli.py`, not copied), the two booleans as `true` or `false`
  without regard to case, every problem collected with the input's name and no value; the
  non-empty secrets kept for the masks. — RF-02
- [x] `src/tlsvigil/action.py`: `channel_environment(environ)`: a copy without any
  `TLSVIGIL_INPUT_*` name, each non-empty channel input over the inherited variable, an empty one
  setting nothing. — RF-03, ADR-8
- [x] `src/tlsvigil/action.py`: `step_result(code, fail_on_attention)`. — RF-04
- [x] `src/tlsvigil/i18n.py`: `action.input_boolean` and `action.input_lang` in both languages. —
  RF-02
- [x] `tests/test_action.py`: each boolean form, an empty and an invalid boolean, each language form
  and an invalid one, several problems at once, a planted value in no message; the environment
  cases of the design; `step_result` for every code with both values of `fail-on-attention`. —
  RF-02, RF-03, RF-04
- [x] See the new tests fail once: a boolean read with its case, an empty input that sets an empty
  variable, a `TLSVIGIL_INPUT_*` name left in the environment, exit `1` mapped to a failure; each
  goes red, then the change is undone. — RF-02, RF-03, RF-04
- [x] Quality gate. — ruff, black, mypy and pytest green (1465 tests, 99% coverage)

## Stage 1 — The run, the commands and the summary

- [x] `src/tlsvigil/action.py`: the tee over the standard output and error (`write`, `flush`,
  `fileno`), keeping one copy of both, in order, up to `MAX_SUMMARY_BYTES`. — RF-06, ADR-3
- [x] `src/tlsvigil/action.py`: the summary: the `### TLSVigil` heading, the fence one backtick
  longer than the longest run (three at least), the copy cut at a whole line with the
  `action.summary_cut` line; written only when `GITHUB_STEP_SUMMARY` is set. — RF-06, ADR-5
- [x] `src/tlsvigil/action.py`: `main(environ=None, *, check=_check)`: the inputs, the masks,
  `::stop-commands::` with a fresh token and its resume in a `finally`, the arguments as a list with
  the `--config=` and `--lang=` forms and, with `notify`, `--notify` and `--state=` in
  `STATE_DIR` (created first), the call of `cli.main(argv, environ=…)` under the tee, the outputs
  `exit-code` and `state=written` through `GITHUB_OUTPUT`, and the step's result. — RF-01, RF-02,
  RF-04, RF-05, RNF-03, ADR-3, ADR-5
- [x] `src/tlsvigil/i18n.py`: `action.summary_cut` in both languages. — RF-06
- [x] `src/tlsvigil/cli.py`: the docstring of `environ` says that the Action passes it too. — ADR-3
- [x] `tests/test_action.py`: the masks before `::stop-commands::`, the resume line last, after a
  crash of `check` too, and two runs with different tokens; a summary with a run of five backticks,
  HTML, a mention and a link inside a fence of six; a summary over the cap cut at a whole line with
  the note; no summary and no output file when their variables are not set; `exit-code` always and
  `state=written` only when the state file exists; a `config` that starts with `-` reaching the
  command as a value. — RF-04, RF-05, RF-06, RNF-02, ADR-5
- [x] `tests/test_action.py`: the whole run through `action.main` with the command's seams, against
  the local TLS server and `FakeGitHub`: a first run with no state directory opens the issue and
  writes the state; a second run with that state adds nothing; a planted invalid state file is read
  as empty; a secret planted in every input appears in no output, summary or output file. — RF-01,
  RF-05, RNF-03
- [x] See the new tests fail once: the fence shortened by one, the resume moved out of the
  `finally`, the masks printed after `::stop-commands::`, `state=written` written without the file,
  the `--config` value passed as a separate argument; each goes red, then the change is undone. —
  RF-05, RF-06, ADR-5; the other guards too (implementation notes)
- [x] Quality gate. — ruff, black, mypy and pytest green (1502 tests, 99% coverage; `action.py` 100%)

## Stage 2 — `action.yml` and the CI job

- [x] `action.yml`: the metadata (name, description, author, branding), the inputs in the order of
  `action.INPUTS` with their descriptions and defaults, the output `exit-code`, and the three steps
  of the design: the restore and the save of `actions/cache` `v6.1.0` pinned by SHA with the key and
  the prefix of ADR-4, and the Check step with the script of "Interfaces". — RF-01, RF-05, RF-07,
  RF-10, RNF-01, RNF-02, ADR-1, ADR-2, ADR-4
- [x] `tests/test_action_files.py`: for `action.yml`: every `uses:` pinned to 40 hexadecimal
  characters and a version comment, read on the text; no `run:` holding `${{`; the inputs equal to
  `action.INPUTS`, in order, each with a description, each reaching the Check step as its
  `TLSVIGIL_INPUT_*` name; the output's value from the Check step; the save step under `always()`.
  — RF-10, RNF-01, RNF-02, RF-12
- [x] `tests/test_action_files.py`: the bootstrap of the Check step, run in a subprocess from a
  directory that holds a planted `json.py`, still runs `tlsvigil.action` (`-I` keeps the workspace
  off `sys.path`). — ADR-2
- [x] `tests/data/action/refused.toml` and `tests/data/action/telegram.toml`: a host on a closed
  port of `127.0.0.1`; the second with `channels = ["telegram"]`. — RF-12
- [x] `.github/workflows/ci.yml`: the "Action" job on `ubuntu-26.04`, `windows-2025` and
  `macos-26`, with a checkout without persisted credentials, the five runs of the test strategy with
  `continue-on-error: true`, and a last step that compares every outcome and `exit-code` and fails on
  any difference; a comment at the top saying it reaches no channel, real host or API. — RF-04,
  RF-07, RF-12, RNF-04, RNF-05
- [x] `specs/001-foundation/design.md`: ADR-6 gains `**Superseded by** 006-action, ADR-1.` below its
  title. — ADR-1
- [x] See the new tests fail once: a tag in place of a SHA, `${{ inputs.config }}` in the `run:`,
  an input missing from `action.yml`, the save without `always()`, `-I` removed from the bootstrap;
  each goes red, then the change is undone. On the pull request, the "Action" job is seen red once
  with an expected outcome changed, then green. — RF-12, ADR-2
- [x] Quality gate. — ruff, black, mypy and pytest green (1518 tests, 99% coverage)

## Stage 3 — The template

- [x] `template/tlsvigil.toml`: `lang`, one host and one domain, `[notifications]` with the `github`
  channel, commented lines for the other keys of specs `004` and `005`. — RF-08, ADR-7
- [x] `template/.github/workflows/tlsvigil.yml`: the workflow of "Interfaces", the checkout and the
  Action pinned by SHA (the Action at the commit of `main` that Stage 2 merged, `# main`), the
  channel inputs commented. — RF-08, RF-09, RNF-06, RNF-07
- [x] `template/.github/dependabot.yml`: `github-actions`, weekly, 7-day cooldown. — RF-08
- [x] `template/README.md` and `template/README.pt-BR.md`: what the repository does, the four steps
  (watch with "All Activity"), the colour of the run, the limits of the platform, a link to this
  repository. — RF-08, RF-11
- [x] `.github/dependabot.yml`: the `github-actions` entry covers `/template/.github/workflows`
  (deviation 6). — RF-08
- [x] `tests/test_action_files.py`: the template's workflow pinned like `action.yml`, with the three
  triggers, no `pull_request`, the two permissions, the concurrency group without cancelling and a
  timeout; the template's `tlsvigil.toml` loading with `load_config`; the template's two READMEs
  with the same headings. — RF-08, RF-09, RF-12
- [x] `tests/test_impersonality.py`: its scope reaches `template/`. — RF-08
- [x] See the new tests fail once: a `pull_request` trigger, `cancel-in-progress: true`, a
  `contents: write` permission, a key the configuration does not know, a heading missing from the
  Portuguese template README; each goes red, then the change is undone. — RF-09, RF-12
- [x] Quality gate. — ruff, black, mypy and pytest green (1529 tests, 99% coverage)

## Stage 4 — Documentation, the check by hand and the close of the spec

- [ ] `README.md` and `README.pt-BR.md`: the `## GitHub Actions` section of the design, its
  workflow equal to the template's, the inputs table equal to `action.yml`, the watch setting ("All
  Activity"; "Custom → Issues" for a busy repository) and the limits of the platform;
  `tests/test_readme_pt_br.py` compares the new tables, and `tests/test_action_files.py` checks the
  workflow block and the inputs rows of both; `tests/test_readme_config.py` and
  `tests/test_readme_links.py` pass. — RF-11, RF-12
- [ ] `CLAUDE.md`: the overview (the Action is built), the architecture with `action.yml`,
  `action.py` and `template/`, the "Before you touch" rows for them, and the quality line for the
  "Action" job. — RF-11
- [ ] `docs/stack.md`: the composite Action and the Actions cache in use, PyYAML among the dev
  tools. — RF-11
- [ ] By hand, in a scratch repository: the template's files, pinned to the commit of `main`; two
  runs: the issue opened on the first and commented on or left alone on the second, the state
  restored on the second, the summary read on the run's page; the result recorded in the
  implementation notes. — RF-01, RF-05, RF-06, RF-09
- [ ] `specs/006-action/`: `status: done` in the three files, the deviations and the implementation
  notes recorded; `specs/README.md`: the roadmap row with the delivery `v0.6` and the status `done`;
  the traceability check (every RF, RNF and ADR cited exists, every RF has a task). — RF-11
- [ ] Quality gate. —
