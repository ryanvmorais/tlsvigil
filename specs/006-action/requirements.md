---
feature: The GitHub Action and the template repository — a daily check in the user's own repository, with no server and no secret to start
status: done
date: 2026-11-28
related:
  - 001-foundation/design.md
  - 005-notifications/requirements.md
origin: conception
---

# 006 — Action

## Context and problem

Specs `002` to `005` delivered the command: `tlsvigil check --notify` reads a `tlsvigil.toml`, runs
the checks, keeps one GitHub issue current and, when asked, sends Telegram and e-mail, remembering
in a state file what each channel was told. Nothing runs it yet. A person who wants a daily check
today needs a server with `cron`, a Python and a place to keep the state file, which is what the
product promises to spare them.

Spec `001` chose where it runs (ADR-3 and ADR-6): a scheduled workflow in the user's own
repository, through a composite Action at the root of this repository, pinned by commit SHA. Spec
`005` left three things to this spec: the workflow that passes the token and the permission, keeping
the state file between runs (its ADR-2), and never running two checks of the same repository at
once.

Four facts about GitHub Actions shape it. A scheduled run can start late or be dropped under load,
so a daily check is best effort (spec `005` already tolerates a skipped run). A fork comes with its
scheduled workflows turned off and cannot be made private, while a repository created from a
template starts with them on. In a public repository, scheduled workflows are turned off after 60
days without activity. And a secret is never passed to a workflow run triggered from a fork.

## Goals

- A **composite Action** in `action.yml` that runs `tlsvigil check --notify` on the user's
  configuration, from the code of the commit it is pinned to.
- **No secret to start:** with the default GitHub issue channel, the workflow's own token and one
  permission are enough.
- **The state kept between runs**, so that spec `005` sends a warning once and its reminders, not
  every day.
- A **template repository** that a person turns into a working daily check by editing one file.
- **A warning is not a failed run:** the job goes red only when something is broken or a warning
  could not be delivered.
- **Ready for the GitHub Marketplace**, without publishing it.

## Non-goals

- Publishing: the release tag, the Marketplace listing and the PyPI package are the maintainer's
  acts at release time.
- A Docker or JavaScript Action, and a moving tag (`v1`) in the examples: they pin a commit SHA.
- More than one configuration per repository (the issue and the state belong to the repository).
- Runs triggered from a pull request or a fork, which have no secrets and a read-only token.
- Keeping a scheduled workflow alive past GitHub's inactivity rule (an automatic commit, for
  instance), and any other runner than the ones of RNF-05 (GitHub Enterprise Server is excluded by
  spec `005`).
- Annotations, pull-request comments, badges and other outputs of the run besides those of RF-04
  and RF-06.

## Personas

- As a **site owner** with no server, I want to click "Use this template", list my sites in one
  file and get an issue when a certificate or a domain is about to expire, so that I am warned
  without running anything.
- As a **site owner**, I want the run to stay green while it warns me through the issue, so that I
  do not get a second e-mail every day about a "failed" workflow.
- As a **maintainer of a workflow** that already exists, I want to add one step pinned by SHA, so
  that I can review the exact code that runs with my token.
- As a **cautious user**, I want the Action to ask for the least permission and to download nothing
  at run time, so that pinning one commit is all I have to trust.
- As a **Brazilian user**, I want the README and the template in Portuguese too, so that I can set
  it up for a client.

## Functional requirements

### RF-01 — The Action runs the check and delivers

- **Given** a workflow step with `uses: <owner>/tlsvigil@<commit SHA>` after a checkout of the
  user's repository
  **When** the step runs
  **Then** it runs `tlsvigil check --notify` on the configuration of RF-02, with the code of that
  commit and nothing else: no package is installed from PyPI or from any index.
- **Given** the step
  **When** it ends
  **Then** the text report of spec `004` and the delivery lines of spec `005`, RF-12, are in the
  log of the step, and the result follows RF-04.
- **Given** `notify: false`
  **When** the step runs
  **Then** it runs `tlsvigil check` without `--notify`: no state is restored or saved and no channel
  is reached, as in spec `005`, RF-01.

### RF-02 — The inputs

- **Given** the step
  **When** it is configured
  **Then** it takes, all optional: the path of the configuration (default `tlsvigil.toml`), the
  language (overriding the file, as `--lang` does), `notify` (default `true`), `fail-on-attention`
  (default `false`, RF-04), the GitHub token (default the workflow's own token) and one input per
  variable of the Telegram and SMTP channels of spec `005`, RF-03; the design names each input.
- **Given** an input with a value outside its form (a `notify` that is neither `true` nor `false`,
  a language the command does not know)
  **When** the step runs
  **Then** it fails before any check with a message that names the input, never its value.
- **Given** a configuration file that is missing or invalid
  **When** the step runs
  **Then** the step fails with the errors of spec `004`, RF-05, in its log (exit code `2`).

### RF-03 — Secrets reach the command through the environment only

- **Given** a token, a password, a chat or an address passed as an input
  **When** the step runs the command
  **Then** the value reaches the command only as the environment variable that spec `005`, RF-03,
  names for it, and never as an argument, in a file or in the text of a script.
- **Given** an input left empty
  **When** the step runs
  **Then** its variable is not set, so that a channel the file turns on reports it missing (spec
  `005`, RF-03), and a channel the file does not turn on never reads it.

### RF-04 — The result of the job

- **Given** a run that ends with exit code `0` (all fine) or `1` (needs attention, delivered)
  **When** `fail-on-attention` is `false`
  **Then** the step succeeds: the warning is the issue or the message, not a red run.
- **Given** exit code `1` and `fail-on-attention: true`
  **When** the step ends
  **Then** the step fails.
- **Given** exit code `2` (nothing checked), `3` (a bug) or `4` (a warning not delivered)
  **When** the step ends
  **Then** the step fails, whatever `fail-on-attention` says, so that a broken setup or an
  undelivered warning still reaches the person through GitHub's own notice of a failed run.
- **Given** any run
  **When** the step ends
  **Then** an output of the step holds the exit code of the command, for the steps after it.

### RF-05 — The state survives between runs

- **Given** `notify: true`
  **When** the step starts
  **Then** the state file of the latest earlier run of the same repository is restored, from
  storage that GitHub Actions keeps for the repository, without a commit and without a permission
  of the token beyond those of RNF-06.
- **Given** a run that ends, whether the deliveries succeeded, a delivery failed (exit `4`) or the
  checks found something
  **When** the step ends
  **Then** the state file the command wrote is saved for the next run.
- **Given** no state to restore (the first run, or one the storage no longer keeps)
  **When** the step runs
  **Then** the command runs as its first run (spec `005`, RF-07): a current warning can be told
  again once, never hidden.
- **Given** a state restored from that storage
  **When** the command reads it
  **Then** it is validated as any other state file (spec `005`, RF-07), so a damaged or planted file
  is read as empty, never trusted.

### RF-06 — The summary of the run

- **Given** a step that ran the checks
  **When** it ends
  **Then** the summary of the workflow run shows the text report and the delivery lines.
- **Given** text from a certificate, an RDAP answer or the configuration in that report
  **When** the summary is written
  **Then** it shows as plain text, never as Markdown, HTML, a link or a mention, whatever characters
  it holds.

### RF-07 — The Python on the runner

- **Given** a runner with Python 3.12 or later on its path (every runner of RNF-05)
  **When** the step runs
  **Then** it uses that Python and installs no other.
- **Given** a runner without it (a self-hosted runner, for instance)
  **When** the step runs
  **Then** it fails before any check with a message that says which version it needs and that a
  step that sets up Python before it solves it.

### RF-08 — The template repository

- **Given** the template repository, separate from this one
  **When** a person creates a repository from it ("Use this template"), public or private
  **Then** the new repository holds a `tlsvigil.toml` with commented examples, the workflow of RF-09,
  a Dependabot configuration that keeps the pinned Actions up to date, and a README in English and
  in Brazilian Portuguese that says what to edit.
- **Given** the new repository
  **When** the person edits `tlsvigil.toml` on the default branch
  **Then** a check runs right away, so that the setup is confirmed without waiting for the schedule.
- **Given** the files of the template
  **When** this repository is tested
  **Then** a copy of them in this repository (the design sets where) is checked against `action.yml`
  and the READMEs (RF-12), so that the template and the Action cannot drift apart.

### RF-09 — The template's workflow

- **Given** the workflow of the template
  **When** it is read
  **Then** it runs once a day on a schedule at a minute that is not the top of the hour, by hand
  (`workflow_dispatch`), and on a push to the default branch that changes `tlsvigil.toml`; never on a
  pull request.
- **Given** the workflow
  **When** it runs
  **Then** it asks for `contents: read` and `issues: write` and nothing else, has a job timeout, and
  never runs two checks of the repository at once: a second run waits for the first instead of
  cancelling it.
- **Given** every `uses:` line of the workflow
  **When** it is read
  **Then** it pins a full commit SHA, with the version it stands for in a comment.

### RF-10 — Ready for the Marketplace

- **Given** `action.yml` at the root of this repository
  **When** it is read
  **Then** it is the only Action of the repository and holds a name, a description, an author, the
  branding (icon and colour) and a description for every input and output.

### RF-11 — Both READMEs show the Action

- **Given** `README.md` and `README.pt-BR.md`
  **When** this spec is done
  **Then** both show the template and a workflow that uses the Action, pinned by SHA; the table of
  inputs and the output; the permissions and how to pass the optional channels' secrets; how the
  state is kept and what losing it does; when the job fails (RF-04); and the limits of the platform:
  a scheduled run can be late or dropped, a public repository without activity for 60 days loses
  its schedule until it is turned on again, a fork starts with its schedule off, and a private
  repository spends the account's Actions minutes.
- **Given** both READMEs
  **When** the documentation tests run
  **Then** the parity, configuration and link tests of specs `001` and `004` still pass.

## Non-functional requirements

### RNF-01 — Standard library, and GitHub's own Actions only

The Action runs the package's source with the standard library (spec `001`, ADR-1): no `pip`, no
build at run time, no download from PyPI. Besides its own code, it uses only Actions published by
GitHub (`actions/*`), each pinned by a full commit SHA; the template adds the checkout, pinned the
same way.

### RNF-02 — No input reaches a shell as code

No input, file name or text from the run is written into the text of a shell script (the
`${{ … }}` expressions of a `run:` step); every value reaches a script as an environment variable,
so a value with quotes, `$(…)` or a line break is data.

### RNF-03 — Secrets never reach an output

The token, the passwords, the chat and the addresses appear in no log line, summary, step output or
saved state file of the Action, beyond what the command's own redaction already guarantees (spec
`005`, RNF-03); the secrets passed from the secret store stay masked by GitHub in the log.

### RNF-04 — The network reaches only what the command already reaches

The Action adds no connection of its own besides the runner's storage for the state (RF-05): no
download at run time, no telemetry, and no host but those of specs `002`, `003` and `005`.

### RNF-05 — Runners

The Action runs on GitHub-hosted runners with Linux, Windows and macOS, with the Python each one
ships, and uses a shell every one of them has.

### RNF-06 — Least privilege

The template's workflow grants `contents: read` (to check out the configuration) and `issues: write`
(the issue channel), and the Action needs no other permission of the token, for the state included.
A workflow without the `github` channel can drop `issues: write`.

### RNF-07 — Bounded time

A run of the Action takes the time of the command (spec `004`, RNF-04, plus the deliveries of spec
`005`, RNF-05) and a few seconds to restore and save the state; the template's job timeout bounds
the rest.

## Open questions

1. **Does an issue opened through the workflow's token notify the repository's watchers?** GitHub's
   documentation says that creating an issue triggers notifications, but not for an issue authored
   by `github-actions[bot]`. Settled on a real repository before the design is approved; the answer
   decides whether the README has to tell the person to watch the repository.

   **Answered** (2026-11-28): it does, when the owner watches the repository; see the overview of
   the design.

## Resolved decisions

1. **A template, not a fork** (2026-11-28): a fork starts with its scheduled workflows off and
   cannot be private, so it would not run until the person finds the switch; a repository created
   from a template runs on the first push, and may be private.
2. **The template is a separate repository** (2026-11-28): it holds only what the person edits (the
   configuration, the workflow and a README), so that a new repository does not carry the code,
   tests and specs of this one; this repository keeps a tested copy of its files (RF-08).
3. **A warning is not a failed run** (2026-11-28): the issue, or the message, is the warning, and
   a red run every day while a certificate expires would be a second, louder channel that teaches
   the person to ignore both; a run goes red when it is broken or when the warning reached no one,
   which is when GitHub's notice of a failed run is useful.
4. **The state lives in the Actions storage, not in a commit** (2026-11-28): a commit needs
   `contents: write`, which RNF-06 refuses, and adds a commit for every change; a lost state repeats
   each current warning once, which spec `005` accepts.
5. **The Action downloads nothing at run time** (2026-11-28): the commit SHA in `uses:` already
   identifies every file of the Action (spec `001`, ADR-6); an install from an index at each run
   would add code that the pin does not cover.

## Tests

### RF-12 — Action test suite

The suite checks, without the network: that `action.yml` and the template's workflow pin every
`uses:` to a full commit SHA; that no `run:` step holds a `${{ … }}` expression; that every input
and output of `action.yml` has a description and a row in the tables of both READMEs, and the
README's workflow matches the template's; that the template's workflow has the triggers, the
permissions, the concurrency and the timeout of RF-09; that the template's `tlsvigil.toml` is a
valid configuration (spec `004`); and the mapping of RF-04 from exit code to the step's result. A CI
job runs the Action from the checkout (`uses: ./`) on Linux, Windows and macOS against a local TLS
server, with no channel and no real host, and asserts the exit-code output, the summary with
hostile text shown as plain text, a run with no state to restore, and the failure of an invalid
input and of a missing configuration.
