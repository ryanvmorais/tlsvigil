---
feature: The notifications — one GitHub issue, Telegram and e-mail, sent on change and at reminders, never twice for the same thing
status: approved
date: 2026-11-27
related:
  - 005-notifications/requirements.md
  - 005-notifications/design.md
origin: conception
---

# 005 — Notifications — Tasks

Stage 0 extracts the bounded HTTPS exchange the channels share with the RDAP client and teaches the
test HTTPS server to answer `POST` and `PATCH`. Stage 1 reads the `[notifications]` table and the
environment, and builds the Redactor. Stage 2 is the pure core: the findings, the events and the
state file. Stage 3 writes the messages and delivers the GitHub issue; Stage 4 delivers Telegram and
e-mail. Stage 5 wires everything into `tlsvigil check --notify`, and Stage 6 documents it and closes
the spec. Each stage adds the sentences it needs to the catalogue, so `tests/test_i18n.py` keeps both
languages complete throughout, and nothing is reachable from the command line before Stage 5.

The quality gate of every stage is, in order: `uv run ruff check .`, `uv run black --check .`,
`uv run mypy`, `uv run pytest --cov`.

## Stage 0 — The shared HTTPS exchange

- [x] `src/tlsvigil/https.py`: `Response`, `RequestError` (the kinds `dns`, `refused`, `timeout`,
  `tls`, `too_large`, `other`), `deadline_after(timeout)` and `request(method, url, *, headers,
  body, cap, deadline, context)`: one `HTTPSConnection` closed on every path, `Connection: close`
  and `Accept-Encoding: identity` always sent, the capped read loop and the deadline moved from
  `rdap.py`, any status returned and no redirect followed. — RNF-01, RNF-02, RNF-05, ADR-1
- [x] `src/tlsvigil/rdap.py`: `_get` calls `https.request()`, keeps its same-origin redirect loop,
  maps `RequestError` kinds and the statuses to `ErrorKind` as before; `_get_once` and
  `_read_body` removed. — ADR-1
- [x] `tests/http_server.py`: `do_POST` and `do_PATCH`; `RecordedRequest.body`; a `Route.handler`
  kind that computes the status, headers and body from the recorded request; the existing routes
  and their tests unchanged. — RF-14
- [x] `tests/test_https.py`: a `2xx` with its headers in lowercase; a `3xx` returned with its
  `Location`, and no second request recorded; a `POST` and a `PATCH` with their bodies and
  headers as sent; silent, drip and endless answers bounded by the deadline and the cap; a
  `Content-Length` over the cap refused before the body; an untrusted certificate as `tls`; a
  refused port as `refused`. — RNF-02, RNF-05, RF-14
- [x] See the new tests fail once: `request()` following a redirect, the cap compared with `>=`
  instead of `>`, the deadline not reset before a read, the `POST` body not sent; each goes red,
  then the change is undone. The RDAP tests of spec `003` pass with no edit. — ADR-1
- [x] Quality gate. —

## Stage 1 — The configuration and the environment

- [ ] `src/tlsvigil/config.py`: `Channel`, `SmtpSecurity`, `DEFAULT_SMTP_PORTS`, `SmtpServer`,
  `Notifications`, `Config.notifications`; `notifications` among the top-level keys; `channels`
  (a list of known, distinct values), the `smtp` table (`host` through `validate_host`, `port`
  `1` to `65535` with its default by `security`, `security` with its default), `"smtp"` listed
  without the table as a problem; `config_from_names` with `notifications=None`. — RF-02
- [ ] `src/tlsvigil/config.py`: the secret-like keys of the design's table as the problem
  `config.secret_key` with the variables to use, never the value. — RF-02, RF-03
- [ ] `src/tlsvigil/environment.py`: `GitHubSettings`, `TelegramSettings`, `SmtpSettings`,
  `ChannelSettings` with `private_values()`, and `read_settings(notifications, environ)` reading
  only the variables of the channels listed, stripping whitespace, collecting every missing,
  empty or malformed variable as a `Problem` placed at its name with no value in it. — RF-03,
  ADR-10
- [ ] `src/tlsvigil/environment.py`: `Redactor(values)`: each value and its percent-encoded form
  replaced with `***`, longest first. — RNF-03, ADR-7
- [ ] `src/tlsvigil/i18n.py`: the sentences of the new configuration problems (`config.secret_key`,
  the `notifications.*` problems) and of `env.missing` and `env.invalid`, in both languages. —
  RF-02, RF-03
- [ ] `tests/test_config.py`: the spec `004` test of a `[notifications]` table as an error becomes
  the tests of the table: each key with its default and its bounds, an unknown channel, a channel
  twice, `"smtp"` without its table, a table without its channel accepted, each secret-like key
  with its variable named and its value absent from the message, every problem reported together
  with those of the hosts. — RF-02, RF-14
- [ ] `tests/test_environment.py`: each variable of each channel missing, empty, blank, and
  malformed (a repository without `/`, a Telegram token without `:`, a chat with letters, an
  address with two `@`, with a display name, with a comma inside one address); the variables of a
  channel not listed never read, with the listed one read as a control; every problem at once; the
  value of a bad variable in no message; the Redactor on a URL that holds the token percent-encoded
  and on two values where one contains the other. — RF-03, RNF-03, RF-14
- [ ] See the new tests fail once: one secret-like key left as `config.unknown_key`, the address
  check accepting a comma, `read_settings` reading a channel not listed, the Redactor without the
  percent-encoded form, the values sorted shortest first; each goes red, then the change is undone.
  — RF-02, RF-03, RNF-03
- [ ] Quality gate. —

## Stage 2 — Findings, events and the state

- [ ] `src/tlsvigil/output.py`: `_host_name` becomes the public `display_name`, its callers
  updated; the output is unchanged. — RF-11
- [ ] `src/tlsvigil/alerts.py`: `REMINDER_DAYS`, `PAST_DUE`, `Finding`, `EventKind`, `Event`,
  `Plan`; `findings(report, error_runs)` with the answers of the design's first table and the
  lowest reminder reached; `plan(told, answers, order)` with the rows of the second table, events
  in the order of the configuration and removed entries last. — RF-04, RF-05, RF-06, ADR-8
- [ ] `src/tlsvigil/state.py`: `STATE_VERSION`, `DEFAULT_STATE`, `MAX_STATE_BYTES`,
  `ChannelState`, `State`; `load_state(path)` reading bytes up to the cap, decoding UTF-8 and
  validating every key and type, giving the empty state and a catalogue key on any deviation;
  `save_state(path, state)` through a temporary file in the same directory, flushed and moved with
  `os.replace`; the reset of a channel no longer listed and of a GitHub part from another
  repository. — RF-07, ADR-2
- [ ] `src/tlsvigil/i18n.py`: the `state.*` warnings in both languages. — RF-07
- [ ] `tests/test_alerts.py`: every row of both tables; `error` once (held, with a told finding
  kept and with nothing told) and twice; a change of error kind that is not a change; `invalid`
  with another reason; a warning that starts at five days with no reminder for `7`; a jump from
  eight to two days that sends one reminder for `3`; a domain past its date at `PAST_DUE`; a
  reminder that goes up and then down again; a threshold of `0`; an entry removed from the
  configuration named in its event. — RF-04, RF-05, RF-06, RF-14
- [ ] `tests/test_state.py`: the round trip; a missing file silent; a directory, a file without
  permission, invalid UTF-8, invalid JSON, an unknown version, a wrong type at each level and a
  file over the cap, each read as empty with its warning; `os.replace` made to fail leaving the old
  file whole; a channel no longer listed dropped; a GitHub part of another repository reset; no
  token, password, chat or address in a saved file. — RF-07, RNF-03, RF-14
- [ ] See the new tests fail once: the reminder compared with `<` instead of `<=`, the hold of the
  first `error` removed, `REMOVED` events placed in configuration order, the cap compared with
  `>=`, the write done in place instead of through `os.replace`; each goes red, then the change is
  undone. — RF-04, RF-06, RF-07
- [ ] Quality gate. —

## Stage 3 — Messages and the GitHub issue

- [ ] `src/tlsvigil/messages.py`: the title, the issue body with the marker, the events, the
  Telegram text, and the e-mail subject and body, in both languages, the details from the
  sentences of spec `004`; the code span with a fence longer than any run of backticks inside,
  for GitHub only; the caps at a whole line measured in UTF-16 code units, with `... and N more`.
  — RF-08, RF-11, RNF-04, ADR-5
- [ ] `src/tlsvigil/i18n.py`: the `notify.*` and `event.*` sentences in both languages, with no
  Markdown character of their own. — RF-11
- [ ] `src/tlsvigil/github.py`: `GitHubChannel.deliver(plan, part, now, lang)` in the three steps
  of the design (find, tell, bring up to date), at most four requests, the headers, the 1 MiB cap,
  `told` advanced once step 2 succeeds, the number forgotten on a close, and the failure messages
  of the design's table with GitHub's `message` escaped and cut. — RF-08, RNF-02, RNF-05, RNF-06,
  ADR-3, ADR-4
- [ ] `tests/fake_apis.py`: `FakeGitHub`, a set of `Route.handler` routes that keep issues and
  comments in memory, check the headers and the token, and can be told to answer `401`, the `403`
  of a missing permission, a rate limit, `404`, `410` or `301`, or to fail one step. — RF-14
- [ ] `tests/test_messages.py`: each event in both languages; the body with the view and the days
  left from `started_at`; outside text with Markdown, HTML, `@user`, `#1`, `https://` and
  `www.` URLs, backticks, control characters and a line break, inside a code span for GitHub and
  as plain text for the others; every catalogue sentence of the notifications free of Markdown
  characters; the caps with one entry more than fits; the subject with no outside text. — RF-11,
  RNF-04, RF-14
- [ ] `tests/test_github.py`: a new warning opening one issue; a later change commenting and
  rewriting; a run with no event rewriting with no comment; the last warning resolved commenting
  and closing; an issue closed by a person left closed and a new one opened at the next change;
  a deleted, a transferred and a missing issue forgotten; adoption of TLSVigil's issue, with an
  issue by another author and one without the marker left untouched beside it; each failure of the
  table with its message; step 3 failing, `told` advanced, and the next run closing the issue; at
  most four requests on every path. — RF-08, RF-14
- [ ] See the new tests fail once: the fence as long as the longest run of backticks, the marker
  check removed from the adoption, the author check removed, `told` advanced before step 2, the
  close sent without the empty view; each goes red, then the change is undone. — RF-08, RNF-04,
  ADR-3, ADR-5
- [ ] Quality gate. —

## Stage 4 — Telegram and e-mail

- [ ] `src/tlsvigil/telegram.py`: `TelegramChannel.deliver`: one `POST` to `sendMessage` with
  `chat_id`, `text` and `link_preview_options`, no `parse_mode`, the 64 KiB cap, a refusal read
  from `ok` and `description`, the URL in no message. — RF-09, RNF-02, RNF-04
- [ ] `src/tlsvigil/mail.py`: `_BoundedSMTP` and `_BoundedSMTPSSL` with `local_hostname` and the
  bounded `getreply` (the time left before each line, 100 lines at most); `MailChannel.deliver`
  with the `EmailMessage` of the design, STARTTLS required before `login`, the context of
  `certificate.create_context`, every `smtplib` exception as the message, refused recipients
  counted and never named. — RF-10, RNF-01, RNF-02, RNF-04, RNF-05, ADR-6
- [ ] `tests/fake_apis.py`: `FakeTelegram`, recording each message and answering a refusal on
  demand (a wrong token, a chat the bot cannot write to, a `429`). — RF-14
- [ ] `tests/smtp_server.py`: `LocalSMTPServer` on `127.0.0.1` with implicit TLS or STARTTLS,
  `AUTH PLAIN` and `LOGIN`, the commands and the messages recorded, and the modes without
  `STARTTLS`, silent, endless continuation lines and recipient refused. — RF-14, ADR-11
- [ ] `tests/test_telegram.py`: the request body and the path; text with Markdown and HTML sent as
  is; each refusal with Telegram's description and no token in the message. — RF-09, RF-14
- [ ] `tests/test_mail.py`: a message over implicit TLS and over STARTTLS, read back with
  `email.message_from_bytes` (plain text, UTF-8, the headers); a server without STARTTLS and one
  with an untrusted certificate failing with no `AUTH` recorded, and with STARTTLS offered `AUTH`
  recorded as the control; a silent server bounded by the deadline; an endless reply refused at
  the line cap; a refused recipient counted and not named; a header value with a line break
  refused; no lookup of the machine's name (the network guard). — RF-10, RNF-04, RNF-05, RF-14
- [ ] See the new tests fail once: `parse_mode` set to `MarkdownV2`, the STARTTLS check removed,
  the line cap removed, the deadline not reset before a line, the refused recipient named; each
  goes red, then the change is undone. — RF-09, RF-10, ADR-6
- [ ] Quality gate. —

## Stage 5 — `tlsvigil check --notify`

- [ ] `src/tlsvigil/notify.py`: `DeliveryOutcome`, `Delivery`, and `notify(report, config,
  settings, state_path, *, lang, context, github_api_url, telegram_api_url)`: the state loaded,
  the answers computed once, each channel in the order of `channels` with its plan, the part
  advanced only on success, the state saved once at the end, and a write failure left on the
  standard error with the exit code unchanged. — RF-01, RF-07, ADR-2
- [ ] `src/tlsvigil/cli.py`: `--notify` and `--state`, `--state` without `--notify` and `--notify`
  with `--host` or `--domain` refused, a file with no channel as the problem
  `notifications.channels`, the settings read after the file validates, `EXIT_DELIVERY`, the help
  text with the code `4`, and the seams `environ`, `github_api_url` and `telegram_api_url`. —
  RF-01, RF-03, RF-12, ADR-9
- [ ] `src/tlsvigil/cli.py`: every write to the standard output and the standard error through the
  Redactor once the settings exist, the traceback of exit `3` formatted with
  `traceback.format_exc()` and redacted. — RNF-03, ADR-7
- [ ] `src/tlsvigil/output.py` and `src/tlsvigil/i18n.py`: one delivery line per channel after the
  summary, and the `notifications` list in the JSON with its four keys, absent without
  `--notify`; the `delivery.*` sentences in both languages. — RF-12
- [ ] `tests/test_output.py`: the delivery lines in both languages; the JSON key with every outcome
  and its absence without `--notify`; the output without deliveries byte for byte that of spec
  `004`. — RF-12, RF-14
- [ ] `tests/test_notify.py`: through `main(argv)` with the three fakes and a `tmp_path` state:
  a check without `--notify` reading and writing no state and sending nothing, with the same run
  under `--notify` as the control; the usage and configuration errors of `--notify` exiting `2`
  with no check run; three runs in a row (a warning told once, reminded, then resolved and
  closed); `error` on one run and then two; a failure on one channel giving exit `4`, the others
  told once, and the failed one told on the next run; the exit code `4` with nothing to warn
  about; both languages; a secret and an address echoed by each fake and by a crash planted after
  the settings are read, first asserted in the raw exception text, then found in no stream, no
  JSON and no state file. — RF-01, RF-07, RF-12, RNF-03, RF-14
- [ ] See the new tests fail once: the state written without `--notify`, the part advanced on a
  failed delivery, the exit code `4` given only when the checks found something, the Redactor left
  off the traceback; each goes red, then the change is undone. — RF-01, RF-07, RF-12, RNF-03
- [ ] Quality gate. —

## Stage 6 — Documentation and the close of the spec

- [ ] `README.md` and `README.pt-BR.md`: `--notify` and `--state`, the `[notifications]` table in
  the example file and in the keys, the eight variables, `permissions: issues: write`, when a
  channel hears about a warning (the changes, the reminder days, `error` from its second run,
  `unknown` never), and the exit code `4`; `tests/test_readme_pt_br.py`,
  `tests/test_readme_config.py` and `tests/test_readme_links.py` pass. — RF-13
- [ ] `CLAUDE.md`: the commands, the environment variables, the architecture with the new modules
  and test servers, the "Before you touch" rows for the channels, the state and `https.py`. — RF-13
- [ ] `docs/stack.md`: `smtplib`, `email`, `os.replace` and the shared HTTPS exchange in use; the
  diagram updated. — RF-13
- [ ] `specs/005-notifications/`: `status: done` in the three files, the deviations and the
  implementation notes recorded; `specs/README.md`: the roadmap row with the delivery `v0.5` and
  the status `done`; the traceability check (every RF, RNF and ADR cited exists, every RF has a
  task). — RF-13
- [ ] Quality gate. —
