---
feature: The notifications — one GitHub issue, Telegram and e-mail, sent on change and at reminders, never twice for the same thing
status: approved
date: 2026-11-27
related:
  - 005-notifications/requirements.md
  - 004-config-and-output/design.md
  - 003-rdap/design.md
  - 001-foundation/design.md
origin: conception
---

# 005 — Notifications — design

## Overview

Notifications are one step after the run of spec `004`, taken only with `--notify`. `cli.py` loads
the configuration as before; with `--notify` it also reads the channels' settings from the
environment (`environment.py`), which fails the load, like a bad key, when a variable is missing or
malformed. After `run()` returns the `Report`, `notify.py` loads the state file (`state.py`), asks
`alerts.py` what each channel has to hear, hands each channel its `Plan`, writes the new state and
returns one `Delivery` per channel. `output.py` adds the deliveries to the text and the JSON, and
`cli.py` turns a failed delivery into the exit code `4`.

The decision of what to send is pure: `alerts.py` turns the report, the count of `error` runs and
what a channel was last told into a list of events (new, changed, reminder, resolved, ended) and the
channel's view after this run, with no clock and no network, so every rule of RF-04 to RF-06 is a
unit test. The words live in `messages.py`, the only place a notification's text is written, with
one escape for GitHub's Markdown and none needed for the two plain-text channels (ADR-5). The three
channels (`github.py`, `telegram.py`, `mail.py`) only deliver. The GitHub API and the Telegram Bot
API go through one bounded HTTPS function extracted from the RDAP client (ADR-1); e-mail goes
through `smtplib` with the same deadline and a cap on the server's replies (ADR-6). Every text the
command writes passes through one redactor that knows the secret values (ADR-7).

## Module layout

```text
src/tlsvigil/
  https.py                   # new: request() — one bounded HTTPS exchange, no redirect (ADR-1)
  environment.py             # new: the channels' settings from the environment, the Redactor (ADR-7, ADR-10)
  alerts.py                  # new: findings, events and the channel's view; the reminder days (ADR-8)
  state.py                   # new: the versioned state file, read with a cap, written atomically (ADR-2)
  messages.py                # new: the issue, the comment, the Telegram text and the e-mail (ADR-5)
  github.py                  # new: the issue channel (ADR-3, ADR-4)
  telegram.py                # new: the Telegram channel
  mail.py                    # new: the SMTP channel (ADR-6)
  notify.py                  # new: notify() — state in, plans, deliveries, state out
  rdap.py                    # changed: its requests go through https.request(); behaviour unchanged
  config.py                  # changed: the [notifications] table, the secret-like keys (RF-02)
  cli.py                     # changed: --notify, --state, exit 4, redaction, new test seams (ADR-9)
  output.py                  # changed: the delivery lines and the `notifications` JSON key (RF-12)
  i18n.py                    # changed: the sentences of the notifications, both languages
tests/
  http_server.py             # changed: POST and PATCH, request bodies recorded, handler routes
  fake_apis.py               # new: FakeGitHub and FakeTelegram, stateful routes for LocalHTTPServer
  smtp_server.py             # new: LocalSMTPServer — implicit TLS, STARTTLS, misbehaving replies (ADR-11)
  test_https.py              # new: the bounded exchange on its own (RNF-02, RNF-05)
  test_environment.py        # new: every variable, missing, empty, malformed; the Redactor (RF-03, RNF-03)
  test_alerts.py             # new: the rules of RF-04 to RF-06, pure
  test_state.py              # new: missing, corrupt, oversized, other version, atomic write (RF-07)
  test_messages.py           # new: both languages, the escapes, the caps (RF-11, RNF-04)
  test_github.py             # new: the issue life cycle against FakeGitHub (RF-08)
  test_telegram.py           # new: the message and Telegram's refusals (RF-09)
  test_mail.py               # new: TLS, STARTTLS, no credential before TLS, the reply cap (RF-10)
  test_notify.py             # new: main(argv) end to end with --notify, exit 4, secrets (RF-01, RF-12)
  test_config.py             # changed: the [notifications] table and its errors (RF-02)
  test_output.py             # changed: the delivery lines and the JSON key (RF-12)
README.md, README.pt-BR.md   # --notify, --state, the table, the variables, the permission, exit 4 (RF-13)
CLAUDE.md, docs/stack.md     # architecture, commands, "Before you touch", smtplib and email in use
```

## Data model

```python
# config.py
class Channel(StrEnum):
    GITHUB = "github"
    TELEGRAM = "telegram"
    SMTP = "smtp"

class SmtpSecurity(StrEnum):
    STARTTLS = "starttls"          # the default
    TLS = "tls"                    # TLS from the first byte

DEFAULT_SMTP_PORTS: Final = {SmtpSecurity.STARTTLS: 587, SmtpSecurity.TLS: 465}

@dataclass(frozen=True, slots=True)
class SmtpServer:
    host: str                      # validated by certificate.validate_host
    port: int
    security: SmtpSecurity

@dataclass(frozen=True, slots=True)
class Notifications:
    channels: tuple[Channel, ...]  # in the order of the file, no repeats
    smtp: SmtpServer | None        # set when the file has [notifications.smtp]

@dataclass(frozen=True, slots=True)
class Config:                      # spec 004, plus:
    notifications: Notifications | None   # None when the file has no [notifications]

# environment.py
@dataclass(frozen=True, slots=True)
class GitHubSettings:
    token: str
    repository: str                # "owner/name"

@dataclass(frozen=True, slots=True)
class TelegramSettings:
    token: str
    chat_id: str                   # an integer, possibly negative, or "@channelname"

@dataclass(frozen=True, slots=True)
class SmtpSettings:
    server: SmtpServer
    username: str
    password: str
    sender: str
    recipients: tuple[str, ...]    # one or more

@dataclass(frozen=True, slots=True)
class ChannelSettings:
    github: GitHubSettings | None
    telegram: TelegramSettings | None
    smtp: SmtpSettings | None

    def private_values(self) -> tuple[str, ...]: ...   # the tokens, the password, the chat, the addresses

class Redactor:
    def __init__(self, values: Iterable[str]) -> None: ...
    def __call__(self, text: str) -> str: ...  # every form of every value → "***"

# alerts.py
REMINDER_DAYS: Final = (14, 7, 3, 1, 0)
PAST_DUE: Final = -1               # the reminder of a domain past its date (days left < 0)

@dataclass(frozen=True, slots=True)
class Finding:                     # what a channel knows about one entry
    key: str                       # "certificate:<host>:<port>" or "domain:<name>", lowercase
    name: str                      # as shown: "example.com:443", "[2001:db8::1]:8443", "example.com.br"
    status: str                    # "expiring", "invalid" or "error"
    reason: str | None             # the invalid reason or the error kind; None for expiring
    expires_at: datetime | None    # aware, UTC; set for expiring
    reminder: int | None           # the lowest reminder day reached, or None

class EventKind(StrEnum):
    NEW = "new"
    CHANGED = "changed"
    REMINDER = "reminder"
    RESOLVED = "resolved"
    UNCHECKABLE = "uncheckable"    # ended: the entry is now unknown
    REMOVED = "removed"            # ended: the entry left the configuration

@dataclass(frozen=True, slots=True)
class Event:
    kind: EventKind
    finding: Finding               # the current one; the last told one for the three endings
    previous: Finding | None       # the last told one, for CHANGED

@dataclass(frozen=True, slots=True)
class Plan:
    events: tuple[Event, ...]      # in the order of the configuration, removed entries last
    view: tuple[Finding, ...]      # what the channel knows after this run, in the same order

# state.py
STATE_VERSION: Final = 1
DEFAULT_STATE: Final = ".tlsvigil-state.json"
MAX_STATE_BYTES: Final = 1024 * 1024

@dataclass(frozen=True, slots=True)
class ChannelState:
    told: Mapping[str, Finding]    # by key
    repository: str | None = None  # github only: the repository the issue belongs to
    issue: int | None = None       # github only: the number of TLSVigil's open issue
    author: str | None = None      # github only: the login that opened it

@dataclass(frozen=True, slots=True)
class State:
    error_runs: Mapping[str, int]              # by key; entries with 0 are left out
    channels: Mapping[Channel, ChannelState]

# notify.py
class DeliveryOutcome(StrEnum):
    SENT = "sent"
    UNCHANGED = "unchanged"
    FAILED = "failed"

@dataclass(frozen=True, slots=True)
class Delivery:
    channel: Channel
    outcome: DeliveryOutcome
    message: str | None            # why it failed, as plain text; None otherwise
    issue: int | None = None       # github only: the issue written to, when there is one

# https.py
@dataclass(frozen=True, slots=True)
class Response:
    status: int
    headers: Mapping[str, str]     # names in lowercase, the first value of each
    body: bytes

class RequestError(Exception):
    kind: str                      # "dns", "refused", "timeout", "tls", "too_large", "other"
    message: str

# cli.py
EXIT_DELIVERY: Final = 4
```

`Finding.status` and `reason` are strings, as `ATTENTION` is in spec `004`, because the certificate
and the domain enums are different types that share values. The view and the events carry the
`name` as shown, so that an entry removed from the configuration can still be named in the message
that says so.

## Components

### Settings from the environment (RF-03; ADR-7, ADR-10)

`read_settings(notifications, environ)` reads only the variables of the channels listed, in the
order of the table below, and collects every problem as a `Problem` of spec `004` whose place is the
variable's name and whose parameters never hold its value. The `cli` raises them as one
`ConfigError`, written and exited with `2` like the problems of the file. A value is stripped of
surrounding whitespace; an empty value counts as missing.

| Channel | Variable | Form |
|---|---|---|
| `github` | `GITHUB_TOKEN` | no whitespace or control character |
| `github` | `GITHUB_REPOSITORY` | `owner/name`: `[A-Za-z0-9-]+/[A-Za-z0-9._-]+` |
| `telegram` | `TLSVIGIL_TELEGRAM_TOKEN` | `<digits>:<letters, digits, _ and ->` |
| `telegram` | `TLSVIGIL_TELEGRAM_CHAT_ID` | an integer, possibly negative, or `@` and 5 to 32 letters, digits or `_` |
| `smtp` | `TLSVIGIL_SMTP_USERNAME` | no control character |
| `smtp` | `TLSVIGIL_SMTP_PASSWORD` | no control character |
| `smtp` | `TLSVIGIL_SMTP_FROM` | one address (below) |
| `smtp` | `TLSVIGIL_SMTP_TO` | one or more addresses, separated by commas |

An address is `local@domain` with one `@`, no whitespace, no control character and none of
`<>()[],;:"`; the display-name forms are refused, so that no value can carry a second address or a
header. `ChannelSettings.private_values()` gives the two tokens, the password, the chat and the
addresses; the `Redactor` built from them replaces, in any text, each value and its percent-encoded
form (the Telegram token sits in the path of a URL) with `***`, longest first. The chat and the
addresses are not secrets, but RNF-03 keeps them out of every output, and an `smtplib` exception
quotes the recipients it refused.

### The configuration (RF-02)

`config.py` accepts a top-level `notifications` key. Inside it, `channels` is a list of strings,
each one a `Channel`, none twice; `smtp` is a table with `host` (required, validated by
`certificate.validate_host`), `port` (`1` to `65535`, default `DEFAULT_SMTP_PORTS[security]`) and
`security` (`"starttls"` or `"tls"`, default `"starttls"`). Listing `"smtp"` without the table is a
problem at `notifications.smtp`; a table whose channel is not listed is accepted, so that a channel
is turned off by removing it from the list.

A secret-like key is a problem of its own (`config.secret_key`), with the variable to use:

| Place | Keys | Variables named |
|---|---|---|
| `notifications` | `token` | `GITHUB_TOKEN`, `TLSVIGIL_TELEGRAM_TOKEN` |
| `notifications` | `github` (a table) | `GITHUB_TOKEN`, `GITHUB_REPOSITORY` |
| `notifications` | `telegram` (a table) | `TLSVIGIL_TELEGRAM_TOKEN`, `TLSVIGIL_TELEGRAM_CHAT_ID` |
| `notifications` | `chat_id`, `chat` | `TLSVIGIL_TELEGRAM_CHAT_ID` |
| `notifications.smtp` | `username`, `user` | `TLSVIGIL_SMTP_USERNAME` |
| `notifications.smtp` | `password` | `TLSVIGIL_SMTP_PASSWORD` |
| `notifications.smtp` | `from`, `sender` | `TLSVIGIL_SMTP_FROM` |
| `notifications.smtp` | `to`, `recipients` | `TLSVIGIL_SMTP_TO` |

Any other unknown key stays the `config.unknown_key` problem of spec `004`. The value of a
secret-like key is never repeated in the message.

### Findings and events (RF-04 to RF-06; ADR-8)

`findings(report, error_runs)` walks the report in its order and gives, per entry key, one of three
answers, and the new `error_runs`:

| Status of the result | Answer | `error_runs` after |
|---|---|---|
| `expiring` | a `Finding` with `expires_at`, and `reminder` (below) | `0` |
| `invalid` | a `Finding` with the reason | `0` |
| `error`, the run before was not `error` | **hold**: the channel keeps what it was told | `1` |
| `error`, the run before was `error` too | a `Finding` with the error kind | count `+ 1` |
| `ok` | resolved | `0` |
| `unknown` | uncheckable | `0` |

The `reminder` of an `expiring` finding is the lowest day `d` among `REMINDER_DAYS` and `PAST_DUE`
with `d < warn_days` and `days_left <= d`, or `None` when the days left are above every such day.

`plan(told, answers, order)` compares what one channel was told with the answers of this run:

| Told | This run | Event | Told after |
|---|---|---|---|
| nothing | a finding | `NEW` | the finding |
| a finding | a finding with another status, or another reason for `invalid` | `CHANGED` | the new finding |
| an `expiring` finding | `expiring`, with a lower `reminder` | `REMINDER` | the new finding |
| an `expiring` finding | `expiring`, with the same or a higher `reminder` | none | the new finding |
| a finding | the same status (and reason for `invalid`) | none | the new finding |
| anything | hold | none | unchanged |
| a finding | resolved / uncheckable | `RESOLVED` / `UNCHECKABLE` | nothing |
| a finding | the key is not in the report | `REMOVED` | nothing |
| nothing | resolved, uncheckable, or absent | none | nothing |

A `NEW` or `CHANGED` finding already carries the lowest reminder reached, so a warning that starts
at five days left sends no reminder for `7` on the same run. A reminder that went up (a date pushed
later but still inside the threshold) is kept silently, so the next lower day reminds again. A
change of error kind or message is not a change: two failures of the same host differ in wording
from run to run. `plan` reads no clock and sends nothing.

### The state file (RF-07; ADR-2)

```json
{
  "version": 1,
  "error_runs": {"certificate:example.com:443": 1},
  "channels": {
    "github": {
      "repository": "owner/name", "issue": 12, "author": "github-actions[bot]",
      "told": {
        "domain:example.com.br": {
          "name": "example.com.br", "status": "expiring", "reason": null,
          "expires_at": "2026-12-20T00:00:00+00:00", "reminder": 14
        }
      }
    },
    "telegram": {"told": {}}
  }
}
```

`load_state(path)` reads the file as bytes up to `MAX_STATE_BYTES` (the pattern of spec `004`,
ADR-6), decodes it as UTF-8 and validates every key and type; any deviation (a missing file, an
unreadable one, invalid JSON, an unknown version, a wrong type anywhere) returns the empty state and
a catalogue key the `cli` writes on the standard error (`state.missing` is silent, the others are
not). `save_state(path, state)` writes the JSON to a temporary file in the same directory, flushes
it to disk and moves it over the old one with `os.replace`. A channel that is no longer listed loses
its part; a GitHub part whose `repository` differs from `GITHUB_REPOSITORY` is reset, so that an
issue number is never used in another repository. A state that cannot be written leaves a line on
the standard error and the exit code as it was: the next run repeats a message, it never loses one.

### The bounded HTTPS exchange (RNF-02, RNF-05; ADR-1)

`https.request(method, url, *, headers, body, cap, deadline, context)` opens one
`http.client.HTTPSConnection` with the given context, sends one request with `Connection: close`
and `Accept-Encoding: identity`, and reads the answer with the chunked, capped and deadline-bound
loop of spec `003` (ADR-3 and ADR-4 there). It returns any status as a `Response`, a redirect
included: it never follows one, and a `3xx` reaches the channel as a status like any other. Every
failure to connect, to hand shake or to read becomes a `RequestError` with the kinds of spec `003`.
The RDAP client keeps its own policy on top of it: it follows a redirect on the same origin, maps the
statuses and the kinds to its `ErrorKind`, and its tests pass unchanged.

### The GitHub issue (RF-08; ADR-3, ADR-4)

`GitHubChannel.deliver(plan, part, now, lang)` works in three steps, with at most four requests:

1. **Find the issue.** With an `issue` in the part, `GET /repos/{repository}/issues/{issue}`: an
   open issue whose `user.login` is the part's `author` is TLSVigil's; a closed one (a person closed
   it), a `404`, a `410` (deleted) or a `301` (transferred) forgets it. Without one, `GET
   /repos/{repository}/issues?state=open&per_page=100` adopts the oldest open issue, not a pull
   request, whose author is `github-actions[bot]` and whose body starts with the marker
   `<!-- tlsvigil -->`. Any other answer fails the delivery.
2. **Tell.** With events and an issue: `POST .../issues/{issue}/comments` with the events. With
   events, no issue and a view that is not empty: `POST .../issues` with the title and the body,
   which counts as the message, and the new number and author go to the part. With events, no
   issue and an empty view (the warnings ended while the issue was closed by a person): nothing is
   posted.
3. **Bring the issue up to date.** With an issue: `PATCH .../issues/{issue}` with the current
   title and body, and, when the view is empty, `state: closed` and `state_reason: completed`,
   after which the part forgets the number.

The part's `told` advances as soon as step 2 succeeds (or has nothing to post); a failure in step 3
fails the delivery but is repaired by the next run, whose step 3 runs whatever the events. Every
request sends `Accept: application/vnd.github+json`, `X-GitHub-Api-Version: 2026-03-10`,
`Authorization: Bearer <token>`, `User-Agent: tlsvigil/<version>` and, with a body,
`Content-Type: application/json`; the answer cap is 1 MiB. The failures read as:

| Answer | Message |
|---|---|
| `401` | the token was refused |
| `403` whose `message` is `Resource not accessible by integration`, or with `X-Accepted-GitHub-Permissions` | the token cannot write issues: the workflow needs `permissions: issues: write` |
| `403` or `429` with `retry-after` or `x-ratelimit-remaining: 0` | GitHub limits the requests; the next run tries again |
| `404` on the repository | the repository does not exist or the token cannot see it |
| `410` on `POST .../issues` | issues are turned off in the repository |
| anything else | `HTTP <status>` and GitHub's `message`, escaped and cut at 200 characters |

Nothing is retried within a run (RNF-05), as in spec `003`.

### Telegram (RF-09)

`TelegramChannel.deliver` sends `POST https://api.telegram.org/bot<token>/sendMessage` with the JSON
`{"chat_id": ..., "text": ..., "link_preview_options": {"is_disabled": true}}` and no `parse_mode`,
so Telegram reads the text as plain text. The answer cap is 64 KiB. An answer whose `ok` is not
`true` fails the delivery with its `description` (escaped and cut at 200 characters) or, without
one, `HTTP <status>`. The URL is never part of a message, and the Redactor covers the token anyway.

### E-mail (RF-10; ADR-6)

`MailChannel.deliver` builds an `email.message.EmailMessage` with the policy `email.policy.SMTP`:
`From` the sender, `To` the recipients, `Subject` the title of `messages.py` (TLSVigil's own words
and a count, no outside text), `Date` from `email.utils.format_datetime`, a `Message-ID` made with
the sender's domain (never the machine's name), and the text as a `text/plain` UTF-8 body. The policy
refuses a line break in a header, so no value reaches a second header.

The session uses `_BoundedSMTP` (`"starttls"`) or `_BoundedSMTPSSL` (`"tls"`), small subclasses of
`smtplib.SMTP` and `smtplib.SMTP_SSL` with `local_hostname="localhost"` (no lookup of the runner's
name) and a `getreply` that sets the socket timeout to the time left before each line and refuses a
reply longer than 100 lines. With `"starttls"`, a server whose `EHLO` does not offer `STARTTLS` ends
the session before `AUTH`; `starttls(context=...)` verifies the certificate with the context of
`certificate.create_context`. Then `login`, `send_message` and `quit`. Every `smtplib` exception
becomes the delivery's message, with the server's reply code and text escaped; a refused recipient
is counted (`the server refused 1 recipient`), never named.

### Messages (RF-11; ADR-5)

`messages.py` writes, in the language of the run:

- **The title:** `TLSVigil: 2 entries need attention`, or `TLSVigil: nothing needs attention` for
  the e-mail and the Telegram message that close a warning.
- **The issue body:** the marker `<!-- tlsvigil -->`, one sentence on what the issue is, one list
  item per finding of the view (its name, status and detail, the days left counted from the run's
  `started_at`), and a closing line saying that the issue is rewritten on each run and that closing
  it stops the updates until something changes.
- **The events** (the comment, the Telegram message, the top of the e-mail): one line per event:
  `New: example.com:443 expires in 9 days (2026-12-06)`, `Changed: example.com:443 is now invalid
  (expired), was expiring`, `Reminder: example.com.br expires in 3 days (2026-12-03)`,
  `Resolved: example.com:443`, `Ended without being resolved: example.de can no longer be checked
  (no RDAP service)`, `... is no longer in the configuration`.
- **The e-mail body:** the events, a blank line, then the view as in the issue body, in plain text.

The details reuse the sentences of the text output of spec `004` (`reason.*`, `error.*`, the days
and the date), so the two never drift apart. For GitHub, every name and every piece of outside text
is written as a code span (ADR-5); for Telegram and e-mail, as plain text through `printable`. Each
text is cut at a whole line under its cap (the issue body and a comment at 60,000 UTF-16 code
units, under GitHub's 65,536 characters; a Telegram message at 4,000, under its 4,096), and ends
with `... and N more`.

### The orchestration (RF-01, RF-07, RF-12)

`notify(report, config, settings, state_path, *, lang, context, github_api_url, telegram_api_url)`
loads the state, computes the answers once, then, for each channel in the order of `channels`,
builds its `Plan` from its part, delivers, and keeps the part the channel returns (advanced on
success, the old one on failure, RF-07). It saves the state once, at the end, and returns the
deliveries. A channel never stops the next one: a failure is a `Delivery` with `FAILED`, and an
unexpected exception inside a channel is a bug that reaches the `cli`'s exit `3`, after the parts of
the channels already delivered are saved.

### The command line (RF-01, RF-12; ADR-7, ADR-9)

`check` gains `--notify` and `--state PATH` (default `DEFAULT_STATE`, `--state` without `--notify`
is a usage error). `--notify` with `--host` or `--domain` is refused by `check.error`, like
`--config` with them (spec `004`, deviation 5); `--notify` with a file without `[notifications]` or
with an empty `channels` list is the configuration problem `notifications.channels`. With `--notify`
the settings are read after the file validates, and their problems exit `2` before any check runs.

From the moment the settings exist, every write to the standard output and the standard error goes
through the `Redactor`: the report, the problems, the state warnings and the traceback of exit `3`,
which is formatted with `traceback.format_exc()` and redacted before it is written (ADR-7). The exit
code is `4` when any delivery failed, else the code of spec `004`.

### Output (RF-12)

The text output adds, after the summary, one line per delivery: `github: sent (issue #12)`,
`telegram: nothing new`, `smtp: failed: <message>`, with the outcome translated and the channel
name as its identifier. The JSON document adds `notifications`, a list with one object per
channel in the order of `channels`: `{"channel": "github", "outcome": "sent", "message": null,
"issue": 12}`, every key always present (spec `004`, ADR-5), `issue` `null` for the other channels.
Without `--notify` the key is absent (RF-12), so its presence says that notifications were asked
for; the schema version stays `1` (spec `004`, RNF-05).

## Interfaces

**Command line.**

```text
tlsvigil check [--config PATH] [--json] [--lang {en,pt-BR}] [--notify [--state PATH]]
exit 0 nothing needs attention · 1 something does · 2 nothing was checked · 3 internal error
     4 a notification could not be delivered (whatever the checks found)
```

**Configuration.**

```toml
[notifications]
channels = ["github", "telegram", "smtp"]

[notifications.smtp]
host = "smtp.example.com"
port = 587                 # default: 587 for starttls, 465 for tls
security = "starttls"      # or "tls"
```

**Environment.** The eight variables of "Settings from the environment". In GitHub Actions,
`GITHUB_REPOSITORY` is set by the runner and `GITHUB_TOKEN` is passed by the workflow
(`env: GITHUB_TOKEN: ${{ github.token }}`, with `permissions: issues: write`); spec `006` writes
that workflow.

**GitHub REST API** (`https://api.github.com`, version `2026-03-10`, the newest the documentation
lists, with `2022-11-28` supported until 2028-03-10): `GET /repos/{o}/{r}/issues/{n}`,
`GET /repos/{o}/{r}/issues?state=open&per_page=100`, `POST /repos/{o}/{r}/issues` (`title`,
`body`), `POST /repos/{o}/{r}/issues/{n}/comments` (`body`), `PATCH /repos/{o}/{r}/issues/{n}`
(`title`, `body`, `state`, `state_reason`). The documentation says that creating an issue and a
comment trigger notifications; labels are not used, because the API drops in silence the labels of
a token without push access, which is the case of `issues: write`.

**Telegram Bot API** (`https://api.telegram.org/bot<token>/sendMessage`, `POST`, JSON body; the
answer always carries `ok`).

**SMTP** (RFC 5321 with STARTTLS, RFC 3207, or implicit TLS on the port given; `AUTH` through
`smtplib.SMTP.login`, which picks a mechanism the server offers).

## ADRs

### ADR-1 — One bounded HTTPS exchange, extracted from the RDAP client, with no redirect for the APIs

**Decision.** Move the connection, the request and the capped, deadline-bound read of `rdap.py` to
`https.request()`, which returns any status and never follows a redirect; the RDAP client keeps its
same-origin redirect policy and its error mapping on top of it.
**Alternatives.** (a) A second, separate client for the APIs. (b) `urllib.request`. (c) Make the RDAP
client's private `_get` general and call it from the channels.
**Why.** (a) duplicates the part that took the most care in spec `003` (the read loop that must not
call `settimeout` after the body ends). (b) follows redirects and reads proxy variables by default,
which spec `003`, ADR-2, ruled out. (c) ties the channels to RDAP's error kinds and redirect rules.
**Trade-off.** A change to `rdap.py`, a `done` spec's module, with no change in behaviour; its tests
are the guard, and they must pass unchanged.

### ADR-2 — The state is one versioned JSON file, with a part per channel, written atomically

**Decision.** One file holds the count of `error` runs and, per channel, what it was told; it is
replaced whole with `os.replace`, and any file that does not validate is read as empty.
**Alternatives.** (a) The state inside the issue, as hidden text. (b) One file per channel. (c) Keep
reading a partly valid file.
**Why.** (a) cannot serve Telegram and e-mail without GitHub, and the first `error` run exists
before any issue does (requirements, resolved decision 6). (b) splits the `error` count, which is
shared. (c) guesses; an empty state errs toward one repeated message, which the requirements accept.
**Trade-off.** A lost or corrupt file repeats every current warning once; spec `006` has to keep the
file between workflow runs.

### ADR-3 — The issue is the one whose number the state keeps, adopted only by marker and author

**Decision.** The state keeps the number and the author of the open issue; when it has none, the
oldest open issue by `github-actions[bot]` whose body starts with `<!-- tlsvigil -->` is adopted.
**Alternatives.** (a) A label. (b) Search by title. (c) Never adopt: a lost state opens a new issue.
**Why.** (a) is dropped in silence for a token without push access. (b) matches an issue anyone can
open in a public repository. (c) leaves the old issue open and stale beside the new one. Only a
workflow of the repository can open an issue as `github-actions[bot]`, and only TLSVigil writes
the marker, so an adopted issue is TLSVigil's own (RF-08).
**Trade-off.** With a token other than the workflow's, a lost state cannot adopt, and the old issue
stays open until a person closes it.

### ADR-4 — The issue body is rewritten on every run; only a comment notifies

**Decision.** Step 3 of the channel rewrites the title and the body on every run that has an issue;
the events go to a comment, and only when there are events.
**Alternatives.** (a) Rewrite the body only with events. (b) Put the events in the body and post no
comment.
**Why.** GitHub notifies watchers of a new issue and a new comment, not of an edit, so the body can
follow the days left every day without a message; (a) leaves the days left stale between reminders;
(b) notifies no one of a change.
**Trade-off.** One `PATCH` per run while an issue is open, well inside GitHub's limit of 80
content-generating requests a minute and 500 an hour.

### ADR-5 — Outside text reaches GitHub as code spans, and the other channels as plain text

**Decision.** In the issue and its comments, every name and every outside text (an issuer, an RDAP
status, an error message) is a Markdown code span whose backtick fence is longer than any run of
backticks inside it, after `printable`; Telegram gets no `parse_mode`; the e-mail is `text/plain`.
**Alternatives.** (a) Backslash-escape every Markdown character. (b) A fenced code block for the
whole list. (c) HTML entities.
**Why.** Inside a code span GitHub renders no Markdown or HTML, makes no link, no `@` mention and no
`#` reference, which is the whole of RNF-04; (a) is a long list of rules and misses the autolinks of
bare URLs and `www.`; (b) loses the translated sentences around the names; (c) is not escaping for
Markdown.
**Trade-off.** Names show in a monospace font; Telegram clients may still show a text that looks like
a link as one, with its preview off.

### ADR-6 — E-mail through `smtplib`, with one deadline and a cap on the server's replies

**Decision.** Subclass `smtplib.SMTP` and `smtplib.SMTP_SSL` to override `getreply` with one that sets
the socket timeout to the time left before each line and refuses more than 100 lines, and set
`local_hostname`.
**Alternatives.** (a) `smtplib` as it is. (b) An SMTP client written on `socket` and `ssl`.
**Why.** `smtplib`'s timeout is per socket operation and its reply loop has no line count, so a server
that streams continuation lines holds the run without end (RNF-05); its `EHLO` also looks up the
machine's name unless `local_hostname` is given. (b) rewrites STARTTLS and `AUTH`, which `smtplib`
already does right.
**Trade-off.** `getreply` is copied from the standard library and must follow it; the CI runs it on
every supported Python.

### ADR-7 — One Redactor cleans every text the command writes, the traceback included

**Decision.** From the moment the settings are read, the report, the problems, the warnings and the
traceback pass through the `Redactor` before they are written, with the secrets and the personal
values (the chat and the addresses); the traceback is formatted to a string first.
**Alternatives.** (a) Make sure each message is built without a secret. (b) Drop the traceback when
notifications are on.
**Why.** (a) holds for TLSVigil's own messages but not for the text of an exception from
`http.client`, `smtplib` or a bug, which can quote a header or a URL; one last filter covers them
all. (b) leaves nothing for a bug report; spec `004`, ADR-3, said this spec would revisit it.
**Trade-off.** A value that also appears as ordinary text (a sender address that is also a
monitored name) shows as `***` there too.

### ADR-8 — The reminder days are fixed, and a reminder is the lowest day reached

**Decision.** `REMINDER_DAYS = (14, 7, 3, 1, 0)` and `PAST_DUE = -1`, counted only below the entry's
threshold; the state keeps the lowest day told, and a run that reaches a lower one sends one reminder.
**Alternatives.** (a) A `remind_days` key. (b) Remind when the days left equal a day exactly.
**Why.** The requirements keep the days out of the configuration (Non-goals). (b) loses a reminder
whenever the run of that day is late or skipped, which GitHub's schedule allows; comparing with the
lowest day told only delays it.
**Trade-off.** A threshold of 60 days on a domain still reminds first at 14.

### ADR-9 — The new test seams are keyword-only arguments of `main`

**Decision.** `main` gains `environ`, `github_api_url` and `telegram_api_url`, keyword-only and
defaulting to `os.environ` and the two public URLs; the SMTP server is the one of the file.
**Alternatives.** (a) Variables such as `TLSVIGIL_GITHUB_API`. (b) A flag.
**Why.** As in spec `004`, ADR-7: a person running the command cannot point the token at another
host, which RNF-02 forbids; a test reaches the local servers.
**Trade-off.** `main`'s signature grows to six keyword arguments that only the tests pass.

### ADR-10 — The GitHub variables keep GitHub's names; the others start with `TLSVIGIL_`

**Decision.** `GITHUB_TOKEN` and `GITHUB_REPOSITORY`; `TLSVIGIL_TELEGRAM_*` and `TLSVIGIL_SMTP_*`.
**Alternatives.** (a) `TLSVIGIL_GITHUB_TOKEN`. (b) No prefix (`TELEGRAM_TOKEN`, `SMTP_PASSWORD`).
**Why.** The runner sets `GITHUB_REPOSITORY` already, and `GITHUB_TOKEN` is the name the
documentation and other tools use for the workflow's token. (b) clashes with variables another tool
in the same job may read.
**Trade-off.** Two naming styles in one list.

### ADR-11 — The tests' SMTP server is written on `socketserver`, not added as a dependency

**Decision.** `tests/smtp_server.py` serves `EHLO`, `STARTTLS`, `AUTH PLAIN` and `LOGIN`, `MAIL`,
`RCPT`, `DATA` and `QUIT`, with implicit TLS or STARTTLS, records every command, and can leave
`STARTTLS` out, stay silent or stream continuation lines.
**Alternatives.** (a) `aiosmtpd` as a development dependency. (b) Mock `smtplib`.
**Why.** The standard library lost `smtpd` in 3.12; (a) adds a dependency for about 200 lines and
does not misbehave on purpose; (b) tests the mock, not the TLS and the order of the commands,
which is what RF-10 guards.
**Trade-off.** One more test server to keep, like those of specs `002` and `003`.

## Impact on existing code

- `rdap.py`: `_get_once` and `_read_body` move to `https.py`; `RdapClient._get` calls
  `https.request` and keeps its loop of redirects and its mapping. No test of spec `003` changes.
- `config.py`: `notifications` joins the top-level keys; `Config` gains `notifications`;
  `config_from_names` sets it to `None`. The spec `004` test that a `[notifications]` table is an
  error changes into the tests of the table.
- `cli.py`: two flags, three test seams, the settings step, the `Redactor` around every write, the
  exit `4`, and the help text of `check` with the new code.
- `output.py`: `render_text` and `render_json` take the deliveries (an empty tuple without
  `--notify`, and then the output is byte for byte that of spec `004`); `_host_name` becomes the
  public `display_name`, shared with `alerts.py`.
- `i18n.py`: the `notify.*`, `event.*`, `delivery.*`, `env.*`, `state.*` and `config.secret_key`
  sentences; `tests/test_i18n.py` already fails on a missing language or placeholder.
- `tests/http_server.py`: `do_POST` and `do_PATCH`, the request body recorded, and a `handler`
  route kind that computes the answer from the request; the RDAP tests keep their fixed routes.

## Test strategy

- **Unit, no network:** `alerts.py` (every row of both tables, a jump over two reminder days, a
  domain past its date, a reminder that goes up, `error` once and twice, a change of error kind that
  is not a change), `state.py` (round trip, each way a file is refused, the atomic write with
  `os.replace` failing, a repository change, a channel removed), `messages.py` (both languages, the
  caps at a whole line, the code span around backticks), `environment.py` (each variable and form,
  the Redactor with the percent-encoded token).
- **Against local servers:** `https.py` with `LocalHTTPServer` (a `3xx` returned, not followed;
  silent, drip and endless; the cap); `github.py` with `FakeGitHub` (open, comment, rewrite, close,
  closed by a person, `404`, `410`, `301`, adoption, an issue by another author or without the
  marker left alone, each failure of the table, step 3 failing and repaired next run);
  `telegram.py` with `FakeTelegram`; `mail.py` with `LocalSMTPServer` (implicit TLS, STARTTLS,
  STARTTLS not offered, an untrusted certificate, a silent server, an endless reply).
- **End to end:** `main(argv)` with `--notify` and a `tlsvigil.toml` naming `127.0.0.1` for the
  SMTP server, the fakes behind the seams, and a `tmp_path` state file; two and three runs in a row
  to see a warning told once, reminded, and closed.
- **Negative tests and their controls:**
  - *no state without `--notify`*: the same run with `--notify` writes it;
  - *no credential before TLS*: with STARTTLS offered, `AUTH` is in the recorded commands;
  - *no request to a channel not listed*: the listed one got its request;
  - *an issue by someone else is untouched*: TLSVigil's own issue, in the same list, got the
    `PATCH`;
  - *no secret in any output*: each fake echoes the token or the password in its error, and the
    test first asserts that the raw exception text holds it, then that no stream, the JSON or the
    state file does; a crash planted after the settings are read does the same for the traceback.
- **Seen failing:** each guard is broken once on purpose before its test is trusted (the `<` of the
  reminder comparison, the hold of the first `error`, the `os.replace`, the marker check, the
  STARTTLS check, the line cap, the Redactor's percent-encoded form, the code span fence), and the
  sabotage is recorded in the implementation notes and the pull request.
- The network guard of spec `002` stays autouse: `api.github.com` and `api.telegram.org` are never
  resolved in a test.

## Deviations

1. **ADR-1: one test of spec `003` changes its import, not its assertions.**
   `test_a_deadline_that_passes_before_the_body_is_read_is_a_timeout` freezes the clock of the
   module that holds the deadline; the deadline moved to `https.py`, so the test patches
   `https.time` instead of `rdap.time`. The ticks and the assertions are those of spec `003`.
2. **ADR-1: the body of any status is read, within the cap and the deadline.** The channels need
   the body of an error answer, so `request()` reads it for every status before the RDAP client
   maps the status. An RDAP answer outside `2xx` that announces a body over the cap, or never
   sends its body, is now `too_large` or `timeout` instead of `not_found`, `rate_limited` or
   `http`; no test of spec `003` covered that case.

## Implementation notes

- **Stage 0:** `deadline_after(timeout)` gives a `Deadline` (the moment and the timeout it was set
  for, named in the message of a timeout), and one deadline covers the RDAP request and its
  redirects. `RequestError.kind` is a `Literal` whose values are those of the RDAP `ErrorKind`, so
  the client maps it with `ErrorKind(kind)`. The caller's headers go first and
  `Accept-Encoding: identity` and `Connection: close` over them. The test server answers `GET`,
  `POST` and `PATCH` alike and gained `Route.handled(fn)`, which computes the route from the
  recorded request. Seen failing (each change undone): `request()` following a redirect (5 red,
  4 of them RDAP tests), the cap written `>=` in the read loop (1 red) and in the
  `Content-Length` check (1 red), the deadline not reset before a read (3 red), the `POST` body
  not sent (2 red), and the caller's `Connection` winning over the fixed one (2 red); in the
  server, the handler not called (3 red), a handler giving a handler not raising (1 red), and the
  request body not read (3 red).
