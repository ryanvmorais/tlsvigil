# The stack, and why

Every load-bearing technology in TLSVigil, what it does, why it was chosen over the alternative,
and the handful of concepts worth learning first if it is new to you.

This is not exhaustive: the full version graph is pinned in `uv.lock`, and per-feature decisions
live in the `### ADR-N` blocks of `specs/*/design.md`. This page is the map: the *shape* of the
project and the reasoning behind each piece. The project is not released yet (`1.0.0.dev0`): today
the package is the skeleton, the quality gate, the CI, the certificate check, the domain check,
the `tlsvigil check` command (the configuration, the text and JSON output, the two languages and the
exit codes) and its `--notify` (the GitHub issue, Telegram and e-mail, with a state file between
runs). The pieces marked **planned** below (the PyPI release and the composite Action) are not
built yet.

The project is one small program with no runtime dependency:

```text
GitHub Actions schedule (cron)  ──►  tlsvigil check  (CLI, argparse)      ◄── built
                                        │
        ┌───────────────────────────────┼─────────────────────────────┐
        ▼                               ▼                             ▼
 configuration (TOML)           certificate check              domain check
 tomllib, validated   built     socket + ssl, verified         IANA bootstrap ──► RDAP server
                                TLS handshake        built     HTTPS, bounded       built
        │                               │                             │
        └───────────────────────────────┴──────────────┬──────────────┘
                                                       ▼
                       days left per host and per domain ──► text or JSON, --lang en|pt-BR
                                                       │      + exit code            built
                                                       ▼  with --notify
   state file (JSON, os.replace)  ◄──►  what each channel hears (new, changed,
                                        reminder, resolved, ended)              built
                                                       │
                                                       ▼
            one GitHub issue (REST API, GITHUB_TOKEN)  ·  Telegram  ·  SMTP e-mail   built
            https.request(): bounded, no redirect               smtplib + email, TLS
```

---

## Language and packaging

### Python 3.12+

The whole tool. 3.12 is the floor because it is the system Python of Ubuntu 24.04 LTS, where
`pipx install` runs; it is tested in CI on 3.12 to 3.15 (the newest stable release today), on Linux
and on Windows, and reaches end of life on 2028-10-31, when the floor gets reviewed.

**Why this:** a compiled language would add a build and a binary per platform for a tool that opens
a few TLS connections a day. The standard library already has what the job needs (sockets, TLS,
HTTPS, TOML, argument parsing), which is what makes "zero dependencies" possible.

**Learn:** type hints (`X | None`, `list[str]`), `pathlib.Path`, frozen dataclasses with
`slots=True`, `enum.StrEnum`, and `from __future__ import annotations`.

**Docs:** [docs.python.org/3.12](https://docs.python.org/3.12/)

### uv

The Python package manager and virtual-environment tool (from Astral, the Ruff people). It replaces
`pip`, `pip-tools`, `virtualenv` and `pipx`. `uv sync` installs the locked graph, `uv run` executes
inside the environment, and `uv lock` resolves. `exclude-newer = "7 days"` in `[tool.uv]` keeps a
release younger than a week out of the lock, and the CI installs with `uv sync --locked`, which
fails on a lock that no longer matches `pyproject.toml`.

**Why this:** against Poetry, PDM and plain pip, uv is much faster, uses the standard
`pyproject.toml` (PEP 621) with a real lockfile (`uv.lock`), and needs no plugins. It also gives end
users `uv tool install tlsvigil` for free.

**Learn:** `uv sync` (and `--locked`), `uv run <cmd>`, `uv add --dev <pkg>`, `uv lock`, and
`exclude-newer` with a relative span; the split between `[project.dependencies]` (empty here, on
purpose) and `[dependency-groups] dev`.

**Docs:** [docs.astral.sh/uv](https://docs.astral.sh/uv/)

### Hatchling and hatch-fancy-pypi-readme

Hatchling is the build backend: the program that turns the source tree into the sdist and the wheel
when you run `uv build`, configured in `[build-system]` and `[tool.hatch.*]` of `pyproject.toml`.
`hatch-fancy-pypi-readme` is its plugin that assembles the PyPI description from `README.md` at
build time and rewrites every relative image and link to an absolute GitHub URL, because PyPI
cannot resolve them.

**Why this:** against setuptools and Flit, Hatchling needs almost no configuration for a `src/`
layout and has no legacy `setup.py` baggage. The plugin is the standard answer to "the README works
on GitHub and breaks on PyPI"; `tests/test_readme_links.py` checks that the links survive the
rewrite.

**Learn:** the `src/` layout and `[tool.hatch.build.targets.wheel] packages`; `dynamic = ["readme"]`;
how a `substitutions` pattern (a regular expression) rewrites links; what the sdist `include` list
leaves out on purpose.

**Docs:** [hatch.pypa.io](https://hatch.pypa.io/latest/) ·
[hatch-fancy-pypi-readme](https://github.com/hynek/hatch-fancy-pypi-readme)

### PyPI distribution

**Planned.** The release is a pure-Python wheel and an sdist, installed with `pipx install tlsvigil`
or `uv tool install tlsvigil`; the CI `package` job already builds them and runs `twine check
--strict` so the README renders on PyPI.

**Why this:** the audience installs a command-line tool, and PyPI is where `pipx` looks. A container
image would add a release surface for no gain.

**Learn:** what a wheel and an sdist contain, `pipx` against `uv tool`, and PyPI trusted publishing
(OIDC), which publishes from a workflow with no stored token.

**Docs:** [packaging.python.org](https://packaging.python.org/)

---

## The standard library, doing the work

### `socket` and `ssl`

The certificate check (`certificate.py`) opens one TCP connection to `host:port`, upgrades it to
TLS with a context built by `create_context()` (certificate and host name verified, against the
system trust store) and reads the `notAfter` date of the certificate the server presents. A
handshake that fails verification (expired, wrong name, untrusted chain) is itself the finding,
with its reason taken from the OpenSSL verification code. The context starts from the defaults and
pins three settings (TLS 1.2 or newer, no `VERIFY_X509_STRICT` or `VERIFY_X509_PARTIAL_CHAIN`, the
host matched against the subject alternative names only), because Python 3.13 changed the
defaults and the verdict must be the same on every supported version.

**Why this:** against `pyOpenSSL`, `cryptography` or shelling out to `openssl s_client`, the `ssl`
module needs no dependency, is already linked to the platform's OpenSSL, and verifies by default.
The date is read only from a verified chain: the tool never retries with `CERT_NONE` to read a date
from a certificate it just rejected.

**Learn:** `ssl.create_default_context()`, `SSLContext.wrap_socket(server_hostname=...)`,
`SSLCertVerificationError` and its `verify_code`, `getpeercert()` and the `notAfter` string (parsed
with `ssl.cert_time_to_seconds`), why SNI (`server_hostname`) is required, and the `verify_flags`
that Python 3.13 added to the defaults.

**Docs:** [`ssl`](https://docs.python.org/3.12/library/ssl.html) ·
[`socket`](https://docs.python.org/3.12/library/socket.html)

### `http.client` and RDAP

The domain check (`rdap.py`) asks the registry's RDAP server (JSON over HTTPS) for the
`expiration` event of the domain. The server is found through the IANA bootstrap file, which maps
each top-level domain to its RDAP base URL; one `RdapClient` fetches it on the first domain of a
run and reuses it for the others. Each request is one `http.client.HTTPSConnection`, HTTPS only,
verified with the same context as the certificate check, under one deadline for the whole
request, with a cap on the size of the answer (2 MiB for the bootstrap, 512 KiB for a domain) and
no compression. A redirect is followed only on the same scheme, host and port, three times at
most. The exchange itself lives in `https.py` (spec `005`, ADR-1): one `request()` that sends one
request, reads the body of any status within the cap and the deadline, and follows no redirect;
the RDAP client keeps its same-origin redirect loop on top of it, and the GitHub and Telegram
channels use it as it is. Every way a check can end (no RDAP service, no date in the record, a refused connection, a
body too large) comes back as a value, never as an exception.

**Why this:** against `requests` or `httpx`, the standard library is a dependency less, and the
traffic is a few simple requests: two `GET`s per domain, and a handful of `POST`s and `PATCH`es to
the notification APIs. RDAP is the standardised, JSON successor of WHOIS; against parsing
WHOIS text, it needs no per-registry format. Against `urllib.request`, `http.client` has nothing
to switch off: it reads no proxy variable from the environment, has no `file:` or `ftp:` handler,
returns a redirect as a status the code looks at, and every error status as a value instead of an
`HTTPError`. The price is about thirty lines (the redirect loop, the `Location` join) and no
connection reuse, which a daily run does not need. The bound on the answer and the refusal to
follow a redirect elsewhere are what keep an external server from steering the tool.

**Learn:** RDAP (RFC 9083, the JSON response; RFC 9224, the bootstrap), the `events` array and the
`expiration` event action, `HTTPSConnection(host, port, context=..., timeout=...)`,
`getresponse()` and `read1()` for reading a body in bounded chunks, and `urljoin` for a relative
`Location`. Some top-level domains publish no RDAP service and some registries leave the date out;
both end as "not checked", never as a guess.

**Docs:** [`http.client`](https://docs.python.org/3.12/library/http.client.html) ·
[RFC 9083](https://datatracker.ietf.org/doc/html/rfc9083) ·
[RFC 9224](https://datatracker.ietf.org/doc/html/rfc9224)

### `argparse`

The command line: `tlsvigil --version` and the `check` sub-command with `--config`, `--host`,
`--domain`, `--json` and `--lang`. `main(argv)` takes the argument list and returns the exit code
(`0` to `3`) instead of calling `sys.exit`, so the tests drive it in-process.

**Why this:** against click, typer and rich, each of which is a dependency, `argparse` is enough for
a handful of flags.

**Learn:** `ArgumentParser`, `add_argument(action="version")`, `add_subparsers`, a `type=`
function that raises `ArgumentTypeError`, `parser.error()` (usage and exit `2`), and the pattern of
a `main(argv) -> int` that tests call directly.

**Docs:** [`argparse`](https://docs.python.org/3.12/library/argparse.html)

### `tomllib`

Reads the configuration file, `tlsvigil.toml`: the language, the timeout, the hosts and ports, the
domains and the warning thresholds. The whole file is validated by hand before any connection, and
every problem is reported with its place. Secrets (a Telegram bot token, an SMTP password) are not in the file: they come
from the CI's secret store.

**Why this:** TOML is what the project's own `pyproject.toml` uses, `tomllib` is in the standard
library since 3.11 (so under the 3.12 floor), and it only reads, which is all that is needed. YAML
would need a dependency and has a history of unsafe loaders.

**Learn:** `tomllib.loads` on text decoded with `utf-8-sig` (the file is read as bytes up to a size
cap first), `TOMLDecodeError` and its line and column, and validating the loaded `dict` by hand with
a clear message for an unknown key.

**Docs:** [`tomllib`](https://docs.python.org/3.12/library/tomllib.html)

### `json` and `datetime`

`json` turns the report into one document for `--json` (schema version `1`), which a script can
read;
`datetime` does the arithmetic of "how many days are left" in UTC, so the count does not depend on
the machine's time zone.

**Why this:** both are in the standard library; a date library such as `arrow` or `pendulum` would
add a dependency for one subtraction.

**Learn:** `json.dumps(..., ensure_ascii=True, indent=2)`, aware `datetime` objects
(`timezone.utc`), and `timedelta.days`.

**Docs:** [`json`](https://docs.python.org/3.12/library/json.html) ·
[`datetime`](https://docs.python.org/3.12/library/datetime.html)

### `os.replace` and the state file

`--notify` keeps, in one versioned JSON file (`.tlsvigil-state.json` by default), what each channel
was last told and how many runs in a row an entry was `error`, so a warning is told once and
reminded on fixed days instead of on every run. The file is read with a size cap and validated key
by key; any problem makes the run a first run, which may repeat a warning but never hides one. It
is written to a temporary file in the same directory (`tempfile.mkstemp`), flushed to disk
(`os.fsync`) and moved over the old one with `os.replace`, so an interrupted run leaves the old
file or the new one, never half of it.

**Why this:** against SQLite (`sqlite3`), the state is a few kilobytes read and written once per
run, and one JSON file is readable and easy to keep between runs. Against writing the file in
place, the atomic rename costs a few lines.

**Learn:** `os.replace` (atomic on POSIX, and on Windows within one volume), a temporary file in
the target's directory, `os.fsync`, and why an unknown version is treated as no state at all.

**Docs:** [`os.replace`](https://docs.python.org/3.12/library/os.html#os.replace)

### `smtplib` and `email`

**Optional channel.** `mail.py` sends one plain-text e-mail in UTF-8 per run, over TLS from the
first byte (`"tls"`) or after `STARTTLS` (`"starttls"`), signed in with the user name and the
password of the environment. `STARTTLS` is required, never opportunistic: a server that does not
offer it, or whose certificate fails verification, ends the session before `AUTH`. The message is
an `email.message.EmailMessage` with the policy `email.policy.SMTP`, which refuses a line break in
a header. Small subclasses of `smtplib.SMTP` and `SMTP_SSL` put the whole session under one
deadline (the socket timeout is set to the time left before each line), cap a reply at 100 lines,
and greet the server as `localhost`, so the runner's name is never looked up or sent.

**Why this:** the standard library already speaks SMTP; an e-mail service SDK would be a dependency
and tie the tool to one vendor, and `aiosmtplib` would add one for a single message per run.

**Learn:** `smtplib.SMTP_SSL` against `starttls(context=...)`, `ehlo()` and `has_extn("starttls")`,
`email.message.EmailMessage` with `email.policy.SMTP`, `email.utils.make_msgid(domain=...)`, and
why a subject or body built from certificate or RDAP text is treated as plain data.

**Docs:** [`smtplib`](https://docs.python.org/3.12/library/smtplib.html) ·
[`email`](https://docs.python.org/3.12/library/email.html)

---

## Where it runs and how it warns

### GitHub Actions: schedule and composite Action

**Planned.** The tool runs from a workflow in the user's own repository, on a daily `cron` schedule,
on the free minutes of a public repository. A composite Action in `action.yml` wraps the install and
the run, and an example workflow ships in the repository and in the README.

**Why this:** the product is "zero infrastructure": the user needs no server, and the maintainer
hosts nothing, so a scheduled workflow in the user's repository is the whole runtime. A composite
Action (steps in YAML) needs no Docker image or JavaScript, which keeps the supply chain to the
Python code.

**Learn:** the `on.schedule` cron syntax (and that GitHub may start a run late or skip it, so a
daily check is best effort), `permissions:` for the `GITHUB_TOKEN`, composite Action syntax, and
pinning third-party Actions to a commit SHA.

**Docs:** [GitHub Actions](https://docs.github.com/en/actions) ·
[Composite Actions](https://docs.github.com/en/actions/sharing-automations/creating-actions/creating-a-composite-action)

### The GitHub REST API (issues)

**Default channel.** `github.py` keeps one issue in the user's repository, through the
`GITHUB_TOKEN` the workflow already has: opened when something needs attention, its title and body
rewritten on every run, a comment added when there is news (the comment is what notifies), and
closed when everything is fine again, so a problem that lasts a week is one issue, not seven. A run
finds its issue again by the number in the state file, or by the marker `<!-- tlsvigil -->` at the
top of the body and the author `github-actions[bot]`; labels are not used, because the API drops
them in silence for a token with `issues: write` only. A run makes four requests at most, through
`https.request()`, with the `X-GitHub-Api-Version` header.

**Why this:** against a mail or chat message per run, one self-updating issue needs no credential
beyond the built-in token, notifies through the GitHub account the user already has and does not
repeat itself.

**Learn:** the issues endpoints (create, update, list, close) and the comments endpoint, the
`issues: write` permission, the REST API versions, and why text from a certificate or an RDAP
answer reaches the issue inside a code span, never as markup, a link or a mention.

**Docs:** [REST API: issues](https://docs.github.com/en/rest/issues/issues)

### Telegram Bot API

**Optional channel.** `telegram.py` sends one message per run to a chat through a bot, with one
`POST` to `sendMessage` through `https.request()`, as plain text (no `parse_mode`) and with link
previews off, the bot token and the chat read from the environment.

**Why this:** it is free, needs no server and reaches a phone; the call is one `POST`, so the
shared HTTPS exchange is enough and no Telegram library is needed.

**Learn:** creating a bot with BotFather, `sendMessage` and the chat id, `link_preview_options`,
and keeping the token out of every log line and error message: it sits in the URL, so a message
never quotes the URL, and the Redactor removes the token and its percent-encoded form from every
text the command writes.

**Docs:** [core.telegram.org/bots/api](https://core.telegram.org/bots/api)

---

## Quality tools

### Ruff

Linter and import sorter in one, configured in `[tool.ruff]`. The rule set is pycodestyle, pyflakes,
isort, pyupgrade, bugbear, flake8-simplify, flake8-comprehensions, the Ruff-native rules and
pydocstyle with the Google convention, with `target-version = "py312"` so syntax newer than the
floor fails the lint.

**Why this:** against flake8 plus isort plus pyupgrade, one fast tool that reads `pyproject.toml`.

**Learn:** `ruff check` and `--fix`, the rule-selection codes in `select` and `ignore`,
`per-file-ignores`, and how `target-version` limits the syntax.

**Docs:** [docs.astral.sh/ruff](https://docs.astral.sh/ruff/)

### Black

The formatter, at line length 100. It decides layout so reviews are about the code.

**Why this:** against Ruff's own formatter, black is the long-established default, and the hooks
run `ruff check --fix` then `black`; the gate checks it separately (`black --check`), so a
formatting difference fails by its own name.

**Learn:** `black --check` against writing, and what the Claude Code hook does on every edit.

**Docs:** [black.readthedocs.io](https://black.readthedocs.io/)

### mypy (strict)

The static type checker, over `src`, `tests` and `.claude/hooks`, with `strict = true` and a few
extra error codes (`redundant-expr`, `truthy-bool`, `possibly-undefined`).

**Why this:** the code is fully annotated, and strict mode catches the `None` and `Any` slips that a
monitoring tool cannot afford.

**Learn:** `Optional` as `X | None`, `Protocol`, `TypedDict`, `# type: ignore[code]` (a bare ignore
is itself an error here), and reading a mypy message.

**Docs:** [mypy.readthedocs.io](https://mypy.readthedocs.io/)

### pytest and pytest-cov

The test runner and its coverage plugin. `[tool.pytest]` is strict (unknown markers and config keys
fail), `filterwarnings = ["error"]` turns a warning into a failure, and the 15 slowest tests end
every run. The CLI is driven in-process through `main(argv)`, and no test reaches the real network:
a local server on `127.0.0.1` or a recorded answer stands in.

**Why this:** against `unittest`, plain `assert`, fixtures and parametrisation make the many small
cases of a parser or a date rule cheap to write.

**Learn:** fixtures, `parametrize`, `tmp_path` and `monkeypatch`, running one test by node id, and
`--cov` with branch coverage.

**Docs:** [docs.pytest.org](https://docs.pytest.org/) ·
[pytest-cov](https://pytest-cov.readthedocs.io/)

### trustme and cryptography (dev only)

The test certificates. `trustme` makes, during the test run, a certificate authority, an
intermediate and leaves with chosen names and dates; `cryptography`, already its dependency, makes
the two certificates `trustme` has no option for (a self-signed leaf and a leaf without an
Authority Key Identifier, in `tests/certs.py`). A threaded server in `tests/tls_server.py` serves
them on `127.0.0.1` and `::1`, a second one in `tests/http_server.py` serves hand-written RDAP
documents over HTTPS (and the answers that misbehave: silent, dripping, endless), and an autouse
guard (`tests/network_guard.py`) fails any lookup or
connection that leaves the loopback interface. The notification tests add stateful fakes of the
GitHub and Telegram APIs on that HTTPS server (`tests/fake_apis.py`) and an SMTP server written on
`socketserver` (`tests/smtp_server.py`, spec `005`, ADR-11), with implicit TLS, `STARTTLS` and
replies that misbehave. Both packages are in the dev group only: the wheel
still declares no dependency.

**Why this:** against certificates committed to the repository, a certificate made at run time can
expire in exactly ten days on every run; a committed one has a fixed date and turns into an
expired one some day. Against the `openssl` command line, it is the same code on the Linux and
Windows runners. Against `pytest-socket` for the guard, the rule is narrow enough for a few dozen
lines of the standard library that the suite tests itself.

**Learn:** `trustme.CA()`, `issue_cert(...)` with `not_before` and `not_after`,
`configure_trust(context)`, `cryptography.x509.CertificateBuilder`, and how `monkeypatch` wraps
`socket.getaddrinfo` and `socket.socket.connect`.

**Docs:** [trustme](https://trustme.readthedocs.io/) ·
[cryptography](https://cryptography.io/)

---

## CI and supply chain

### GitHub Actions CI

`ci.yml` runs the gate (`ruff`, `black --check`, `mypy`, `pytest --cov`) on Python 3.12 to 3.15, on
Linux and on Windows, plus a `package` job that builds the sdist and wheel, runs `twine check
--strict` and fails if the wheel declares a runtime dependency. Third-party Actions are pinned to a
commit SHA, and the same gate runs in `.githooks/pre-commit`.

**Why this:** the repository is public, so the minutes are free, and running the same sequence
locally and in CI means a green commit is a green PR.

**Learn:** a build matrix, `uv sync --locked`, `permissions:` at the workflow level, and the SHA pin
with a version comment.

**Docs:** [GitHub Actions](https://docs.github.com/en/actions)

### CodeQL, dependency review and pip-audit

CodeQL runs static analysis on `main`, on pull requests and weekly; dependency review checks the
dependencies a pull request changes; `pip-audit` audits `uv.lock` against known vulnerabilities
every Monday. None of the three is a required check, so a new CVE cannot block the pull request that
fixes it.

**Why this:** a tool that runs in other people's CI is held to the supply-chain standard it asks of
them, and with no runtime dependency the audited graph is the dev tooling only.

**Learn:** reading a CodeQL alert, what dependency review blocks, and `uv export` feeding
`pip-audit`.

**Docs:** [CodeQL](https://codeql.github.com/docs/) ·
[pip-audit](https://github.com/pypa/pip-audit) ·
[dependency review](https://docs.github.com/en/code-security/supply-chain-security/understanding-your-software-supply-chain/about-dependency-review)

### Dependabot

Opens one grouped pull request per week for each ecosystem it watches (a major stays alone), after a
7-day cooldown on every new release; security updates ignore the schedule and the cooldown.

**Why this:** the cooldown mirrors `exclude-newer` in `[tool.uv]`: a release that is hijacked or
broken on its first day does not reach the lock.

**Learn:** `groups`, `cooldown`, and the difference between version updates and security updates.

**Docs:** [Dependabot](https://docs.github.com/en/code-security/dependabot)

---

## Smaller pieces

| Piece | Role |
|---|---|
| `twine` (via `uvx`) | `twine check --strict` in CI: the README renders on PyPI |
| `coverage` | the engine under pytest-cov; settings in `[tool.coverage.*]` |
| `shellcheck` | lints `.githooks/pre-commit` in CI |
| `.githooks/pre-commit` | the versioned quality gate; active once per clone with `git config core.hooksPath .githooks` |
| Claude Code hooks (`.claude/hooks`) | format on edit, validate at the end of a turn, and block a hand edit of `uv.lock` |

---

## What is deliberately not in the stack

- **requests, httpx** — nicer HTTP clients, but each is a dependency; a few `GET`s, `POST`s and `PATCH`es do not need one.
- **PyGithub, python-telegram-bot** — API client libraries, each a dependency for the one to four requests of a run.
- **cryptography, pyOpenSSL at run time** — would parse a certificate in detail; the `ssl` module already returns the date and verifies the chain. `cryptography` is in the dev group only, to make test certificates.
- **click, typer, rich** — nice command lines, but each is a dependency; `argparse` does the job.
- **PyYAML, pydantic** — a YAML configuration needs a dependency and a safe loader; TOML with `tomllib` and a hand-written check is enough for a small, fixed shape.
- **A WHOIS client** — WHOIS has no standard format; RDAP is JSON and standardised.
- **A hosted service, a database or a dashboard** — the product is zero infrastructure, and a security tool must never be an instance that scans a target the visitor names.
- **A Docker image, a JavaScript Action** — a composite Action over a pure-Python wheel keeps the release surface to the package.
- **Slack, Discord, Teams and other channels** — not built in; the issue, Telegram and e-mail cover the audience, and the `--json` output and the exit code let a later workflow step send the result anywhere else.
