---
feature: The GitHub Action and the template repository — a daily check in the user's own repository, with no server and no secret to start
status: in progress
date: 2026-11-28
related:
  - 001-foundation/design.md
  - 005-notifications/design.md
origin: conception
---

# 006 — Action — design

## Overview

`action.yml` is a composite Action of three steps with no logic of its own: restore the state, run
the check, save the state. The middle step finds a Python 3.12 or later on the runner and runs a
new module of the package, `tlsvigil.action`, straight from the commit the user pinned, in isolated
mode and with nothing installed (ADR-1, ADR-2). That module reads the inputs from the environment,
validates them, builds the variables of spec `005` for the channels, calls the command's `main()` in
the same process (ADR-3), copies what it writes to the job's summary as plain text (ADR-5), writes
the step's outputs and turns the exit code into the result of the step (RF-04).

The state file of spec `005` lives in the runner's temporary directory, and the Actions cache keeps
it between runs: each run saves it under a key of its own, and the next run restores the newest one
by prefix (ADR-4). The template repository is a separate public repository; this repository keeps
the copy of its files that the tests read, under `template/` (ADR-7).

**The open question of the requirements, settled on a real repository (2026-11-28).** An issue and
a comment opened through the workflow's token, authored by `github-actions[bot]`, reach the owner by
e-mail and in the notification inbox when the owner watches the repository with "All Activity" or
with "Custom → Issues", and do not when the owner does not watch it: a repository created through
the API did not start watched. So the template's README and both READMEs tell the person to watch
the repository with "All Activity", which also brings the template's Dependabot pull requests (a
new release of TLSVigil) and, in a repository that only runs TLSVigil, little else; "Custom →
Issues" is the quieter choice for a busy repository that adds the Action to its own workflow
(RF-08, RF-11).

## Module layout

```text
action.yml                     # new: the composite Action — restore, check, save (RF-01 to RF-05, RF-10)
src/tlsvigil/
  action.py                    # new: the inputs, the channels' environment, the run, the summary,
                               #   the outputs and the step's exit code (RF-02 to RF-04, RF-06)
  i18n.py                      # changed: the action's messages in both languages
  cli.py                       # changed: the docstring of main(environ=…), now also used by action.py
template/                      # new: the files of the template repository, as they are published (ADR-7)
  .github/workflows/tlsvigil.yml
  .github/dependabot.yml
  tlsvigil.toml
  README.md
  README.pt-BR.md
tests/
  test_action.py               # new: action.main() against the local servers; inputs, exit, summary
  test_action_files.py         # new: action.yml, the template, the READMEs, read with PyYAML (ADR-6)
  data/action/refused.toml     # new: the configuration of the CI job (a closed port on 127.0.0.1)
.github/
  workflows/ci.yml             # changed: the "Action" job on Linux, Windows and macOS (RF-12)
  dependabot.yml               # changed: github-actions also in /template
pyproject.toml, uv.lock        # changed: pyyaml and types-pyyaml in the dev group (ADR-6)
specs/001-foundation/design.md # changed: ADR-6 gets "Superseded by 006-action, ADR-1."
README.md, README.pt-BR.md     # the GitHub Actions section (RF-11)
CLAUDE.md, docs/stack.md       # the Action is built: architecture, commands, the stack entry
```

## Data model

```python
# action.py

INPUT_PREFIX: Final = "TLSVIGIL_INPUT_"   # the step passes input "fail-on-attention" as
                                          # TLSVIGIL_INPUT_FAIL_ON_ATTENTION
STATE_DIR: Final = "TLSVIGIL_STATE_DIR"   # the directory the cache restores and saves
STATE_FILE: Final = "state.json"
MAX_SUMMARY_BYTES: Final = 512 * 1024     # GitHub takes up to 1 MiB per step

# Each channel input and the variable of spec 005, RF-03, it becomes (ADR-8).
CHANNEL_INPUTS: Final[Mapping[str, str]] = {
    "github-token": "GITHUB_TOKEN",
    "telegram-token": "TLSVIGIL_TELEGRAM_TOKEN",
    "telegram-chat-id": "TLSVIGIL_TELEGRAM_CHAT_ID",
    "smtp-username": "TLSVIGIL_SMTP_USERNAME",
    "smtp-password": "TLSVIGIL_SMTP_PASSWORD",
    "smtp-from": "TLSVIGIL_SMTP_FROM",
    "smtp-to": "TLSVIGIL_SMTP_TO",
}
SECRET_INPUTS: Final = frozenset({"github-token", "telegram-token", "smtp-password"})
INPUTS: Final = ("config", "lang", "notify", "fail-on-attention", *CHANNEL_INPUTS)


@dataclass(frozen=True)
class Inputs:
    config: str | None          # None: the command's default, tlsvigil.toml
    lang: Lang | None           # None: the file's lang
    notify: bool
    fail_on_attention: bool
    secrets: tuple[str, ...]    # the non-empty values of SECRET_INPUTS, to mask


class InputError(Exception):
    """An input outside its form; carries the input's name and a catalogue key, never the value."""
```

The state file is the one of spec `005` (its format, cap and version unchanged), at
`$TLSVIGIL_STATE_DIR/state.json`.

## Components

### The composite Action (RF-01, RF-05, RF-07, RF-10; ADR-1, ADR-2, ADR-4)

Three steps, in this order:

1. **Restore** (`actions/cache/restore`), only when `inputs.notify == 'true'` (an expression that
   ignores case, like the module's own reading): the path `${{ runner.temp }}/tlsvigil-state`, the
   key `tlsvigil-state-${{ github.run_id }}-${{ github.run_attempt }}`, the restore prefix
   `tlsvigil-state-`. A miss is not an error.
2. **Check** (`id: run`, `shell: bash`): every input reaches the step as `TLSVIGIL_INPUT_<NAME>` in
   its `env:`, with `TLSVIGIL_SOURCE` (`${{ github.action_path }}/src`) and `TLSVIGIL_STATE_DIR`.
   The script tries `python3`, then `python`, and keeps the first whose version is 3.12 or later;
   with none, it writes `exit-code=2` to the step's outputs, prints one `::error` line that says
   which version is needed and that a step with `actions/setup-python` before this one solves it,
   and exits `2` (RF-07). Otherwise it runs
   `"$python" -I -X utf8 -c '<bootstrap>' "$TLSVIGIL_SOURCE"`, where the bootstrap puts that
   directory first on `sys.path` and calls `tlsvigil.action.main()` (ADR-2).
3. **Save** (`actions/cache/save`), when
   `always() && inputs.notify == 'true' && steps.run.outputs.state == 'written'`: the same path and
   key. `always()` keeps the save after a failed check (exit `4`), so the channels that were told
   are not told again (spec `005`, RF-07); checked on GitHub-hosted runners on 2026-11-28, where a
   composite step with `if: always()` ran after a step that exited `4`, and the next run restored
   the newest key by its prefix.

The metadata carries `name: TLSVigil`, a one-sentence description, the author of the package,
`branding` (`icon: shield`, `color: blue`), a description and default for every input, and the
output `exit-code` (`${{ steps.run.outputs.exit-code }}`).

### The inputs (RF-02, RF-03; ADR-8)

`read_inputs(environ)` reads each `TLSVIGIL_INPUT_*` value and strips it:

- `config`: empty means `None`; any other text is a path, relative to the workspace.
- `lang`: empty means `None`; else `en` or `pt-BR` without regard to case, the same reading as
  `--lang`; anything else is `InputError("lang", "action.input_lang")`.
- `notify`, `fail-on-attention`: `true` or `false` without regard to case; anything else (an empty
  value included) is `InputError(name, "action.input_boolean")`.
- The channel inputs are not validated here: spec `005`, RF-03, validates the variables they become.

Every problem is found before any check, and each is one line on the standard error that names the
input and never holds its value; the step then ends with exit code `2`.

`channel_environment(environ)` returns the environment the command reads: a copy of `environ`
without any `TLSVIGIL_INPUT_*` name, where each non-empty channel input sets the variable
`CHANNEL_INPUTS` names for it, over any value inherited from the step's `env:`. An empty input sets
nothing, so a variable the step's `env:` set stays as it was, and a channel the file turns on with
no value reports it missing (RF-03).

### The run (RF-01, RF-04, RF-06; ADR-3, ADR-5)

`main(environ=None, *, check=_check) -> int`, in this order:

1. Read the inputs; on `InputError`, write the lines, the output `exit-code=2`, and return `2`.
2. Print `::add-mask::<value>` for each secret in `Inputs.secrets` (the secret store already masks
   its own values; this covers a token that came from elsewhere), then
   `::stop-commands::<token>`, where the token is `secrets.token_hex(16)` (ADR-5).
3. Build the arguments as a list: `check`, `--config=<path>` when given, `--lang=<lang>` when given,
   and, with `notify`, `--notify` and `--state=<STATE_DIR>/state.json` (the directory is created
   first). The `=` form keeps a value that starts with `-` a value.
4. Call `check(argv, channel_environment(environ))`, which calls the command's
   `main(argv, environ=…)`, while the standard output and the standard error go to a tee that
   writes each text to the real stream and keeps a copy, in the order written, up to
   `MAX_SUMMARY_BYTES`.
5. In a `finally`: flush both streams and print `::<token>::`, which turns workflow commands back
   on.
6. Write the summary (below), the outputs `exit-code=<n>` and, when the state file exists,
   `state=written`, each to the file its variable names (`GITHUB_STEP_SUMMARY`, `GITHUB_OUTPUT`);
   a variable that is not set (a run outside Actions) skips its file.
7. Return `step_result(code, fail_on_attention)`.

`step_result(code, fail_on_attention)` returns `0` for `0`, `0` for `1` unless `fail_on_attention`,
and the code itself otherwise; a non-zero value fails the step, and the log keeps the command's own
code.

**The summary.** A heading `### TLSVigil` and the copy of the run's output in one fenced code
block. The fence is a run of backticks one longer than the longest run of backticks in the text, and
never shorter than three, so no line of the text can close it (CommonMark, "Fenced code blocks");
inside it, nothing is read as Markdown, HTML, a link or a mention. A copy cut at `MAX_SUMMARY_BYTES`
ends at a whole line, followed by the catalogue's `action.summary_cut` line, inside the block. The
command's output already shows control characters as escapes (spec `004`, ADR-4).

### The template (RF-08, RF-09; ADR-7)

`template/` holds, as they are published in the template repository:

- `tlsvigil.toml`: `lang`, one host and one domain (`example.com`), `[notifications]` with
  `channels = ["github"]`, and commented lines for the other keys of specs `004` and `005`.
- `.github/workflows/tlsvigil.yml`, in full under "Interfaces".
- `.github/dependabot.yml`: `github-actions`, weekly, with a 7-day cooldown, so that the person
  receives a pull request when a new release of TLSVigil or of the checkout is out.
- `README.md` and `README.pt-BR.md`: what the repository does, the four steps (create from the
  template; edit `tlsvigil.toml` and commit, which runs a check; watch the repository with "All
  Activity"; optionally add the channels' secrets and uncomment their inputs), what the run's
  colour means (RF-04) and the limits of the platform (RF-11), with a link to this repository for
  the rest.

Until the first release, the workflow pins the commit of `main` that holds `action.yml`, with the
comment `# main`; preparing the release moves the pin to the release's commit and its tag.

### Both READMEs (RF-11)

A new `## GitHub Actions` section, between "Notifications" and "TLSVigil vs. other tools", with: the
template and its four steps; the workflow of "Interfaces" for an existing repository; the table of
inputs (one row per input of `action.yml`, in its order) and the output; the permissions; the
channels' secrets passed as inputs from `secrets.*`; the state in the Actions cache and what losing
it does (a current warning told once more); when the run fails (RF-04); the concurrency group for
a workflow of one's own; and the limits: late or dropped scheduled runs, 60 days without activity in
a public repository, a fork's schedule off, the minutes of a private repository, and the watch
setting of "Overview" ("All Activity"; "Custom → Issues" for a busy repository).

## Interfaces

**`action.yml`** (the Check step's script, whole; the other steps as in "Components"):

```bash
python=""
for candidate in python3 python; do
  if command -v "$candidate" >/dev/null 2>&1 &&
    "$candidate" -c 'import sys; sys.exit(sys.version_info < (3, 12))' >/dev/null 2>&1; then
    python=$candidate
    break
  fi
done
if [ -z "$python" ]; then
  echo "exit-code=2" >>"$GITHUB_OUTPUT"
  echo "::error title=TLSVigil::Python 3.12 or later was not found on the runner. Add a step with actions/setup-python before this one."
  exit 2
fi
exec "$python" -I -X utf8 -c 'import sys; sys.path.insert(0, sys.argv[1]); from tlsvigil.action import main; sys.exit(main())' "$TLSVIGIL_SOURCE"
```

**Inputs** (all strings, as GitHub passes them):

| Input | Default | Becomes |
|---|---|---|
| `config` | `tlsvigil.toml` | `--config=` |
| `lang` | empty (the file's) | `--lang=` |
| `notify` | `true` | `--notify --state=…`, and the cache steps |
| `fail-on-attention` | `false` | exit `1` fails the step |
| `github-token` | `${{ github.token }}` | `GITHUB_TOKEN` |
| `telegram-token`, `telegram-chat-id` | empty | `TLSVIGIL_TELEGRAM_TOKEN`, `TLSVIGIL_TELEGRAM_CHAT_ID` |
| `smtp-username`, `smtp-password` | empty | `TLSVIGIL_SMTP_USERNAME`, `TLSVIGIL_SMTP_PASSWORD` |
| `smtp-from`, `smtp-to` | empty | `TLSVIGIL_SMTP_FROM`, `TLSVIGIL_SMTP_TO` |

**Output:** `exit-code`, the command's exit code (`0` to `4`; `2` also for an invalid input and for a
runner without Python).

**The template's workflow:**

```yaml
name: TLSVigil

on:
  schedule:
    - cron: "17 7 * * *" # every day at 07:17 UTC; GitHub may start it late
  workflow_dispatch:
  push:
    branches: [main]
    paths: [tlsvigil.toml]

permissions:
  contents: read
  issues: write

concurrency:
  group: tlsvigil
  cancel-in-progress: false

jobs:
  check:
    runs-on: ubuntu-26.04
    timeout-minutes: 10
    steps:
      - uses: actions/checkout@<sha> # v7.0.1
        with:
          persist-credentials: false
      - uses: <owner>/tlsvigil@<sha> # main
        # with:
        #   telegram-token: ${{ secrets.TLSVIGIL_TELEGRAM_TOKEN }}
        #   … one line per channel input
```

**Workflow commands** the module prints: `::add-mask::`, `::stop-commands::` and its resume line;
the Check step's script prints `::error`. **Files** it writes: the summary and the outputs, through
`GITHUB_STEP_SUMMARY` and `GITHUB_OUTPUT`.

**`actions/cache`** `v6.1.0` (`restore` and `save`), pinned by commit SHA like every Action of the
repository; it uses the runner's own cache service, which needs no permission of the token (checked
on 2026-11-28 with `contents: read` and `issues: write` only).

## ADRs

### ADR-1 — The Action runs the source of its commit, with the runner's Python, and installs nothing

**Decision.** The Check step runs `src/` of the Action's checkout directly with the first Python
3.12 or later on the runner's path; no `pip install`, no build, no `actions/setup-python`.
**Alternatives.** (a) `pip install ${{ github.action_path }}`, as spec `001`, ADR-6, foresaw. (b)
`pipx install tlsvigil` from PyPI. (c) `actions/setup-python` with a fixed version, then (a).
**Why.** The package has no runtime dependency, so nothing needs installing; (a) would fetch the
build backend and its plugin from PyPI on every run, unpinned, which is code the SHA in `uses:` does
not cover; (b) trusts PyPI instead of the pinned commit; (c) adds a second Action and a download to
every run. Every GitHub-hosted runner has a Python that fits (checked on 2026-11-28: Ubuntu 24.04
3.12.3, Ubuntu 26.04 3.14.4, Windows Server 2022 and 2025 3.12.10, macOS 15 and 26 3.15.2).
**Trade-off.** `tlsvigil --version` is not what the step runs, and the version the package reports
from source is its placeholder; the pinned SHA is the version. A runner with an older Python fails
with a message (RF-07) instead of working.

### ADR-2 — The module runs in isolated mode

**Decision.** `python -I -X utf8 -c '<bootstrap>' <src>`.
**Alternatives.** (a) `PYTHONPATH=<src> python -m tlsvigil.action`. (b) `-P` alone.
**Why.** (a) puts the workspace (the user's repository) first on `sys.path`, so a `json.py` or an
`email/` there would replace the standard library's, and it reads `PYTHON*` variables an earlier step
may have set; `-I` ignores both, and the user's site-packages. (b) keeps the variables. `-X utf8`
replaces `PYTHONUTF8`, which `-I` ignores, so a Portuguese report reaches a Windows log intact.
**Trade-off.** A one-line bootstrap in `action.yml` instead of `-m`.

### ADR-3 — The module calls the command's `main()` in the same process

**Decision.** `action.main` calls `cli.main(argv, environ=…)` with a built environment and tees the
standard streams.
**Alternatives.** (a) A subprocess of `tlsvigil check`. (b) The whole run in the bash step.
**Why.** The channels' environment is a mapping handed to `main` (spec `005`, ADR-9), never written
to the process's environment or a file; the tests drive the module with the same seams as the
command; (a) needs a second interpreter and the same bootstrap again; (b) is untestable in the
suite and differs between the shells of Linux, Windows and macOS.
**Trade-off.** The module depends on `cli.main` writing to `sys.stdout` and `sys.stderr` at call
time, which it does; the tee implements `write`, `flush` and `fileno`.

### ADR-4 — The state is kept in the Actions cache, one entry per run, restored by prefix

**Decision.** Save under `tlsvigil-state-<run id>-<attempt>`; restore with the prefix
`tlsvigil-state-`, which picks the newest entry.
**Alternatives.** (a) A single fixed key. (b) An artifact of the previous run. (c) A commit
(requirements, resolved decision 4).
**Why.** A cache entry cannot be overwritten, so (a) would keep the first state forever unless the
run deleted it, which needs `actions: write`; (b) needs `actions: read` and the API to find the
previous run's artifact. The cache needs no permission of the token (RNF-06).
**Trade-off.** One entry of a few kilobytes per run; GitHub removes an entry not used for 7 days, so
the store holds about a week of them, and a schedule paused for more than 7 days starts again with
no state (a current warning told once more, never hidden). Entries are scoped to the branch: a run
on another branch reads the default branch's state but saves its own, so the next run on the
default branch may tell again what that run told.

### ADR-5 — Text from the run is fenced in the log and in the summary

**Decision.** The log of the run is wrapped in `::stop-commands::<random token>` and its resume; the
summary puts the output in a fenced code block whose fence is longer than any backtick run in it.
**Alternatives.** (a) Rely on the command's escapes. (b) A `<pre>` block with HTML escapes. (c)
Escape every Markdown character.
**Why.** The runner reads a line that starts with `::` as a command, and the output carries text
from certificates, RDAP answers and network errors; the escapes of spec `004` keep it on one line but
not off the start of one. A fenced block is never parsed (CommonMark), so no character needs
escaping, and the copy stays readable; (b) depends on which HTML GitHub keeps; (c) is fragile.
**Trade-off.** A workflow command the command itself might print is ignored; it prints none.

### ADR-6 — The YAML files are tested with PyYAML, a dev dependency

**Decision.** `pyyaml` and `types-pyyaml` join the dev group; the tests read `action.yml` and the
template's workflow with `yaml.safe_load`.
**Alternatives.** (a) Regular expressions over the text. (b) `ruamel.yaml`.
**Why.** "No `${{` in a `run:`" and "these triggers and permissions" need the structure, which (a)
guesses; PyYAML is the common reader and has types. The runtime stays without dependencies (spec
`001`, ADR-1); the `package` job still checks the wheel.
**Trade-off.** PyYAML reads YAML 1.1, where the key `on` is the boolean `True`; the tests read the
triggers under that key. The comments of `uses:` lines are not in the parsed data, so the version
comment is checked on the text.

### ADR-7 — The template's files live in this repository and are copied to the template repository

**Decision.** `template/` holds the template repository's files as published; the template
repository is created from it and updated by copying it.
**Alternatives.** (a) The template repository as the only copy. (b) A Git submodule.
**Why.** The tests that keep the template, `action.yml` and the READMEs in step (RF-08) need the
files here; (a) lets them drift; (b) adds a step to every clone for five small files.
**Trade-off.** Two copies, kept equal by hand when the template changes, which happens at a release.

### ADR-8 — One input per channel variable, mapped to the names of spec `005`

**Decision.** `github-token`, `telegram-token`, `telegram-chat-id`, `smtp-username`,
`smtp-password`, `smtp-from` and `smtp-to` are inputs; the module turns each non-empty one into the
variable of spec `005`, RF-03.
**Alternatives.** (a) Only the step's `env:` with the variables' own names. (b) One input holding
every setting.
**Why.** Inputs are listed in the Action's page and in the Marketplace, with a description, and an
unknown input is warned about by GitHub; (a) works too and keeps working (an inherited variable is
kept when its input is empty), but is invisible in the Action's metadata; (b) needs a format.
**Trade-off.** Seven inputs, and the names of the variables in two places; a test keeps
`CHANNEL_INPUTS`, `action.yml` and the READMEs equal.

## Impact on existing code

- `cli.py`: no change in behaviour; the docstring of `environ` says that the Action passes it too.
- `i18n.py`: the keys `action.input_boolean`, `action.input_lang` and `action.summary_cut`, in both
  languages (`tests/test_i18n.py` holds both to the same keys).
- `specs/001-foundation/design.md`: ADR-6 gains `**Superseded by** 006-action, ADR-1.`; its text
  stays.
- `pyproject.toml` and `uv.lock`: the two dev packages; the wheel and the sdist are unchanged
  (`action.py` is part of the package; `action.yml` and `template/` are in neither).
- `.github/workflows/ci.yml`: the "Action" job, not required by the ruleset, like CodeQL. CodeQL
  already analyses the `actions` language, which covers `action.yml` and the workflows.
- `.github/dependabot.yml`: the `github-actions` entry gains `/template`.
- `tests/test_readme_pt_br.py`: the new section's tables join the compared rows; the template's two
  READMEs are compared the same way.
- `tests/test_impersonality.py`: its scope reaches `template/`.

## Test strategy

**`tests/test_action.py`** (no network; the CLI's local servers and fakes of spec `005`):

- The inputs: each boolean form, an empty and an invalid boolean, each language form and an invalid
  one, several problems at once, a planted value that appears in no line; an empty `config` and
  `lang` leave their flags out; a `config` that starts with `-` stays a value.
- The environment: a non-empty input sets its variable over an inherited one; an empty input keeps
  an inherited one and sets nothing otherwise; no `TLSVIGIL_INPUT_*` reaches the command.
- `step_result` for every code with both values of `fail-on-attention`.
- The commands: the masks come before `::stop-commands::`, and the resume line comes last, after a
  crash of `check` too; the token differs between two runs.
- The summary: a text with a run of five backticks, HTML, a mention and a link sits inside a fence
  of six; a text over the cap is cut at a whole line with the note; no summary file when the
  variable is not set.
- The outputs: `exit-code` always; `state=written` only when the state file exists.
- The whole run through `action.main` with the seams of the command: against the local TLS server
  and `FakeGitHub`, a first run with no state directory opens the issue and writes the state; a
  second run with that state adds nothing; a planted invalid state file is read as empty (spec
  `005`, RF-07); a secret planted in every input appears in no output, summary or output file.

**`tests/test_action_files.py`** (PyYAML; ADR-6): every `uses:` of `action.yml`, of the template's
workflow and of the README's workflow pins 40 hexadecimal characters followed by a comment; no
`run:` holds `${{`; the inputs of `action.yml` are `action.INPUTS`, in order, each with a
description, and each reaches the Check step as its `TLSVIGIL_INPUT_*` name; the inputs table of
each README has exactly those rows; the workflow block of `README.md` equals the template's file;
the template's workflow has the three triggers and no `pull_request`, the two permissions, the
concurrency group without cancelling, and a timeout; the template's `tlsvigil.toml` loads with
`load_config`; the template's two READMEs have the same headings.

**Seeing each guard fail once:** remove `-I` from the bootstrap and plant a `json.py` in the working
directory of a subprocess run (the run breaks); shorten the fence by one (the fence test fails);
drop the `finally` (the resume test fails after the crash); put `${{ inputs.config }}` in the
`run:` (the files test fails); swap a SHA for a tag (the pin test fails); map exit `1` to a failure
(the `step_result` test fails). Each sabotage is undone and recorded in the implementation notes.

**The "Action" CI job** (RF-12), on `ubuntu-26.04`, `windows-2025` and `macos-26`, with no `uv`
and no Python set up: it checks out the repository and runs `uses: ./` five times with
`continue-on-error: true`, against `tests/data/action/refused.toml` (a host on a closed port of
`127.0.0.1`, which the command reports as an error that needs attention, without any network):
(1) `notify: false` → `exit-code` `1`, outcome `success`; (2) the same with `fail-on-attention:
true` → `1`, `failure`; (3) a missing `config` → `2`, `failure`; (4) `notify: maybe` → `2`,
`failure`; (5) `notify: true` with a file whose only channel is `telegram` and no Telegram input →
the restore runs and misses, then `2`, `failure`, and the save is skipped. A last step compares
every outcome and output and fails on any difference. No step reaches a channel, a real host or an
API.

**By hand, once, before the first release:** the template's workflow in a scratch repository,
pinned to the commit of `main`: an issue opened and then commented on across two runs, the state
restored on the second, the summary read on the run's page. The result goes to the implementation
notes.

## Deviations

1. **`parse_lang` moved to `i18n.py`** (RF-02): the reading of a language that `--lang` had in
   `cli.py` is now `i18n.parse_lang()`, used by the command and by `action.read_inputs()`, so the
   two accept the same spellings; `--lang` behaves as before.
2. **`input_variable(name)` is public** (ADR-8): the tests and the files check build the
   `TLSVIGIL_INPUT_*` names with it instead of repeating the rule.
3. **`summary(text, cut, lang)` is public** (ADR-5): the tests check the fence against texts
   of their own without running the step.
4. **Outside Actions, the state file is the command's default** (RF-05): with `notify` and no
   `TLSVIGIL_STATE_DIR`, the module passes `--state=.tlsvigil-state.json`, the file
   `tlsvigil check --notify` uses on its own; `action.yml` always sets the variable.
5. **An invalid input is one `::error` line per problem** (RF-02): `::error title=TLSVigil::`
   and the catalogue's sentence, which names the input and never holds its value, so each
   problem is also an annotation of the run.

## Implementation notes

**Stage 0.** `read_inputs()`, `channel_environment()` and `step_result()` in `action.py`, 48 tests
in `tests/test_action.py`. Each guard was seen failing once, then restored: the booleans read with
their case (3 tests red), an empty channel input setting an empty variable (2), the
`TLSVIGIL_INPUT_*` names left in the command's environment (1), exit `1` failing the step without
`fail-on-attention` (2). Gate: 1465 tests, 99% coverage.

**Stage 1.** The tee, the summary and `main()` in `action.py`, 35 more tests in
`tests/test_action.py`, the whole run among them (the real command against the local TLS server
and `FakeGitHub`). The tee's `close()` leaves the real stream alone: the collector closes the
tee after the run, and the default would flush a stream that may be closed by then. Each guard
was seen failing once, then restored: the fence shortened by one (3 tests red), the resume moved
out of the `finally` (1), the masks printed after `::stop-commands::` (3), `state=written`
without the file (1), the `--config` value as a separate argument (3), the copy not cut at a
whole line (3), the cap counted in characters (1), a split character decoded strictly (1), a
secret masked whole (1), an empty line masked (1), no state directory made (4), `fileno` of
another stream (1). Gate: 1502 tests, 99% coverage, `action.py` 100%.

**Stage 2.** `action.yml` with the script of "Interfaces" unchanged and `actions/cache` `v6.1.0`
pinned by SHA; 15 tests in `tests/test_action_files.py`, among them the bootstrap run in a
subprocess from a directory with a planted `json.py`, and its positive control: the same line
without `-I` runs the planted file. `action.yml` joins the authorship files of
`tests/test_impersonality.py`, because its `author:` names the package's author. Each guard was
seen failing once, then restored: a tag in place of the SHA (1 test red), `${{ inputs.config }}` in
the `run:` (1), the input `smtp-to` removed (3), the save without `always()` (1), `-I` removed
from the bootstrap (1). Gate: 1518 tests, 99% coverage. On the pull request, the "Action" job
went red on the three runners with one expected exit code changed, only that run differing, and
the cache restore of the fifth run missed; then green with the right value.
