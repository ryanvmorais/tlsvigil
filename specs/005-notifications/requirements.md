---
feature: The notifications — one GitHub issue, Telegram and e-mail, sent on change and at reminders, never twice for the same thing
status: approved
date: 2026-11-27
related:
  - 001-foundation/design.md
  - 004-config-and-output/requirements.md
origin: conception
---

# 005 — Notifications

## Context and problem

Spec `004` delivered `tlsvigil check`: it reads the configuration, runs the certificate and domain
checks, prints a text or JSON report and exits `1` when something needs attention. A person who
runs it sees the result; nobody else does. Run on a schedule, an exit code warns only whoever reads
the log of that run, and a daily job that fails every day while a certificate is expiring is the
flood that teaches people to stop looking.

Spec `001` (ADR-3) chose the default channel: one GitHub issue in the user's own repository, opened
when something needs attention, updated on later runs and closed when everything is fine again,
through the workflow's token, with Telegram and SMTP e-mail as optional channels. This spec designs
that behaviour for the command line, so that the Action of spec `006` only has to call it.

Two facts shape it. A scheduled run can be late or skipped, so a warning must not depend on running
on one exact day. And the configuration file usually lives in a public repository, so it cannot hold
a token, a password, a chat or an e-mail address. Spec `004` refused a `[notifications]` table
until this spec defined it (spec `004`, Non-goals); this spec adds it to the keys of spec `004`,
RF-04.

## Goals

- A **`--notify` flag** for `tlsvigil check` that delivers the result to the channels the
  configuration turns on, and leaves a plain `tlsvigil check` without side effects.
- **One GitHub issue** in the repository the run belongs to: opened, kept current, commented on when
  something changes, and closed when nothing needs attention.
- **Telegram** and **SMTP e-mail** as optional channels, with the same rules.
- **No duplicate warnings:** a channel hears about a warning when it starts, when it changes, at a
  few reminders as the date gets closer, and when it ends; never again for the same thing.
- **Secrets and personal data outside the file:** tokens, passwords, the chat and the e-mail
  addresses come from the environment (the CI's secret store).
- A **separate exit code** when a warning could not be delivered.

## Non-goals

- The Action, the template repository, the schedule, keeping the state file between workflow runs
  and stopping two runs of the same workflow at once: spec `006`.
- Other channels (Slack, Discord, generic webhooks, SMS, push services).
- GitHub Enterprise Server, an issue in a repository other than the one of the run, and assigning,
  labelling for triage or mentioning people in the issue.
- A notification for `unknown` (Resolved decision 2).
- Choosing the reminder days, quiet hours, digests on a schedule of their own, and a command that
  sends a test message.
- HTML e-mail, e-mail without TLS, and SMTP sign-in other than a user name and a password (OAuth).
- Notifications from `--host` and `--domain` without a file: the channels live in the file.

## Personas

- As a **site owner** who runs TLSVigil in GitHub Actions, I want one issue in my repository that
  tells me what is about to expire, so that GitHub's own e-mail and app reach me without a secret to
  set up.
- As a **site owner**, I want to hear about a certificate again as its date gets closer, but not
  every day, so that I still read the warning when it comes.
- As an **operator** who runs the command from `cron` on a server, I want Telegram or e-mail without
  GitHub, so that the tool works outside Actions too.
- As a **person with a public repository**, I want my chat and my e-mail address kept out of the
  file, so that the repository does not publish them.
- As a **Brazilian user**, I want the issue and the messages in Portuguese, so that I can forward
  them to a client.

## Functional requirements

### RF-01 — `--notify` delivers the result

- **Given** `tlsvigil check` without `--notify`
  **When** it runs
  **Then** it behaves exactly as in spec `004`: no state is read or written, and nothing but the
  checks reaches the network, even if the file has a `[notifications]` table.
- **Given** `tlsvigil check --notify` and a valid configuration with at least one channel
  **When** the checks end
  **Then** the result is delivered to every channel turned on, following RF-04 to RF-11, and the
  report of spec `004` is printed as before, with the deliveries added (RF-12).
- **Given** `--notify` with `--host` or `--domain`, or with a file that turns no channel on
  **When** the command runs
  **Then** it is a usage or configuration error: no check runs and the exit code is `2`.

### RF-02 — The `[notifications]` table

- **Given** a configuration file
  **When** it is read
  **Then** it may hold a `[notifications]` table with `channels`, a list of distinct values among
  `"github"`, `"telegram"` and `"smtp"`, and, when `"smtp"` is listed, a `[notifications.smtp]`
  table with the server's `host`, its `port` and its `security` (`"starttls"` or `"tls"`), with
  defaults set in the design.
- **Given** an unknown key, a wrong type, an unknown channel, a channel listed twice or a port out
  of range in that table
  **When** the file is validated
  **Then** it is a configuration error with its place, reported with every other problem of the
  file before any check runs (spec `004`, RF-05).
- **Given** a key that looks like a secret or a personal address (a token, a password, a chat, a
  sender or a recipient) anywhere in the table
  **When** the file is validated
  **Then** it is a configuration error whose message says that the value belongs in an environment
  variable, and names that variable.

### RF-03 — Secrets and addresses come from the environment

- **Given** `--notify` and a channel turned on
  **When** the configuration is loaded
  **Then** what the channel needs that is secret or personal is read from environment variables:
  the GitHub token and repository; the Telegram bot token and chat; the SMTP user name, password,
  sender and one or more recipients. The design names each variable.
- **Given** a channel turned on whose variable is missing or empty, or holds a value of the wrong
  form (a repository that is not `owner/name`, an e-mail address without `@`)
  **When** the configuration is loaded
  **Then** it is a configuration error that names the variable, never its value, and no check runs
  (exit code `2`).
- **Given** a channel not turned on
  **When** the configuration is loaded
  **Then** its variables are not read, even if they are set.

### RF-04 — What makes a warning

- **Given** a result `expiring` or `invalid`
  **When** the run is compared with the state of the earlier runs
  **Then** it is a warning from this run on.
- **Given** a result `error`
  **When** the run is compared with the state
  **Then** it becomes a warning only when the same entry was also `error` in the run before; until
  then the entry keeps what the channels were last told about it.
- **Given** a result `ok` or `unknown`
  **When** the run is compared with the state
  **Then** it is never a warning.

### RF-05 — A channel hears about a change

- **Given** a warning no channel was told about (a new warning)
  **When** the run delivers
  **Then** every channel is told about it.
- **Given** a warning whose status, or whose reason for `invalid`, differs from the one the channel
  was last told (an expiring certificate that has now expired, a host that went from `error` to
  `invalid`)
  **When** the run delivers
  **Then** the channel is told about the change.
- **Given** an entry the channel was warned about that is now `ok`
  **When** the run delivers
  **Then** the channel is told it was resolved.
- **Given** an entry the channel was warned about that is now `unknown`, or that is no longer in the
  configuration
  **When** the run delivers
  **Then** the channel is told that the warning ended without being resolved, with the reason (no
  longer checkable, no longer in the configuration), never that it was resolved.
- **Given** a run in which, for a channel, nothing changed and no reminder is due (RF-06)
  **When** the run delivers
  **Then** that channel receives nothing new.

### RF-06 — Reminders as the date gets closer

- **Given** a warning with a number of days left (an `expiring` result) whose days left reach a
  reminder day lower than its threshold, among 14, 7, 3, 1 and 0 days, or go below 0 (a domain
  past its expiration date)
  **When** the run delivers
  **Then** the channel receives a reminder with the days left, once per reminder day.
- **Given** runs that were late or skipped, so that the days left jumped over more than one reminder
  day
  **When** the next run delivers
  **Then** the channel receives one reminder, for the lowest day reached, and none for the days
  jumped over.
- **Given** a warning without days left (`invalid`, `error`)
  **When** later runs find it unchanged
  **Then** no reminder is sent; the next message is for its change or its end.

### RF-07 — The state between runs

- **Given** `--notify`
  **When** the run ends
  **Then** a state file records, per entry and per channel, what that channel was last told, and
  how many runs in a row the entry was `error`; `--state PATH` chooses the file, and the design sets
  the default.
- **Given** no state file, or one that cannot be read, is not valid, is larger than its cap or has a
  version this release does not know
  **When** the run starts
  **Then** it runs as the first run (every warning is new), and a line on the standard error says
  why; a lost state can send a warning again, never hide one.
- **Given** a delivery that failed on one channel
  **When** the state is written
  **Then** that channel's part is left as it was, so the next run tries again what failed, and the
  channels that succeeded are not told twice.
- **Given** a run interrupted while writing the state
  **When** the next run reads it
  **Then** it finds either the old state or the new one, never a partial file.

### RF-08 — The GitHub issue

- **Given** a new warning and no open issue of TLSVigil's own
  **When** the run delivers
  **Then** it opens one issue in the repository of the run, with a title that starts with
  `TLSVigil` and says how many entries need attention, and a body that lists every current warning.
- **Given** the open issue
  **When** a later run has warnings
  **Then** its title and body are rewritten to the current warnings and days left on every run, and
  a comment is added only when RF-05 or RF-06 has something to tell.
- **Given** the open issue
  **When** a run has no warning left
  **Then** a comment says how each warning ended, and the issue is closed.
- **Given** an issue that TLSVigil did not open, even with the same title
  **When** any run delivers
  **Then** it is never edited, commented on or closed.
- **Given** TLSVigil's issue closed by a person while warnings remain
  **When** a later run has nothing to tell
  **Then** the issue is left closed and untouched; when a change or a reminder comes, a new issue is
  opened.
- **Given** a token without permission to write issues
  **When** the run delivers
  **Then** the delivery fails with a message that names the permission the workflow needs.

### RF-09 — Telegram

- **Given** the `telegram` channel and something to tell (RF-05, RF-06)
  **When** the run delivers
  **Then** one message goes to the chat through the Telegram Bot API, as plain text, with link
  previews off.
- **Given** an answer from Telegram that refuses the message (a wrong token, a chat the bot cannot
  write to)
  **When** the run delivers
  **Then** the delivery fails with Telegram's own description of the problem, without the token.

### RF-10 — E-mail over SMTP

- **Given** the `smtp` channel and something to tell
  **When** the run delivers
  **Then** one plain-text e-mail in UTF-8 goes from the sender to every recipient, through the server
  of RF-02, over TLS (`"tls"` from the first byte, or `"starttls"`), signed in with the user name and
  the password.
- **Given** a server that does not offer STARTTLS when `"starttls"` was chosen, or whose certificate
  fails verification
  **When** the run delivers
  **Then** the delivery fails before the user name or the password is sent.

### RF-11 — What a message says

- **Given** a channel with something to tell
  **When** the run delivers
  **Then** it receives at most one message per run (one issue or comment, one Telegram message, one
  e-mail), saying what changed in this run (new, changed, reminded, resolved, ended) and how many
  entries need attention.
- **Given** an entry in a message
  **When** it is written
  **Then** it carries the name, the status and, as in the text output of spec `004`, the days left
  and the date, or the reason, or the kind of error.
- **Given** `lang` in the file (or `--lang`)
  **When** the issue and the messages are written
  **Then** they are in that language, like the text output.
- **Given** more warnings than fit in a channel's limit (the size of an issue, of a comment, of a
  Telegram message)
  **When** the message is written
  **Then** it is cut at a whole entry and ends saying how many entries were left out; a delivery
  never fails because of its length.

### RF-12 — The run reports its deliveries

- **Given** `--notify`
  **When** the text output ends
  **Then** one line per channel says whether it was told something, had nothing new, or failed, and
  why it failed.
- **Given** `--notify` and `--json`
  **When** the document is written
  **Then** it carries, per channel, the same outcome as English identifiers, as a key added to the
  schema version `1` of spec `004` (spec `004`, RNF-05); without `--notify` the key is absent.
- **Given** a run in which at least one delivery failed
  **When** the command ends
  **Then** the exit code is `4`, whether the checks found something or not, so that a script tells
  "the warning did not reach anyone" apart from `0`, `1`, `2` and `3` (spec `004`, RF-09).

### RF-13 — Both READMEs show the notifications

- **Given** `README.md` and `README.pt-BR.md`
  **When** this spec is done
  **Then** both show `--notify` and `--state`, the `[notifications]` table, each environment
  variable, the permission the issue channel needs, when a channel hears about a warning (RF-04 to
  RF-06) and the exit code `4`, and the parity and configuration tests of specs `001` and `004`
  still pass.

## Non-functional requirements

### RNF-01 — Standard library only

The channels use the bounded HTTPS client of spec `003` for the GitHub API and the Telegram Bot
API, and `smtplib` and `email` for the e-mail (spec `001`, ADR-1).

### RNF-02 — The network reaches only the channels turned on

With `--notify`, besides the checks of specs `002` and `003`, the command connects only to the
GitHub REST API at `api.github.com` for the repository of the run, to the Telegram Bot API at
`api.telegram.org`, and to the SMTP server of the file, and only for the channels turned on. Every
connection verifies the certificate, sets TLS 1.2 or later explicitly, has the timeout of the
configuration and a cap on the size of the answer, and follows no redirect.

### RNF-03 — Secrets never reach an output

The GitHub token, the Telegram token and the SMTP password appear in no text output, JSON document,
line on the standard error, issue, message, state file or traceback, including the traceback of an
unexpected failure (spec `004`, ADR-3, revisited here): any text the command writes is cleaned of
the secret values it holds before it is written. The chat and the e-mail addresses appear only in
the requests that need them.

### RNF-04 — External text is data in every channel

Text that comes from a certificate, an RDAP answer, a network error or the configuration reaches the
issue as plain text, never as Markdown, HTML, a link, a mention or a reference to another issue;
reaches Telegram with no markup parsing; and reaches the e-mail only in a plain-text body. No such
text can add a header to the e-mail or break the subject line.

### RNF-05 — The deliveries are bounded

A run makes a bounded number of requests per channel (set in the design, independent of the number
of entries), each with the timeout of the configuration, so `--notify` adds at most a few timeouts
to the run of spec `004`, RNF-04.

### RNF-06 — Least privilege

The issue channel needs the permission to write issues in the repository of the run and nothing
else: no access to the code, to other repositories or to other issues than its own.

## Open questions

None.

## Resolved decisions

1. **A channel hears about a change and at reminders** (2026-11-27): a warning is told when it
   starts, when it changes and when it ends, and again only at fixed reminder days, so that a
   certificate expiring in 14 days sends a few messages before its date instead of 14; a late or
   skipped run delays a reminder, never loses one.
2. **`unknown` never notifies** (2026-11-27): a domain under a top-level domain without RDAP is
   `unknown` on every run, and a warning that never ends teaches the user to ignore the channel; the
   output of spec `004` still says that the date is unknown.
3. **`error` notifies from its second run in a row** (2026-11-27): a timeout or a failed lookup on a
   single run would open and close an issue the next day; a host that stays unreachable is warned a
   run later.
4. **Secrets and personal data come from the environment** (2026-11-27): the file turns the
   channels on and holds what is neither secret nor personal (the SMTP server); tokens, passwords,
   the chat and the e-mail addresses go to the CI's secret store, so that a public repository does
   not publish them.
5. **Notifications are asked for on each run** (2026-11-27): `--notify` makes the side effects
   explicit, so that a check on a laptop never opens an issue or sends a message.
6. **One state file for every channel** (2026-11-27): the issue cannot hold the state, because
   Telegram and e-mail work without GitHub and the count of `error` runs exists before any issue
   does; a lost file sends a warning again, never hides one.
7. **A new issue for each episode, and a close by a person is respected** (2026-11-27): each issue is
   the history of one episode, and closing it is how a person says "seen"; the next change or
   reminder opens a new one instead of reopening it.
8. **A failed delivery has its own exit code** (2026-11-27): `4` tells a script that the warning
   reached no one, which `1` cannot say, and the failed channel is tried again on the next run.

## Tests

### RF-14 — Notification test suite

The suite drives the command through `main(argv)` against local servers only: an HTTPS server that
plays the GitHub REST API and the Telegram Bot API on `127.0.0.1`, and an SMTP server with TLS and
STARTTLS on `127.0.0.1`, with no real API, chat or mailbox (spec `004`, RF-11, and the network guard
of spec `002`). It covers: a check without `--notify` reading and writing no state; the
`[notifications]` table and each of its errors; every variable missing, empty or malformed, and a
secret-like key in the file; the warning rules of RF-04 (`error` once and twice, `unknown`, `ok`);
each change of RF-05; the reminders of RF-06, a run that jumps over reminder days and a domain past
its date; the state missing, corrupt, oversized, of another version, and kept for a channel whose
delivery failed; the issue opened, rewritten, commented on, closed, an issue of someone else left
alone, an issue closed by a person, and a token without permission; a Telegram refusal; an SMTP
server without STARTTLS or with an untrusted certificate, with no credential sent; a message cut at
its limit; both languages; the JSON key and the exit code `4`; a secret planted in every error path
and in a crash, and found in no output; and external text with Markdown, a mention, a link, control
characters and a line break, reaching every channel as plain text.
