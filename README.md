# <img align="bottom" alt="TLSVigil logo: a padlock with an open eye" src="https://raw.githubusercontent.com/ryanvmorais/tlsvigil/main/docs/images/tlsvigil-tls-certificate-expiry-monitor-logo.svg" width="28"> TLSVigil

**English** · [Português (Brasil)](README.pt-BR.md)

<!-- The logo is an HTML <img> with an absolute URL: the PyPI rewrite in pyproject.toml covers only Markdown images. -->

![Python](https://img.shields.io/badge/Python-3.12+-3776AB?logo=python&logoColor=white)
![runtime deps](https://img.shields.io/badge/runtime%20deps-0-brightgreen)
[![CI](https://img.shields.io/github/actions/workflow/status/ryanvmorais/tlsvigil/ci.yml?label=CI&logo=githubactions&logoColor=white)](https://github.com/ryanvmorais/tlsvigil/actions/workflows/ci.yml)
![license](https://img.shields.io/badge/license-Apache%202.0-blue?logo=apache&logoColor=white)

**TLSVigil is a zero-dependency TLS certificate and domain expiry monitor: a command-line tool and
a GitHub Action that warn you, before it happens, that the certificate of your site or the
registration of your domain is about to expire.**

An expired certificate takes a site down with a browser warning that sends visitors away, and an
expired domain takes the site and its mail down together. Let's Encrypt stopped sending expiry
reminder e-mails in June 2025, and certificates are getting shorter lives every year, so the
renewal that fails silently is now the common case. TLSVigil checks each host and each domain once
a day and tells you while there is still time to act.

It is built for small businesses, site owners and freelancers who look after a few sites, and it
needs no server: it runs on a daily schedule in a GitHub repository of yours, on the free GitHub
Actions minutes, and opens an issue there when something needs attention.

> **Status:** in development, not released yet; the first release will be `1.0.0`. This README
> describes the tool as it is specified in [specs/](specs/). Today `tlsvigil check` runs from a
> clone, prints the result and, with `--notify`, delivers it to the GitHub issue, Telegram or
> e-mail; the Action runs it in a workflow, and the template repository is not published yet
> (its files are in [template/](template/)).

## What it checks

TLSVigil answers one question for every name you give it: **how many days are left, and is that
enough?** A host or a domain is reported when it has fewer days left than your warning threshold,
and a certificate that fails verification is reported at once, whatever its date.

| Check | What it tells you | Reads | Network |
|---|---|---|---|
| Certificate | Days until the certificate of `host:port` expires, and whether it fails verification (expired, wrong name, untrusted chain) | The certificate the server presents in the TLS handshake | A TLS connection to each host you list |
| Domain | Days until the registration of the domain expires | The `expiration` event of the registry's RDAP record | HTTPS to the RDAP server the IANA bootstrap names for the top-level domain |

The result goes to one GitHub issue in your repository, through the `GITHUB_TOKEN` the workflow
already has: the issue is opened when something needs attention, updated on the next runs and
closed when everything is fine again, so a problem that lasts a week is one issue, not seven.
Telegram and e-mail (SMTP) are optional extra channels. From the command line, the same result is
printed as text or as JSON (`--json`), in English or Brazilian Portuguese (`--lang pt-BR`), with
an exit code a script can test.

### What you can rely on

- **Passive.** It opens a TLS connection the way a browser does and asks a public registration
  service. It sends no attack payload and logs in nowhere.
- **Zero runtime dependencies.** The package uses the Python standard library only, so what runs
  in your CI is the interpreter and this code.
- **Verified TLS only.** The certificate date is read only from a chain that passed verification;
  a chain that fails is the warning itself, never a silent pass.
- **Bounded requests.** Each RDAP request is HTTPS, to a server the IANA bootstrap names, with a
  limit on its time and on the size of the answer.
- **Your secrets stay secrets.** Tokens and passwords come from the CI's secret store and never
  reach an output, a log line or an issue.

## Quick start

Not published yet, so for now run it from a clone. You need [uv](https://docs.astral.sh/uv/).

```bash
# Clone the repository and install the environment
git clone https://github.com/ryanvmorais/tlsvigil.git
cd tlsvigil
uv sync

# Check one host and one domain, without a configuration file
uv run tlsvigil check --host example.com --domain example.com
```

Each name gets one line, and a last line sums up the run:

```text
OK        example.com:443  certificate expires in 86 days (2027-02-22)
OK        example.com      domain expires in 258 days (2027-08-13)

2 checked: 0 need attention.
```

To watch your own list, write it in a `tlsvigil.toml` file (next section) and run
`uv run tlsvigil check` in the same directory.

## Configuration

`tlsvigil check` reads `tlsvigil.toml` in the current directory, or the file named by
`--config PATH`. A commented example:

```toml
# Optional: "en" (default) or "pt-BR".
lang = "en"
# Optional: seconds for each TLS connection and each RDAP request (default 10).
timeout = 10

[certificates]
# Warn when fewer days than this are left (default 14).
warn_days = 14
hosts = [
  "example.com",                                  # port 443
  "mail.example.com:465",
  "[2001:db8::1]:8443",
  { host = "legacy.example.com", warn_days = 30 },
]

[domains]
# Warn when fewer days than this are left (default 30).
warn_days = 30
names = ["example.com", "example.com.br"]

[notifications]
# The channels --notify delivers to, in this order: "github", "telegram", "smtp".
channels = ["github"]

# Read only when "smtp" is in channels; the user name, the password and the
# addresses come from environment variables, never from this file.
[notifications.smtp]
host = "smtp.example.com"
port = 587                                        # default: 587 for "starttls", 465 for "tls"
security = "starttls"
```

| Key | Default | What it holds |
|---|---|---|
| `lang` | `"en"` | The language of the text output, of the error messages and of the notifications: `"en"` or `"pt-BR"` |
| `timeout` | `10` | The seconds each TLS connection and each RDAP request may take, greater than `0` |
| `certificates.warn_days` | `14` | Warn when a certificate has fewer days left than this (`0` or more) |
| `certificates.hosts` | none | The hosts to check: `"host"` (port 443), `"host:port"`, `"[IPv6]:port"`, or a table `{ host, port, warn_days }` |
| `domains.warn_days` | `30` | Warn when a domain registration has fewer days left than this (`0` or more) |
| `domains.names` | none | The domains to check, or a table `{ name, warn_days }`. Write each domain as registered (`example.com`, not `www.example.com`), and an internationalized name in its `xn--` form |
| `notifications.channels` | none | The channels `--notify` delivers to, each once: `"github"`, `"telegram"`, `"smtp"` |
| `notifications.smtp.host` | none | The SMTP server, required when `"smtp"` is listed |
| `notifications.smtp.port` | `587` or `465` | The server's port, from `1` to `65535`; the default follows `security` |
| `notifications.smtp.security` | `"starttls"` | `"starttls"` (a plain connection upgraded to TLS before signing in) or `"tls"` (TLS from the first byte); there is no plain-text mode |

An entry's own `warn_days` wins over its table's. The whole file is checked before any connection:
an unknown key (a typo such as `warn_day`), a value of the wrong type, a host or a domain listed
twice, or a file with nothing to check stops the run, and each problem is printed with its place
(such as `certificates.hosts[2]`). A token, a password, a chat or an address written in the file
(`password`, `to`, `chat_id`...) is refused too, with the name of the environment variable it
belongs in.

## Command line

```text
tlsvigil check [--config PATH | --host HOST[:PORT]... --domain NAME...] [--json] [--lang en|pt-BR]
               [--notify [--state PATH]]
```

| Option | What it does |
|---|---|
| `--config PATH` | Reads this file instead of `tlsvigil.toml` |
| `--host HOST[:PORT]` | Checks the certificate of this host instead of reading a file; repeat it for more hosts |
| `--domain NAME` | Checks the registration of this domain instead of reading a file; repeat it for more domains |
| `--json` | Writes one JSON document (schema version `1`) with every result and a count per status, with the same English keys and values in any language; with `--notify`, a `notifications` list with the outcome of each channel |
| `--lang en\|pt-BR` | The language of the text output and of the notifications; it wins over the `lang` of the file |
| `--notify` | Delivers the result to the channels of `[notifications]` (next section); only with a file, not with `--host` or `--domain` |
| `--state PATH` | The file where `--notify` keeps what each channel was told (default `.tlsvigil-state.json` in the current directory); only with `--notify` |

The exit code tells a script what happened:

| Code | Meaning |
|---|---|
| `0` | Every result is `ok`, or `unknown` (a domain with no RDAP service or no date) |
| `1` | At least one result needs attention: `expiring`, `invalid` (a certificate that fails verification) or `error` (the check could not finish) |
| `2` | Nothing was checked: a usage error, a configuration error, or nothing to check |
| `3` | An internal error (a bug); the traceback is on the standard error |
| `4` | With `--notify`, a notification could not be delivered, whatever the checks found; the delivery line says why |

## Notifications

With `--notify`, `tlsvigil check` runs the checks as usual and then delivers the result to each
channel of `[notifications]`, in the order listed:

- **`github`** keeps one issue in the repository of the run: it opens the issue when something
  needs attention, rewrites its title and body on every run to the current warnings, adds a
  comment when there is news, and closes it when no warning is left. It touches only the issue it
  opened, and an issue that you close stays closed until there is news, which opens a new one.
- **`telegram`** sends one plain-text message to a chat, through a bot of yours.
- **`smtp`** sends one plain-text e-mail to one or more recipients, over TLS, signed in to the
  server of `[notifications.smtp]`; a server that does not offer STARTTLS, or whose certificate
  fails verification, is never sent the user name or the password.

The report is printed as before, with one line per channel right after the summary:

```text
6 checked: 1 needs attention.
github: sent (issue #12)
telegram: nothing new
smtp: failed: the SMTP server refused 1 recipient
```

### Secrets and addresses

Tokens, the password, the chat and the e-mail addresses come from environment variables, never
from the file, and only the variables of the channels listed are read. A missing or malformed
variable stops the run before any check (exit code `2`), with the variable's name and never its
value, and none of these values reaches the output, the JSON, the state file or a message.

| Variable | Channel | What it holds |
|---|---|---|
| `GITHUB_TOKEN` | `github` | A token that can write issues; in GitHub Actions, `${{ github.token }}` |
| `GITHUB_REPOSITORY` | `github` | The repository of the issue, as `owner/name`; GitHub Actions sets it |
| `TLSVIGIL_TELEGRAM_TOKEN` | `telegram` | The token of your bot, as BotFather gives it (`123456:ABC-...`) |
| `TLSVIGIL_TELEGRAM_CHAT_ID` | `telegram` | The chat: its number (negative for a group) or `@` and the name of a public channel |
| `TLSVIGIL_SMTP_USERNAME` | `smtp` | The user name that signs in to the server |
| `TLSVIGIL_SMTP_PASSWORD` | `smtp` | Its password |
| `TLSVIGIL_SMTP_FROM` | `smtp` | The sender, a bare address (`alerts@example.com`, no display name) |
| `TLSVIGIL_SMTP_TO` | `smtp` | One or more recipients, separated by commas |

In a workflow, the issue channel needs the permission to write issues and the token in the
environment of the step; the other values come from the repository's secrets:

```yaml
permissions:
  contents: read
  issues: write

# In the step that runs tlsvigil check --notify:
env:
  GITHUB_TOKEN: ${{ github.token }}
  TLSVIGIL_TELEGRAM_TOKEN: ${{ secrets.TLSVIGIL_TELEGRAM_TOKEN }}
  TLSVIGIL_TELEGRAM_CHAT_ID: ${{ secrets.TLSVIGIL_TELEGRAM_CHAT_ID }}
```

### When a channel hears about a warning

A result is a warning when it is `expiring` or `invalid`, or when it is `error` on two runs in a
row, since one failed connection is often a passing blip; `ok` and `unknown` never are. Each
channel gets at most one message per run, in the language of the run, and only when it has news:

- **A new warning**, or **a change** of status or of reason (an expiring certificate that has now
  expired, a host that went from `error` to `invalid`).
- **A reminder** when the days left reach 14, 7, 3, 1 or 0, counting only the days below the
  entry's `warn_days`, and once more when a domain is past its date. After late or skipped runs,
  one reminder for the lowest day reached, none for the days jumped over.
- **The end of a warning:** resolved when the entry is `ok` again, or ended without being
  resolved when it can no longer be checked (`unknown`) or is no longer in the configuration.

What each channel was told is kept in the state file (`--state`). Keep that file between runs:
without it, every run is a first run and each warning is told again, never hidden. A channel whose
delivery fails keeps what it was told before, so the next run tries again, and the channels that
succeeded are not told twice.

## GitHub Actions

The Action runs `tlsvigil check --notify` on a GitHub-hosted runner, with the runner's own Python
and nothing to install, and keeps the state file between runs for you. With the `github` channel
alone it needs no secret: the issue is written with the workflow's own token.

### Start from the template

The template repository holds a `tlsvigil.toml`, the daily workflow and a Dependabot file that
proposes each new release of TLSVigil. It is not published yet; its files are in
[template/](template/).

1. **Create your repository** from the template (**Use this template** → **Create a new
   repository**), public or private. Not as a fork: a fork starts with its schedule off.
2. **Name what to check:** edit `tlsvigil.toml` and commit to `main`. That commit starts a check;
   the **Actions** tab shows it, with the result in the run's summary.
3. **Watch the repository** with **Watch** → **All Activity**. The issue is opened by
   `github-actions[bot]`, and GitHub notifies only those who watch the repository; in a busy
   repository, **Custom** → **Issues** is quieter.
4. **Optional:** add `telegram` or `smtp` to `channels`, the channel's secrets to the repository,
   and uncomment its lines in the workflow.

### Add it to a repository of yours

Put `tlsvigil.toml` at the root of the repository and this workflow in
`.github/workflows/tlsvigil.yml`; it is the template's, word for word:

```yaml
# TLSVigil: checks the certificates and domains of tlsvigil.toml every day and keeps one issue of
# this repository up to date with what needs attention. Run by GitHub Actions on the schedule, by
# hand (Actions → TLSVigil → Run workflow) and on a push to main that changes tlsvigil.toml.
# The run is red when nothing could be checked or a notification was not delivered; a warning,
# a host that could not be reached among them, is an issue, not a red run.
name: TLSVigil

on:
  schedule:
    - cron: "17 7 * * *" # every day at 07:17 UTC; GitHub may start it late
  workflow_dispatch:
  push:
    branches: [main]
    paths: [tlsvigil.toml]

# contents: read to check out tlsvigil.toml; issues: write for the "github" channel only.
permissions:
  contents: read
  issues: write

# One check at a time: a second run waits for the first, so the saved state is never raced.
concurrency:
  group: tlsvigil
  cancel-in-progress: false

jobs:
  check:
    name: Check
    runs-on: ubuntu-26.04
    timeout-minutes: 10
    steps:
      - name: Check out the configuration
        uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
        with:
          persist-credentials: false
      - name: Run TLSVigil
        uses: ryanvmorais/tlsvigil@3a6171c98cf91597b3746f105730d23f5abea5b0 # main
        # Telegram or e-mail: add the secrets (Settings → Secrets and variables → Actions), add the
        # channel to tlsvigil.toml and uncomment its lines.
        # with:
        #   telegram-token: ${{ secrets.TLSVIGIL_TELEGRAM_TOKEN }}
        #   telegram-chat-id: ${{ secrets.TLSVIGIL_TELEGRAM_CHAT_ID }}
        #   smtp-username: ${{ secrets.TLSVIGIL_SMTP_USERNAME }}
        #   smtp-password: ${{ secrets.TLSVIGIL_SMTP_PASSWORD }}
        #   smtp-from: ${{ secrets.TLSVIGIL_SMTP_FROM }}
        #   smtp-to: ${{ secrets.TLSVIGIL_SMTP_TO }}
```

Pin the Action to a full commit SHA, as above, never to a branch or a tag that can move. The two
permissions are all it needs: `contents: read` for the checkout and `issues: write` for the issue;
the cache that keeps the state needs none. Keep the concurrency group in a workflow of your own,
so that a run started by hand waits for the scheduled one instead of racing its state.

### Inputs and output

| Input | Default | What it does |
|---|---|---|
| `config` | `tlsvigil.toml` | The configuration file, relative to the workspace |
| `lang` | empty | The language of the report, `en` or `pt-BR`; empty keeps the file's `lang` |
| `notify` | `true` | `true` delivers to the channels of `[notifications]` and keeps the state; `false` only checks |
| `fail-on-attention` | `false` | `true` fails the step when something needs attention, even after it was delivered |
| `github-token` | `${{ github.token }}` | The token of the `github` channel (`GITHUB_TOKEN`) |
| `telegram-token` | empty | The bot token of the `telegram` channel (`TLSVIGIL_TELEGRAM_TOKEN`) |
| `telegram-chat-id` | empty | The chat of the `telegram` channel (`TLSVIGIL_TELEGRAM_CHAT_ID`) |
| `smtp-username` | empty | The user name of the `smtp` channel (`TLSVIGIL_SMTP_USERNAME`) |
| `smtp-password` | empty | Its password (`TLSVIGIL_SMTP_PASSWORD`) |
| `smtp-from` | empty | The sender, one bare address (`TLSVIGIL_SMTP_FROM`) |
| `smtp-to` | empty | The recipients, separated by commas (`TLSVIGIL_SMTP_TO`) |

| Output | What it holds |
|---|---|
| `exit-code` | The exit code of the check, `0` to `4`; `2` also for an invalid input or a runner without Python 3.12 or later |

Each channel input becomes the variable in parentheses, the one of
[Secrets and addresses](#secrets-and-addresses), so its value should come from the repository's
secrets (`${{ secrets.TLSVIGIL_TELEGRAM_TOKEN }}`), never be written in the workflow. An input left
empty sets nothing. The Action hides the tokens and the password in the log, and the report it
writes to the log and the summary is fenced, so text from a certificate or an RDAP answer is never
read as a workflow command or as Markdown.

### State and the colour of a run

The state file lives in the Actions cache: each run restores the newest entry and saves its own.
Without it, the next run is a first run and tells each current warning once more; nothing is
hidden. GitHub removes a cache entry not used for 7 days, so a schedule paused for longer starts
again that way.

The step follows the [exit code](#command-line) of the check. `0` and `1` are green: a warning,
a host that could not be reached among them, is delivered, not a failure, and
`fail-on-attention: true` turns `1` red. `2` (nothing was checked: the configuration or an input
is wrong), `3` (a bug) and `4` (a notification was not delivered) are red, and the run's summary
says which.

### Limits of the platform

- **A scheduled run can be late,** and under heavy load GitHub may drop it; the next day's run
  catches up.
- **A public repository with no activity for 60 days** has its schedule turned off; turn it back on
  in the **Actions** tab.
- **A fork starts with its scheduled workflows off,** and the secrets of the original do not reach
  it.
- **In a private repository,** each run spends minutes of the plan's Actions quota; a check takes
  about a minute.
- **A self-hosted runner** needs Python 3.12 or later on its path, or an `actions/setup-python` step
  before this one.

## TLSVigil vs. other tools

| | TLSVigil | [Uptime Kuma](https://github.com/louislam/uptime-kuma) | [Red Sift Certificates Lite](https://redsift.com/) | [action-check-domain](https://github.com/codex-team/action-check-domain) |
|---|---|---|---|---|
| Shape | A CLI and a GitHub Action, standard library only | A self-hosted monitoring service | A hosted service | A GitHub Action |
| Where it runs | Your GitHub repository, or any machine with Python | A server you run | The vendor's cloud | Your GitHub repository |
| Certificate expiry | Yes | Yes | Yes, up to 250 certificates on the free plan | Yes |
| Domain expiry | Yes, over RDAP | Yes | No | Yes |
| How it warns | One issue that updates and closes itself; Telegram or e-mail if you want | Over 90 notification services | E-mail, 7 days before | Outputs for the next step; the example opens an issue with another Action |

_Comparison checked against the official pages in November 2026._

If you already run Uptime Kuma, keep it: it watches uptime too. TLSVigil is for when the expiry
dates are all you need and you would rather not keep a server running for them.

## Scope and limitations

TLSVigil is deliberately small. For each limit below, what to reach for instead.

- **Some top-level domains have no RDAP service.** The IANA bootstrap lists none for `.de`, `.io`,
  `.co`, `.eu`, `.cn`, `.ru` and `.jp`, among others, so the expiry of those domains is not
  checked; their certificates still are. *Instead:* turn on the renewal reminders and the
  automatic renewal of your registrar.
- **Some registries leave the date out.** RDAP does not require the `expiration` event, and a
  record without it gets no domain warning; the result says the date is unknown. *Instead:* the
  same as above.
- **A revoked certificate is not detected.** The check verifies the chain and the name and reads
  the expiry date, but does not ask the certificate authority (OCSP or CRL) whether the
  certificate was revoked before that date. *Instead:* a monitor that checks revocation, or the
  account page of your certificate authority.
- **A port that starts in plain text is not checked.** The check speaks TLS from the first byte,
  as on ports 443, 465 or 993; a server that starts in plain text and upgrades with STARTTLS (SMTP
  on 25 or 587, IMAP on 143, XMPP) ends as a TLS error. *Instead:* point it at the implicit-TLS
  port of the same server when it has one (465 for SMTP, 993 for IMAP).
- **It sees what the internet sees.** The check runs from a GitHub-hosted runner, so a host that
  is reachable only inside your network is not covered. *Instead:* run the command line on a
  machine inside that network, from `cron` or a scheduled task.
- **The schedule is best effort.** GitHub may start a scheduled workflow late, or skip a run when
  its runners are under heavy load. *Instead:* keep the warning threshold at several days, so one
  missed run never costs you the warning.
- **It is not an uptime monitor.** It does not tell you that a site is down, only that a
  certificate or a domain is about to expire. *Instead:*
  [Uptime Kuma](https://github.com/louislam/uptime-kuma) on a server of yours.

## Development

Python 3.12 is the floor (CI tests 3.12 and the newest stable release). The quality gate runs
before every commit through a versioned hook:

```bash
# Activate the pre-commit hook once per clone
git config core.hooksPath .githooks

# Lint (the hook and the CI run the same four steps, in this order)
uv run ruff check .

# Check the formatting
uv run black --check .

# Check the types (strict)
uv run mypy

# Run the tests with coverage
uv run pytest --cov
```

Each technology of the stack, and why it was chosen, is explained in [docs/stack.md](docs/stack.md).
Features are specified under [specs/](specs/) before they are built.

Contributions are welcome: see [CONTRIBUTING.md](CONTRIBUTING.md). To report a vulnerability in
TLSVigil itself, use the private channel described in [SECURITY.md](SECURITY.md), never a public
issue.

## License

[Apache License 2.0](LICENSE).
