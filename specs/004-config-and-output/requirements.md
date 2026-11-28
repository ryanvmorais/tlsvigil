---
feature: The command — the TOML configuration, the text and JSON output, the language switch and the exit codes
status: done
date: 2026-11-27
related:
  - 001-foundation/design.md
  - 002-certificate/requirements.md
  - 003-rdap/requirements.md
origin: conception
---

# 004 — Configuration and output

## Context and problem

Specs `002` and `003` delivered the two checks as library functions: `check_certificate` and
`classify` for a `host:port`, `RdapClient.check_domain` and `classify` for a domain. Each returns a
result instead of raising, and each result already says whether it is `ok`, `expiring`, `invalid`,
`unknown` or `error` against a threshold. Nothing calls them yet: today the command answers
`tlsvigil --version` only, and a bare `tlsvigil` exits `0` without doing anything (spec `001`).

This spec turns the two checks into a command a person or a workflow can run. It needs four things
the checks left out on purpose: a file that says what to check and with which thresholds (spec
`001`, ADR-4: TOML read with `tomllib`); an output a person can read at a glance and a script can
parse; the two languages of the audience, English and Brazilian Portuguese; and an exit code that
tells a script, a `cron` job or a workflow step whether something needs attention.

The configuration is the first input a person writes by hand. A typo in a key, a port out of range
or a host written as a URL has to stop the run with a message that points at the line, before any
connection is opened, and never be skipped in silence: a host that is never checked is a warning
that never comes.

## Goals

- A **`tlsvigil check` command** that reads the configuration, runs every check in order and
  prints one result per host and per domain.
- A **TOML configuration** with the hosts, the domains, a warning threshold for each kind (and per
  entry when needed), the timeout and the language, validated in full before any check runs.
- **Names on the command line** (`--host`, `--domain`) for a quick check without a file.
- A **text output** for people and a **JSON output** (`--json`) for scripts, with a versioned shape.
- **English and Brazilian Portuguese** (`--lang en|pt-BR`, or `lang` in the file) for every text
  the command writes.
- **Exit codes** that separate "nothing needs attention", "something needs attention" and "the
  command could not run".

## Non-goals

- The notifications (the GitHub issue, Telegram, SMTP) and their keys in the configuration: spec
  `005`. In this spec a `[notifications]` table, or any other key not listed here, is a
  configuration error.
- The composite Action and the template repository: spec `006`.
- Merging names given on the command line with the names in the file: with `--host` or `--domain`,
  the file is not read.
- Finding the configuration in a parent directory, in the home directory or through an environment
  variable; reading it from standard input.
- Choosing the language from the system locale (`LANG`, `LC_ALL`): the default is English, the same
  on every machine and every CI runner.
- Translating the help and the usage errors that `argparse` writes (`--help`, an unknown flag).
- Colours, a progress bar, other output formats (CSV, SARIF, HTML) and languages other than the
  two above.
- Checking several hosts or domains in parallel, and running as a service or in a loop.

## Personas

- As a **site owner**, I want to list my hosts and domains in one small file with comments, so that
  the list lives in my repository and changes through a pull request.
- As a **site owner**, I want a typo in that file to stop the run with the line it is on, so that a
  host I meant to watch is never left out in silence.
- As a **person trying the tool**, I want `tlsvigil check --host example.com` to work without a
  file, so that I see what it does in one command.
- As a **Brazilian user**, I want the output in Portuguese, so that I can share it with a client
  or a colleague who does not read English.
- As an **operator who scripts**, I want a JSON document with a stable shape and an exit code, so
  that a `cron` job or a later workflow step can act on the result.

## Functional requirements

### RF-01 — `tlsvigil check` runs the checks

- **Given** a valid configuration
  **When** `tlsvigil check` runs
  **Then** every host listed gets a certificate check and every domain listed gets a domain check,
  one after the other, in the order of the file (the hosts first, then the domains), and one result
  is printed for each.
- **Given** a run with domains
  **When** the domains are checked
  **Then** they share one RDAP client, so the IANA bootstrap is fetched at most once (spec `003`,
  RF-04); a run without domains sends no request to the IANA.
- **Given** `tlsvigil` with no command
  **When** it runs
  **Then** it prints the usage and exits with code `2`, without checking anything; `--version`
  keeps the behaviour of spec `001`.

### RF-02 — The configuration file

- **Given** no `--config` flag
  **When** `tlsvigil check` runs
  **Then** it reads `tlsvigil.toml` in the current directory.
- **Given** `--config PATH`
  **When** `tlsvigil check` runs
  **Then** it reads that file instead.
- **Given** a file that does not exist, cannot be read, is not UTF-8, is not valid TOML or is
  larger than the cap set in the design
  **When** `tlsvigil check` runs
  **Then** no check runs, the problem is written to the standard error (with the line and the
  column for a TOML syntax error), and the exit code is `2`.
- **Given** the default file is missing and no name was given on the command line
  **When** `tlsvigil check` runs
  **Then** the message says so and shows how to give a host or a domain (`--host`, `--domain`, or a
  `tlsvigil.toml` file).

### RF-03 — Names on the command line

- **Given** one or more `--host HOST[:PORT]` and/or `--domain NAME` flags
  **When** `tlsvigil check` runs
  **Then** those names are checked, in the order given, with the default thresholds, timeout and
  language (or the `--lang` flag), and no configuration file is read, even if one exists.
- **Given** a name given this way that fails the validation of RF-05
  **When** `tlsvigil check` runs
  **Then** no check runs and the exit code is `2`, as for a bad entry in the file.

### RF-04 — The shape of the configuration

- **Given** a configuration file
  **When** it is read
  **Then** it may hold, and only hold:
  - `lang`: `"en"` or `"pt-BR"` (default `"en"`);
  - `timeout`: the seconds each TLS connection and each RDAP request may take, a number greater
    than `0` (default `10`);
  - a `[certificates]` table with `warn_days` (an integer, `0` or more; default `14`) and `hosts`,
    a list whose items are a string (`"example.com"`, `"mail.example.com:465"`,
    `"[2001:db8::1]:8443"`) or an inline table with `host`, and optionally `port` (default `443`)
    and `warn_days`;
  - a `[domains]` table with `warn_days` (an integer, `0` or more; default `30`) and `names`, a
    list whose items are a string (`"example.com.br"`) or an inline table with `name` and
    optionally `warn_days`.
- **Given** an entry with its own `warn_days`
  **When** it is classified
  **Then** its own threshold is used instead of the table's.
- **Given** a file with neither a host nor a domain
  **When** `tlsvigil check` runs
  **Then** it is a configuration error: a run that checks nothing never exits `0`.

### RF-05 — The whole configuration is validated before any check

- **Given** a file with an unknown key (a typo such as `warn_day`, or a table of a later spec), a
  value of the wrong type, a number out of range (a port outside `1` to `65535`, a negative
  threshold, a timeout not greater than `0`), a `lang` other than the two above, a host the
  certificate check refuses (a URL, a path, whitespace) or a domain the domain check refuses (spec
  `003`, RF-07)
  **When** `tlsvigil check` runs
  **Then** no connection or request is made, every problem found is written to the standard error
  with the place it is in (such as `certificates.hosts[2]`), and the exit code is `2`.
- **Given** the same `host:port` or the same domain listed twice (letter case and a trailing dot
  ignored)
  **When** `tlsvigil check` runs
  **Then** it is a configuration error that names the duplicate.
- **Given** a domain in capital letters or with a trailing dot
  **When** it is checked and printed
  **Then** it is shown in the normalized form the check uses (lowercase, without the dot).

### RF-06 — The text output

- **Given** a run without `--json`
  **When** the checks end
  **Then** the standard output has one line per result, in the order of RF-01, with the status, the
  name (`host:port` or the domain), and: the days left and the expiration date for `ok` and
  `expiring`; the reason, in words, for `invalid` and `unknown`; the kind of error and its message
  for `error`.
- **Given** the results of a run
  **When** the text output ends
  **Then** a last line sums up how many were checked and how many need attention.
- **Given** a person reading the output
  **When** a line needs attention (`expiring`, `invalid` or `error`)
  **Then** the status alone, without colour, tells it apart from an `ok` or `unknown` line.

### RF-07 — The JSON output

- **Given** `--json`
  **When** the checks end
  **Then** the standard output holds one JSON document and nothing else, with: a schema version
  (`1`), the version of TLSVigil, the time the run started, the exit code, a list of the
  certificate results and a list of the domain results, in the order of RF-01, and a count per
  status.
- **Given** a result in the JSON
  **When** it is read
  **Then** it carries the fields of the result of spec `002` or `003` (the name, the status, the
  dates in ISO 8601 with their UTC offset, the days left, the reason or the kind of error, the
  message, the issuer or the server and the statuses of the domain) and the threshold it was
  classified against; a field without a value is `null` (an empty list for the statuses of the
  domain).
- **Given** `--json` and any language
  **When** the document is written
  **Then** the keys and the values (the statuses, the reasons, the kinds of error) are the same
  English identifiers in every language; only the text output and the messages on the standard
  error are translated.
- **Given** a configuration error with `--json`
  **When** the command stops
  **Then** the standard output stays empty, the problem goes to the standard error, and the exit
  code is `2`.

### RF-08 — Two languages

- **Given** `--lang pt-BR`, or `lang = "pt-BR"` in the file
  **When** the command writes the text output, the summary or a configuration error
  **Then** it is in Brazilian Portuguese; otherwise it is in English.
- **Given** both the flag and the key
  **When** the language is chosen
  **Then** the flag wins.
- **Given** `--lang` with a value other than `en` or `pt-BR` (letter case ignored)
  **When** the command runs
  **Then** it is refused as a usage error, with the exit code `2`.
- **Given** text that comes from outside (a certificate issuer, an RDAP server name or status, the
  message of a network error)
  **When** it is printed in any language
  **Then** it is printed as it came, not translated.

### RF-09 — Exit codes

- **Given** a run in which no result needs attention (every result is `ok` or `unknown`)
  **When** the command ends
  **Then** the exit code is `0`.
- **Given** a run in which at least one result is `expiring`, `invalid` or `error`
  **When** the command ends
  **Then** the exit code is `1`, even if other results are `ok`.
- **Given** a usage error, a configuration error or a configuration with nothing to check
  **When** the command stops
  **Then** the exit code is `2`, and no check ran.
- **Given** an unexpected failure inside the command (a bug)
  **When** it happens
  **Then** the exit code is neither `0` nor `1`, so that a script never reads it as "all fine" or
  as an ordinary warning.

### RF-10 — Both READMEs show the command and the file

- **Given** `README.md` and `README.pt-BR.md`
  **When** this spec is done
  **Then** both show `tlsvigil check` from a clone, an example `tlsvigil.toml` with comments, the
  keys of RF-04 with their defaults, the `--json` and `--lang` flags, and the three exit codes, and
  the parity test of spec `001` still passes.

## Non-functional requirements

### RNF-01 — Standard library only

The command uses `argparse`, `tomllib` and `json` of the standard library, and the translations
live in the package as plain Python data, with no compiled catalogue and no dependency (spec `001`,
ADR-1).

### RNF-02 — External text cannot reach the terminal as control codes

Given a certificate issuer, an RDAP status or server name, or an error message that carries control
characters, escape sequences or line breaks, the text output shows them as visible escapes, on the
same line, and never writes them to the terminal as they are. The JSON output carries them escaped
by the JSON encoding. Nothing read from the outside is interpreted as a format string.

### RNF-03 — The output never fails on a character

On Windows, where a redirected standard output may use a legacy code page, and on any stream that
cannot encode a character of a result (a name or an issuer outside ASCII), the command still writes
the whole output, with that character replaced or escaped, and keeps its exit code. The JSON output
is pure ASCII.

### RNF-04 — The run is bounded

Each check keeps the bounds of its spec (the timeout, the size caps of spec `003`), so a run takes
at most about the number of checks times the timeout. The configuration file is read up to its
size cap, and loading it runs no code (spec `001`, ADR-4).

### RNF-05 — The JSON shape is a contract

The JSON document carries a schema version. Within the same version, a key is never renamed,
removed or given another type; a new key may be added. The text output is for people and is not a
contract.

### RNF-06 — Same output on every supported Python

For the same configuration and the same answers from the servers, the output (text and JSON) and
the exit code are the same on Python 3.12 and on every newer version the CI tests, on Linux and on
Windows.

## Open questions

None.

## Resolved decisions

1. **A `check` command, not a bare `tlsvigil`** (2026-11-27): running the checks is an explicit
   act, and a bare `tlsvigil` prints the usage instead of opening connections from wherever it was
   called.
2. **Default thresholds: 14 days for certificates, 30 for domains** (2026-11-27): a certificate
   renewed by automation is replaced well before 14 days are left, even with the shorter lifetimes
   the certificate authorities are moving to, so fewer than 14 means the renewal failed; a domain
   renewal can need a payment or a person, so it gets a month.
3. **`unknown` exits `0`** (2026-11-27): a domain under a top-level domain without RDAP (`.de`,
   `.io` and others) can never be checked, and a run that always fails teaches the user to ignore
   it; the text and the JSON still say that the date is unknown. Whether `unknown` reaches a
   notification is decided in spec `005`.
4. **`error` exits `1`** (2026-11-27): a host or a domain that cannot be checked is never a pass
   (specs `002` and `003`), so it needs the same attention as an expiring one.
5. **Unknown keys are errors** (2026-11-27): a typo in a threshold or a host would otherwise be
   ignored in silence; the cost is that a file written for a later version fails on an older one,
   with a message that names the key.
6. **The configuration is validated in full before the first connection** (2026-11-27): a run
   either checks everything the file lists or nothing, and the user sees every problem of the file
   at once instead of one per run.
7. **The JSON is in English in every language** (2026-11-27): a script that reads it must not
   break when the language of the run changes.
8. **No language from the locale** (2026-11-27): a CI runner and a laptop give the same output for
   the same file.

## Tests

### RF-11 — Configuration and output test suite

The suite drives the command through `main(argv)` against the local TLS and HTTPS servers of specs
`002` and `003`, with no real host, registry or DNS lookup. It covers: a file with hosts and
domains and the order of the results; a run without domains sending nothing to the bootstrap; the
default file, `--config`, a missing file, a file that is not UTF-8, invalid TOML (with its line),
an oversized file; `--host` and `--domain` with the file ignored; each key of RF-04 with its
default and its bounds, the string and table forms of an entry, the per-entry threshold, the IPv6
form with brackets; every kind of problem of RF-05 reported together and no connection made; the
duplicates; the text output of each status in both languages and the summary; the JSON document,
its keys, `null` fields and identical values in both languages; the exit codes `0`, `1` and `2`
with `unknown` alone giving `0`; an issuer or a status with control characters and a line break;
a stream that cannot encode a character; and a test that every message exists in both languages.
