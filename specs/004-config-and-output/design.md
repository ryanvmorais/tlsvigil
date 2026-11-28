---
feature: The command — the TOML configuration, the text and JSON output, the language switch and the exit codes
status: in progress
date: 2026-11-27
related:
  - 004-config-and-output/requirements.md
  - 002-certificate/design.md
  - 003-rdap/design.md
  - 001-foundation/design.md
origin: conception
---

# 004 — Configuration and output — design

## Overview

The command is a pipeline of four small modules behind `cli.py`. `config.py` turns either the TOML
file or the `--host` and `--domain` flags into one validated `Config`, or into a `ConfigError` that
lists every problem with its place. `runner.py` takes the `Config`, runs the certificate checks and
then the domain checks through one `RdapClient`, classifies each result against its threshold and
returns a `Report`. `output.py` renders the `Report` as text or as JSON. `i18n.py` holds the two
languages as a Python dictionary and is the only place a sentence a person reads is written.
`cli.py` parses the arguments, chooses the language, wires the four together and turns the outcome
into an exit code: `0`, `1`, `2`, or `3` for a bug.

Validation has one source of truth per rule: the config module calls the same validators the checks
use (`certificate.validate_host`, `rdap.normalize_domain`), which now raise a `ValueError` subclass
that carries a code, and maps the code to a translated sentence (ADR-2). Everything read from the
outside (a certificate field, an RDAP answer, an error message, a value from the file) passes
through one function that escapes control and format characters before it reaches a terminal
(ADR-4). The tests drive `main(argv)` against the local TLS and HTTPS servers of specs `002` and
`003`, through keyword-only arguments of `main` that a person cannot set from the command line
(ADR-7).

## Module layout

```text
src/tlsvigil/
  cli.py                     # changed: the `check` command, the flags, the exit codes (ADR-3)
  config.py                  # new: Config, the TOML loader, the validation (ADR-2, ADR-6, ADR-8)
  runner.py                  # new: run(config) -> Report
  output.py                  # new: render_text(), render_json(), printable() (ADR-4, ADR-5)
  i18n.py                    # new: Lang, the catalogue, translate() (ADR-1)
  certificate.py             # changed: validate_host() public, InvalidHost with a code
  rdap.py                    # changed: normalize_domain() public, InvalidDomain with a code
tests/
  test_config.py             # new: the file, the keys, the forms, every problem (RF-02, RF-04, RF-05)
  test_i18n.py               # new: both languages complete and with the same placeholders (RF-08)
  test_runner.py             # new: the run against the local servers, order, counts (RF-01, RF-09)
  test_output.py             # new: text lines, summary, escapes, JSON shape (RF-06, RF-07)
  test_cli_check.py          # new: main(argv) end to end, exit codes, flags (RF-01, RF-03, RF-09)
  test_readme_config.py      # new: the example tlsvigil.toml of both READMEs loads (RF-10)
  test_cli.py                # changed: a bare `tlsvigil` now exits 2 (RF-01)
  test_certificate.py        # changed: each bad host also asserts its code (ADR-2)
  test_rdap_validation.py    # changed: each bad domain also asserts its code (ADR-2)
README.md, README.pt-BR.md   # quick start, the example file, the keys, the flags, the exit codes
CLAUDE.md                    # commands, architecture, "Before you touch"
docs/stack.md                # argparse, tomllib and json in use; the diagram without "planned"
```

## Data model

```python
# i18n.py
class Lang(StrEnum):
    EN = "en"
    PT_BR = "pt-BR"

# config.py
DEFAULT_CONFIG: Final = "tlsvigil.toml"
MAX_CONFIG_BYTES: Final = 256 * 1024
DEFAULT_TIMEOUT: Final = 10.0
DEFAULT_CERTIFICATE_WARN_DAYS: Final = 14
DEFAULT_DOMAIN_WARN_DAYS: Final = 30

@dataclass(frozen=True, slots=True)
class HostEntry:
    host: str                      # as given, brackets removed from an IPv6 address
    port: int                      # 443 unless given
    warn_days: int                 # the entry's own, or the table's

@dataclass(frozen=True, slots=True)
class DomainEntry:
    domain: str                    # normalized by rdap.normalize_domain (RF-05)
    warn_days: int

@dataclass(frozen=True, slots=True)
class Config:
    lang: Lang | None              # None when the file does not set it (RF-08)
    timeout: float
    hosts: tuple[HostEntry, ...]   # in the order of the file or of the flags
    domains: tuple[DomainEntry, ...]

@dataclass(frozen=True, slots=True)
class Problem:
    place: str                     # "certificates.hosts[2].port", "lang", "" for the file itself
    message: str                   # a key of the catalogue
    params: Mapping[str, str]      # the values the sentence names, as plain text

class ConfigError(Exception):
    problems: tuple[Problem, ...]  # every problem found, in the order of the file
    lang: Lang | None              # the file's `lang` when it was valid, for the messages

# runner.py
@dataclass(frozen=True, slots=True)
class CertificateItem:
    entry: HostEntry
    result: certificate.CertificateResult
    status: certificate.Status

@dataclass(frozen=True, slots=True)
class DomainItem:
    entry: DomainEntry
    result: rdap.DomainResult
    status: rdap.Status

@dataclass(frozen=True, slots=True)
class Report:
    started_at: datetime           # aware, UTC
    certificates: tuple[CertificateItem, ...]
    domains: tuple[DomainItem, ...]

    def counts(self) -> dict[str, int]: ...      # total, attention and one per status value
    def needs_attention(self) -> bool: ...       # any status in ATTENTION

# runner.py
ATTENTION: Final = frozenset({"expiring", "invalid", "error"})   # status values (RF-09)

# cli.py
EXIT_OK: Final = 0
EXIT_ATTENTION: Final = 1
EXIT_USAGE: Final = 2
EXIT_INTERNAL: Final = 3

# certificate.py and rdap.py
class HostProblem(StrEnum): ...      # one member per rule of spec 002, RF-06
class InvalidHost(ValueError):
    code: HostProblem
class DomainProblem(StrEnum): ...    # one member per rule of spec 003, RF-07
class InvalidDomain(ValueError):
    code: DomainProblem
```

`ATTENTION` holds the string values, not enum members, because the two `Status` enums of specs
`002` and `003` are different types that share the values `ok`, `expiring` and `error`.

## Components

### Configuration loading (RF-02, RF-03; ADR-6)

`load_config(path)` opens the file in binary mode and reads at most `MAX_CONFIG_BYTES + 1` bytes; one
byte more than the cap is a problem (`config.too_large`), and so are a missing file, a directory and
a permission error, each with its own message. The bytes are decoded as `utf-8-sig`, so a leading
byte order mark is dropped and anything else that is not UTF-8 is a problem. The text goes to
`tomllib.loads`; a `TOMLDecodeError` becomes one problem whose parameters are the line and the
column read from the exception (its `lineno` and `colno` on Python 3.14 and newer, the
`(at line L, column C)` suffix of its message before that) and the parser's own sentence as detail.

`config_from_names(hosts, domains)` builds a `Config` from the flags with the defaults of RF-04
and runs the same entry validation as the file, with places such as `--host[1]`.

### Validation (RF-04, RF-05; ADR-2, ADR-8)

`validate(document)` walks the parsed dictionary against a fixed schema written as code: the
allowed keys of each table, the type of each value, and the bounds. It never stops at the first
problem; each one is appended to a list, and a `ConfigError` is raised at the end if the list is
not empty. The rules:

- **Unknown keys** at the top level, in `[certificates]`, in `[domains]` and in an inline entry are
  problems that name the key (resolved decision 5).
- **Types:** `type(value) is int` for `port` and `warn_days` (TOML has no `bool`-as-`int`, but the
  check keeps the rule of specs `002` and `003`); `int` or `float` for `timeout`, finite and greater
  than `0`, since TOML allows `inf` and `nan`; a string for `lang`, matched without regard to case.
- **Entries:** a string is parsed by the host syntax of ADR-8; an inline table needs `host` (or
  `name` for a domain) and may hold `port` and `warn_days` only. The host and the port then go to
  `certificate.validate_host`, the domain to `rdap.normalize_domain`; an `InvalidHost` or an
  `InvalidDomain` becomes a problem whose message is chosen by its code.
- **Duplicates:** a host is compared by its lowercase name without a trailing dot (an IPv6 address
  in its compressed form) and its port; a domain by its normalized name. The second occurrence is
  the problem, and its message names the place of the first.
- **Nothing to check:** no host and no domain is one problem with an empty place.

Every value of the file that a message repeats goes into `params` as the user wrote it, and is
escaped by `printable()` when the message is written.

### The run (RF-01)

`run(config, *, context=None, bootstrap_url=rdap.BOOTSTRAP_URL, now=None)` takes `started_at` from
`now` or the clock, checks each host with `check_certificate(host, port, timeout=config.timeout,
now=now, context=context)`, then creates one `RdapClient(timeout=config.timeout, context=context,
bootstrap_url=bootstrap_url)` and checks each domain, classifying each result with the entry's
`warn_days`. The client is created only when there is a domain, so a run without domains sends
nothing to the IANA (RF-01). The checks run in sequence and never raise for the network (specs
`002` and `003`); `run` adds no handling of its own.

### Languages (RF-08; ADR-1)

`i18n.py` holds `MESSAGES: Final[Mapping[str, Mapping[Lang, str]]]`, a dictionary from a message
key to its two sentences, written as `str.format` templates with named fields. `translate(lang,
key, **params)` looks the key up and calls `format_map(params)` on the template: the template is
ours, and a parameter is inserted as it is, never parsed as a template itself (RNF-02). Plurals have
two keys (`days.one`, `days.other`), chosen by the count; Portuguese and English agree on which
counts are singular, so no plural rule table is needed. The language of a run is the `--lang`
flag, else the file's `lang`, else `en`; for a `ConfigError`, the file's `lang` is used only when
that key itself was valid.

### Text output (RF-06; ADR-4)

`render_text(report, lang)` writes one line per item, the certificates first:

```text
EXPIRING  example.com:443       certificate expires in 9 days (2026-12-06)
OK        mail.example.com:465  certificate expires in 61 days (2027-01-27)
INVALID   old.example.com:443   certificate failed verification: expired
ERROR     typo.example.com:443  certificate not checked: the name does not resolve ([Errno 11001] getaddrinfo failed)
OK        example.com           domain expires in 301 days (2027-09-24)
UNKNOWN   example.de            domain expiry unknown: the registry has no RDAP service

6 checked: 3 need attention.
```

```text
VENCENDO      example.com:443       certificado vence em 9 dias (2026-12-06)
INVÁLIDO      old.example.com:443   certificado não passou na verificação: vencido
DESCONHECIDO  example.de            vencimento do domínio desconhecido: o registro não tem serviço RDAP

6 verificados: 3 precisam de atenção.
```

The status column is as wide as the widest label of the language, so the labels themselves tell an
attention line apart without colour (RF-06). The name column is as wide as the widest name of the
run, up to 40 characters; a longer name pushes its detail to the right instead of being cut. Dates
are the UTC date in ISO form (`YYYY-MM-DD`). Zero days left reads "expires today"; a negative count
on a domain reads "expired 3 days ago". The reason and the kind of error are translated words from
the catalogue, one per enum member; the message of an error follows in parentheses, as it came,
through `printable()`.

`printable(text)` replaces every character whose Unicode category is `Cc`, `Cf`, `Cs`, `Co`, `Cn`,
`Zl` or `Zp` (control and format characters, the bidirectional overrides, line and paragraph
separators) with its Python escape (`\x1b`, `‮`), so the result is one line and no terminal
sequence. It is applied to every value from the outside and from the file, not to the catalogue.

### JSON output (RF-07; ADR-5)

`render_json(report, exit_code)` builds a dictionary and writes it with `json.dumps(document,
ensure_ascii=True, indent=2)` and a final newline, so the document is pure ASCII (RNF-03) and every
control character inside a string arrives escaped by the encoder (RNF-02). `allow_nan` stays at its
default: no float reaches the document. The shape is in "Interfaces".

### The command line (RF-01, RF-03, RF-08, RF-09; ADR-3, ADR-7)

`build_parser()` gains a `check` sub-command; `--version` stays on the main parser. `check` takes
`--config PATH`, `--host HOST[:PORT]` and `--domain NAME` (both `action="append"`), `--json` and
`--lang`, whose `type` maps `en` and `pt-br` in any case to a `Lang` and raises
`ArgumentTypeError` otherwise. `--config` and the names are in a mutually exclusive group, so
giving both is a usage error (RF-03).

`main(argv=None, *, context=None, bootstrap_url=rdap.BOOTSTRAP_URL, now=None)`:

1. Sets `errors="backslashreplace"` on `sys.stdout` and `sys.stderr` when they support
   `reconfigure` (RNF-03).
2. Parses the arguments; `argparse` exits `2` for its own errors. No sub-command prints the usage
   to the standard error and returns `2`.
3. Loads the `Config` from the names or the file; a `ConfigError` is written to the standard error
   in the chosen language, one problem per line under a heading that names the file, and returns
   `2`. With `--json`, the standard output stays empty.
4. Runs the checks, renders the output to the standard output, and returns `1` when the report
   needs attention and `0` otherwise.
5. Wraps steps 3 and 4 in `except Exception`: it writes one translated line and the traceback to the
   standard error and returns `3` (ADR-3). A `BrokenPipeError` while writing (`tlsvigil check |
   head -1`) points the standard output at `os.devnull`, as the Python documentation advises, and
   keeps the exit code of the report.

The console script and `python -m tlsvigil` already call `sys.exit(main())`, so the code reaches
the shell unchanged.

## Interfaces

**Command line** (the user contract):

```text
tlsvigil --version
tlsvigil check [--config PATH | --host HOST[:PORT]... --domain NAME...] [--json] [--lang en|pt-BR]
```

| Exit code | Meaning |
|---|---|
| `0` | every result is `ok` or `unknown` |
| `1` | at least one result is `expiring`, `invalid` or `error` |
| `2` | usage error, configuration error or nothing to check; no check ran |
| `3` | internal error (a bug); the traceback is on the standard error |

**Configuration file** (TOML 1.0):

```toml
# Optional: "en" (default) or "pt-BR".
lang = "pt-BR"
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
```

**JSON document** (schema version 1):

```json
{
  "schema_version": 1,
  "tlsvigil_version": "1.0.0",
  "started_at": "2026-11-27T12:00:00+00:00",
  "exit_code": 1,
  "summary": {"total": 2, "attention": 1, "ok": 1, "expiring": 1, "invalid": 0, "unknown": 0, "error": 0},
  "certificates": [
    {
      "host": "example.com", "port": 443, "status": "expiring", "warn_days": 14,
      "checked_at": "2026-11-27T12:00:00+00:00", "not_after": "2026-12-06T23:59:59+00:00",
      "days_left": 9, "issuer_organization": "Let's Encrypt", "issuer_common_name": "R11",
      "reason": null, "error_kind": null, "message": null
    }
  ],
  "domains": [
    {
      "domain": "example.com", "status": "ok", "warn_days": 30,
      "checked_at": "2026-11-27T12:00:01+00:00", "expiration": "2027-09-24T04:00:00+00:00",
      "days_left": 301, "server": "rdap.verisign.com", "registry_name": "EXAMPLE.COM",
      "statuses": ["client transfer prohibited"],
      "reason": null, "error_kind": null, "message": null
    }
  ]
}
```

Every key is present in every item, `null` when it has no value. `status`, `reason` and
`error_kind` are the `StrEnum` values of specs `002` and `003`. Dates are `datetime.isoformat()`
of an aware UTC time.

**Python** (for the tests and for `cli.py`; not a public library API):

```python
def main(
    argv: Sequence[str] | None = None,
    *,
    context: ssl.SSLContext | None = None,
    bootstrap_url: str = rdap.BOOTSTRAP_URL,
    now: datetime | None = None,
) -> int: ...

def load_config(path: Path) -> Config: ...
def config_from_names(hosts: Sequence[str], domains: Sequence[str]) -> Config: ...
def run(config: Config, *, context=None, bootstrap_url=rdap.BOOTSTRAP_URL, now=None) -> Report: ...
def render_text(report: Report, lang: Lang) -> str: ...
def render_json(report: Report, exit_code: int) -> str: ...
def printable(text: str) -> str: ...
def translate(lang: Lang, key: str, **params: str | int) -> str: ...
def validate_host(host: str, port: int) -> None: ...          # certificate.py
def normalize_domain(domain: str) -> str: ...                  # rdap.py
```

**Network contract:** unchanged from specs `002` and `003`; the command adds no request of its own.

## ADRs

### ADR-1 — The two languages live in a Python dictionary, not in gettext catalogues

**Decision.** `i18n.py` holds every sentence as `MESSAGES[key][lang]`, and a test checks that each
key has both languages with the same placeholders.
**Alternatives.** (a) `gettext` with `.po` files compiled to `.mo`. (b) One module per language.
(c) The `locale` module.
**Why.** Two languages and well under a hundred sentences. (a) needs a compile step in the build,
binary files in the wheel and a tool to extract the strings, for no gain at this size; a missing
translation falls back to English in silence. (b) lets one language gain a key the other lacks
without anything noticing. (c) follows the system locale, which the requirements rule out
(resolved decision 8). A dictionary is plain data that mypy and the parity test check.
**Trade-off.** No translation tooling for a third language; adding one means editing the
dictionary by hand, and the parity test then lists every missing sentence.

### ADR-2 — The checks' validators are the single source of each rule, and carry a code

**Decision.** `certificate.validate_host` and `rdap.normalize_domain` become public and raise
`InvalidHost` and `InvalidDomain`, subclasses of `ValueError` with a `code`; the configuration
calls them and maps the code to a translated sentence.
**Alternatives.** (a) The configuration repeats each rule with its own message. (b) The
configuration shows the English message of the `ValueError` as it is. (c) A schema library.
**Why.** (a) lets the file accept a host the check then refuses, or the reverse, the day one rule
changes. (b) breaks RF-08: a configuration error in Portuguese with half of it in English. (c)
breaks the zero-dependency promise (spec `001`, ADR-1). Subclasses keep every existing test and
caller that expects a `ValueError`.
**Trade-off.** Specs `002` and `003` gain two public names and an enum each; the observable
behaviour of both checks is unchanged.

### ADR-3 — An internal error exits `3`, with the traceback on the standard error

**Decision.** `main` catches `Exception` around loading, running and rendering, writes one
translated line and the traceback, and returns `3`.
**Alternatives.** (a) Let the exception reach the interpreter, which exits `1`. (b) Catch it and
return `2`. (c) Hide the traceback.
**Why.** (a) makes a crash look like "something needs attention", and a script that handles `1`
would act on a result that does not exist (RF-09). (b) makes it look like a mistake in the file.
(c) leaves nothing to put in a bug report; the traceback holds no secret in this spec, and spec
`005` revisits it when tokens exist.
**Trade-off.** A bug prints a Python traceback to the person running the command; `3` is a code
of TLSVigil's own that a script has to know about.

### ADR-4 — Outside text is escaped, not stripped, before it reaches a terminal

**Decision.** `printable()` replaces control, format and separator characters with their Python
escape; the standard streams use `errors="backslashreplace"`; the JSON is written with
`ensure_ascii=True`.
**Alternatives.** (a) Strip those characters. (b) Replace each one with `?`. (c) Escape only the
ESC character.
**Why.** An escape sequence in a certificate's organization or in an RDAP status is either a
mistake or an attempt to rewrite the terminal; (a) and (b) hide that it was there, while an escape
shows it and keeps the text readable. (c) leaves the other controls, the bidirectional overrides
that reorder a line, and the line breaks that forge a second line of output.
**Trade-off.** A legitimate format character (a zero-width joiner in a name) is shown as an escape
in the text output; the JSON keeps the exact text.

### ADR-5 — The JSON has one list per kind, every key always present, and a schema version

**Decision.** `certificates` and `domains` are separate lists of flat objects whose keys never
depend on the status; a missing value is `null`; `schema_version` is the integer `1`.
**Alternatives.** (a) One list with a `kind` field and the union of the keys. (b) Leave out the
keys without a value. (c) JSON Lines, one object per result.
**Why.** The two results have different fields, so (a) fills each object with keys that never
apply to it. (b) makes `jq '.certificates[].days_left'` fail on some items and not others. (c)
splits the summary and the exit code from the results. Fixed keys and one version number are what
RNF-05 promises.
**Trade-off.** The document is larger than it needs to be, and a key added to one kind does not
appear in the other.

### ADR-6 — The file is read as bytes up to a cap and decoded with an optional byte order mark

**Decision.** At most 256 KiB are read; the bytes are decoded as `utf-8-sig` and parsed with
`tomllib.loads`.
**Alternatives.** (a) `tomllib.load` on the open file. (b) A cap of 1 MiB or none.
**Why.** (a) reads the whole file whatever its size and refuses a byte order mark, which some
Windows editors still write. A thousand hosts take about 40 KiB, so 256 KiB leaves room without
letting a wrong path (a log, a disk image) be read whole (RNF-04).
**Trade-off.** A file over 256 KiB is refused even when it is a real configuration.

### ADR-7 — The test seams are keyword-only arguments of `main`, never flags or variables

**Decision.** `main` takes `context`, `bootstrap_url` and `now` as keyword-only arguments; the
console script calls `main()` without them.
**Alternatives.** (a) Hidden flags (`--bootstrap-url`). (b) Environment variables
(`TLSVIGIL_BOOTSTRAP_URL`). (c) Monkeypatching module globals in the tests.
**Why.** (a) and (b) would let anyone who controls the arguments or the environment of a run, a
workflow step for instance, send the RDAP requests to a server the IANA does not name, against the
rule that the network reaches only what the configuration names (spec `003`). (c) couples the
tests to private names and leaks between tests when a patch is forgotten.
**Trade-off.** `main`'s signature carries three parameters only the tests use.

### ADR-8 — A host is written `host`, `host:port` or `[IPv6]:port`

**Decision.** A string entry with no `:` is a host on port `443`; one `:` separates the host and
the port; a string that starts with `[` is an IPv6 address in brackets, with an optional `:port`
after `]`; a string with two or more `:` and no brackets is an IPv6 address on port `443`.
**Alternatives.** (a) The port only in the table form. (b) The URL form (`https://host:port`).
**Why.** `host:port` is how people write a TLS endpoint, and the brackets are the convention of
RFC 3986 for an IPv6 address with a port. (b) suggests that a path or a scheme other than TLS
would work.
**Trade-off.** `2001:db8::1:8443` is read as an address on port `443`, not as a port; the
brackets are the only way to give a port to an IPv6 address.

## Impact on existing code

- **`cli.py`:** rewritten around the `check` sub-command; `build_parser()` and `main(argv)` keep
  their names. A bare `tlsvigil` changes from exit `0` to exit `2`, as spec `001` foresaw (its
  design, "the no-argument behaviour ... spec 004 replaces it"); `test_cli.py` changes with it.
- **`certificate.py`:** `_validate` splits into the public `validate_host(host, port)` (raising
  `InvalidHost`) and the private checks of the timeout and `now`. Every existing test passes
  unchanged, since `InvalidHost` is a `ValueError`.
- **`rdap.py`:** `_normalize_domain` becomes `normalize_domain` and raises `InvalidDomain`; the
  same holds for its tests.
- **No new dependency**, runtime or dev.
- **Docs:** both READMEs gain the real quick start (`uv run tlsvigil check --host example.com`),
  the example file of "Interfaces", the table of keys and defaults, the flags and the exit codes,
  and drop "the only command that exists so far"; the two rules on how a domain is written (as
  registered, in its `xn--` form) from spec `003` go into the keys table. `CLAUDE.md` gains the
  command in "Common commands", the four modules in "Architecture", rows in "Before you touch"
  and the end of "Today the CLI answers `tlsvigil --version` only". `docs/stack.md` moves
  `argparse`, `tomllib` and `json` to "in use" and removes "planned" from the diagram.

## Test strategy

- **Configuration** (`test_config.py`, files written to `tmp_path`): the example of "Interfaces"
  loads to the expected `Config`; each default; the string and table forms; the per-entry
  threshold; `[2001:db8::1]:8443`, `2001:db8::1` and `[::1]`; `Example.COM.` normalized; a byte
  order mark accepted; then a parametrized table of bad files, each asserting the place and the
  message key of its problem: missing, a directory, `MAX_CONFIG_BYTES + 1` bytes (and exactly the
  cap as the control that loads), Latin-1 bytes, invalid TOML with its line and column, an unknown
  key at each level, `warn_day`, a `[notifications]` table, a wrong type for each key, `port = 0`
  and `65536`, `warn_days = -1`, `timeout = 0`, `inf` and `nan`, `lang = "fr"`, a URL, a path,
  whitespace, a port in a table host, each `DomainProblem`, a duplicate host and a duplicate
  domain in another case, and an empty file. One file with five problems reports all five, in
  order. `config_from_names` with good and bad names.
- **Languages** (`test_i18n.py`, pure): every key has both languages; the two templates of a key
  name the same fields; every member of the reason and error-kind enums of specs `002` and `003`,
  and of `HostProblem` and `DomainProblem`, has a key; a parameter holding `{0}` or `{x}` is
  printed as it is, not formatted.
- **Output** (`test_output.py`, reports built by hand, no network): one line per status in both
  languages; singular, plural, zero and negative days; the column widths and a name over 40
  characters; the summary; `printable()` on ESC, `\r`, `\n`, U+202E, U+2028 and a plain accented
  name (the control, left unchanged); an issuer with an escape sequence rendered on one line; the
  JSON parsed back, with every key present on every status, `null` fields, the ISO dates, the
  counts, `ensure_ascii` (an accented issuer arrives as `é`), and the same document for
  `Lang.EN` and `Lang.PT_BR`.
- **End to end** (`test_cli_check.py`, `main(argv, context=client_context, bootstrap_url=...,
  now=...)` with `monkeypatch.chdir(tmp_path)` and `capsys`): a file with two hosts on the local
  TLS server (one valid, one with an expired leaf) and two domains on the local HTTPS server (one
  dated, one under a top-level domain without RDAP), asserting the order, the text and exit `1`;
  the same file with only the valid host and the dated domain giving `0`; an `unknown` domain alone
  giving `0`; an `error` alone giving `1`; `--json` with an empty standard error; `--lang pt-BR`
  over `lang = "en"`, and `lang = "pt-BR"` alone; `--LANG`-style case (`--lang PT-br`) accepted and
  `--lang fr` exiting `2`; `--host` with an invalid `tlsvigil.toml` in the directory ignored (and,
  as the control, the same directory without `--host` exiting `2`); `--config` with `--host`
  exiting `2`; a bare `tlsvigil` exiting `2`; a missing default file with the hint; a config error
  with `--json` leaving the standard output empty; `run` patched to raise `RuntimeError`, giving `3`
  and a traceback; a standard output that is a `TextIOWrapper` over `BytesIO` with
  `encoding="cp1252"` and an issuer in Japanese, ending with the escaped text and the exit code
  kept.
- **Negative paths with their controls:** an invalid file opens no connection (the guard and the
  local servers record nothing; the same file fixed records the connections); a run without
  domains sends no bootstrap request (the same run with one domain sends exactly one).
- **README** (`test_readme_config.py`): the `toml` block of each README loads with `load_config`
  and holds the same entries in both.
- **Guard:** every test runs under the network guard of spec `002`; the domains of the tests use
  top-level domains of the local bootstrap only.
- **Seen failing:** before each test is trusted, the line it guards is broken once: the
  unknown-key check removed, the problem list stopping at the first, the duplicate check removed,
  the per-entry threshold ignored, `utf-8-sig` turned into `utf-8`, the cap check off by one,
  `error` removed from `ATTENTION`, the flag and key precedence of the language swapped, `Cf`
  removed from the categories of `printable()`, `ensure_ascii` set to `False`, the `except
  Exception` removed (the crash then exits `1`), the client created even without domains, and the
  `--config`/`--host` group made non-exclusive. Each sabotage and its red run go into the tasks and
  the pull request.
- **Coverage:** `config.py`, `runner.py`, `output.py`, `i18n.py` and `cli.py` at 100% lines and
  branches; the `lineno` branch of the TOML error that only Python 3.14 and newer reach is marked
  and explained in the code.

## Deviations

1. **RF-08, ADR-1: the plural helper is a public function.** `plural(lang, key, count,
   **params)` sits beside `translate` in `i18n.py`: it picks `<key>.one` for a count of `1` and
   `<key>.other` for any other, and fills `count`. A count of days already passed is given as a
   positive number, so the caller writes "expired 3 days ago" from `3`.
2. **RF-02, ADR-6: the position of a TOML error comes from a pure function.**
   `toml_position(message, text, lineno, colno)` takes the attributes of the error as arguments
   (`None` before Python 3.14) instead of a branch on the version marked for coverage, so the
   tests run both paths on every Python. The detail is the message without its position suffix,
   the same text on every version (RNF-06).
3. **RF-05, ADR-8: a URL with one `:` is reported as a URL.** When a host string has one `:` and
   no valid port after it (`https://example.com`), the rules of the whole text run first, so the
   problem is `host.url_or_path` (or the whitespace or the empty host) and not a bad port.
4. **RF-04: a table or a list of the wrong type counts as written.** `certificates = 1` or
   `hosts = "example.com"` gives its own problem without "nothing to check" beside it; that
   problem stays for a file with no table, or with empty lists only.

## Implementation notes

- **Stage 0:** `HostProblem` has one member per check of `validate_host` (`empty`, `whitespace`,
  `url_or_path`, `port_in_host`, `port`); the `/`, `[`, `]` and `@` of a host are one check in
  spec `002`, so they share `url_or_path`. `DomainProblem` has one member per check of
  `normalize_domain` (twelve). The sentence of a rule lives under `host.<code>` or
  `domain.<code>`, and `test_i18n.py` fails when a member has none. The messages of the two
  exceptions stay the English text of specs `002` and `003`; only the configuration translates.
  Seen failing: a host with whitespace given `EMPTY` (3 red), an IPv4 domain given `BAD_LABEL`
  (1 red), the Portuguese `host.empty` removed (2 red), `{count}` renamed `{total}` in Portuguese
  (2 red), the result formatted a second time (4 red), `domain.too_long` renamed (1 red), and `0`
  made singular (1 red); each change was undone.
- **Stage 1:** the places count entries from 1 (`certificates.hosts[1]`, `--host[1]`), as a person
  counts the lines of a list; a key with a wrong value has the key's path as its place, and a rule
  of a table entry points at its field (`certificates.hosts[2].port`, `domains.names[1].name`).
  An entry with another problem is still remembered for the duplicate check, so a later copy of
  it is reported too. Windows opens a directory with a `PermissionError`, so that error is
  reported as a directory when the path is one. The catalogue gained 22 `config.*` sentences; a
  test reads every `config.*` key written in `config.py` and fails when the catalogue lacks one.
  Seen failing (each change undone): the unknown-key check removed (7 red), the collector
  raising at the first problem (5 red), the duplicate check removed (7 red), the per-entry
  threshold replaced by the table's (2 red), `utf-8-sig` turned into `utf-8` (1 red), the cap
  check written `>=` (1 red), `isfinite` removed (2 red), and the bracket rule of ADR-8 dropped
  (12 red).
