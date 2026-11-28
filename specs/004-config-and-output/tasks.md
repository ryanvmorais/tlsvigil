---
feature: The command — the TOML configuration, the text and JSON output, the language switch and the exit codes
status: in progress
date: 2026-11-27
related:
  - 004-config-and-output/requirements.md
  - 004-config-and-output/design.md
origin: conception
---

# 004 — Configuration and output — Tasks

Stage 0 lays the two foundations every later stage uses: the validators of specs `002` and `003`
gain a code, and the language module exists with its parity test. Stage 1 builds the configuration,
Stage 2 the run and the two outputs, Stage 3 the command that wires them, and Stage 4 the
documentation and the closing of the spec. Each stage adds the sentences it needs to the catalogue,
so the parity test of Stage 0 keeps both languages complete from the start.

The quality gate of every stage is, in order: `uv run ruff check .`, `uv run black --check .`,
`uv run mypy`, `uv run pytest --cov`.

## Stage 0 — Validators with a code, and the language module

- [x] `src/tlsvigil/certificate.py`: `HostProblem` (one member per rule of spec `002`, RF-06) and
  `InvalidHost(ValueError)` with `code`; the public `validate_host(host, port)` split out of
  `_validate`, which keeps the checks of the timeout and `now`; `check_certificate` unchanged in
  behaviour. — RF-05, ADR-2
- [x] `src/tlsvigil/rdap.py`: `DomainProblem` (one member per rule of spec `003`, RF-07) and
  `InvalidDomain(ValueError)` with `code`; `_normalize_domain` renamed `normalize_domain` and
  raising `InvalidDomain`; the callers updated. — RF-05, ADR-2
- [x] `tests/test_certificate.py` and `tests/test_rdap_validation.py`: each bad host
  and bad domain of the existing tables also asserts its `code`; the existing `ValueError`
  assertions stay. — RF-05, ADR-2
- [x] `src/tlsvigil/i18n.py`: `Lang`, `MESSAGES`, `translate(lang, key, **params)` with
  `format_map`, the plural helper (`days.one`, `days.other`), and the sentences of every
  `HostProblem` and `DomainProblem` in both languages. — RF-08, ADR-1. The helper is the
  public `plural()` (deviation 1).
- [x] `tests/test_i18n.py`: every key has both languages; the two templates of a key name the same
  fields; every member of `HostProblem` and `DomainProblem` has a key; a parameter holding `{0}`
  or `{x}` is printed as it is; `translate` with an unknown key raises `KeyError`. — RF-08,
  RNF-02, ADR-1
- [x] See the new tests fail once: one `HostProblem` and one `DomainProblem` given the wrong code,
  one Portuguese sentence removed, one placeholder renamed in one language, the parameter passed
  through `format` twice; each goes red, then the change is undone. — RF-05, RF-08. Also
  sabotaged, each red on its own test: the sentence of `domain.too_long` renamed, and
  `plural()` calling `0` singular.
- [x] Quality gate. — 381 passed (52 new); ruff/black/mypy green; `src` at 100% of lines and
  branches

## Stage 1 — The configuration

- [ ] `src/tlsvigil/config.py`: the constants, `HostEntry`, `DomainEntry`, `Config`, `Problem`
  and `ConfigError`, as in the design's data model. — RF-04, RF-05
- [ ] `src/tlsvigil/config.py`: `load_config(path)`: the binary read up to `MAX_CONFIG_BYTES + 1`,
  the missing file, the directory and the permission error, the `utf-8-sig` decoding, and
  `tomllib.loads` with the line and column of a `TOMLDecodeError` (`lineno`/`colno` when present,
  the message suffix otherwise, the 3.14 branch marked for coverage). — RF-02, RNF-04, ADR-6
- [ ] `src/tlsvigil/config.py`: `validate(document)`: the allowed keys of each table, the types
  and bounds (`inf` and `nan` refused), `lang` without regard to case, the string and table forms
  of an entry with the host syntax of ADR-8, `validate_host` and `normalize_domain` mapped by
  code, the duplicates naming the first place, nothing to check, every problem collected in
  order. — RF-04, RF-05, ADR-2, ADR-8
- [ ] `src/tlsvigil/config.py`: `config_from_names(hosts, domains)` with the defaults and the same
  entry validation, places `--host[N]` and `--domain[N]`. — RF-03, RF-05
- [ ] `src/tlsvigil/i18n.py`: the sentences of every configuration problem, in both languages.
  — RF-08
- [ ] `tests/test_config.py`: the example of the design's "Interfaces" loads to the expected
  `Config`; each default; both entry forms; the per-entry threshold; the IPv6 forms; a normalized
  domain; a byte order mark; the parametrized bad files of the design's test strategy, each with
  its place and message key; exactly the cap as the control of the cap plus one; five problems in
  one file reported in order; `config_from_names` with good and bad names. — RF-02 to RF-05, RF-11
- [ ] See the new tests fail once: the unknown-key check removed, the problem list stopping at the
  first, the duplicate check removed, the per-entry threshold ignored, `utf-8-sig` turned into
  `utf-8`, the cap check off by one, the `isfinite` check removed, the bracket rule of ADR-8
  dropped; each goes red, then the change is undone. — RF-04, RF-05
- [ ] Quality gate. —

## Stage 2 — The run and the output

- [ ] `src/tlsvigil/runner.py`: `CertificateItem`, `DomainItem`, `Report` with `counts()` and
  `needs_attention()`, `ATTENTION`, and `run(config, *, context, bootstrap_url, now)` with the
  `RdapClient` created only when there is a domain. — RF-01, RF-09
- [ ] `src/tlsvigil/output.py`: `printable(text)` over the categories `Cc`, `Cf`, `Cs`, `Co`,
  `Cn`, `Zl` and `Zp`. — RNF-02, ADR-4
- [ ] `src/tlsvigil/output.py`: `render_text(report, lang)`: the status and name columns, the
  detail of each status, the days (singular, plural, zero, negative) and the UTC date, the reason
  and the error kind in words, the message through `printable()`, the summary line. — RF-06,
  RF-08
- [ ] `src/tlsvigil/output.py`: `render_json(report, exit_code)` with the shape of the design's
  "Interfaces", `ensure_ascii=True`, `indent=2` and a final newline. — RF-07, RNF-03, RNF-05,
  ADR-5
- [ ] `src/tlsvigil/i18n.py`: the status labels, the details, the summary, and the words of every
  reason and error kind of specs `002` and `003`, in both languages. — RF-06, RF-08
- [ ] `tests/test_i18n.py`: every member of the reason and error-kind enums of specs `002` and
  `003` has a key. — RF-08
- [ ] `tests/test_runner.py`: against the local servers, the order of the items, the per-entry
  threshold applied, `counts()` and `needs_attention()` for each status; a run without domains
  sends no bootstrap request, with one domain sending exactly one as the control. — RF-01, RF-09
- [ ] `tests/test_output.py`: the text and JSON cases of the design's test strategy, on reports
  built by hand. — RF-06, RF-07, RNF-02, RNF-03, RNF-05, RF-11
- [ ] See the new tests fail once: `error` removed from `ATTENTION`, the client created even
  without domains, `Cf` removed from the categories of `printable()`, `ensure_ascii` set to
  `False`, a key left out of an `error` item, the singular key used for every count, the name
  column cut at 40 characters; each goes red, then the change is undone. — RF-01, RF-06, RF-07,
  RF-09, RNF-02
- [ ] Quality gate. —

## Stage 3 — The command

- [ ] `src/tlsvigil/cli.py`: `build_parser()` with the `check` sub-command, `--config`, the
  exclusive group with `--host` and `--domain`, `--json`, and `--lang` with its `type`; the
  English help. — RF-01, RF-03, RF-08
- [ ] `src/tlsvigil/cli.py`: `main(argv, *, context, bootstrap_url, now)` with the steps of the
  design: the streams reconfigured, no sub-command giving the usage and `2`, the language chosen,
  the `ConfigError` written to the standard error, the run and the render, `0` or `1`, `except
  Exception` giving `3` with the traceback, and the `BrokenPipeError` handling. — RF-01, RF-02,
  RF-08, RF-09, RNF-03, ADR-3, ADR-7
- [ ] `src/tlsvigil/i18n.py`: the sentences of the command (the missing default file with its
  hint, the heading of a configuration error, the internal error), in both languages. — RF-02,
  RF-08
- [ ] `tests/test_cli.py`: a bare `tlsvigil` exits `2` with the usage; `--version` unchanged.
  — RF-01
- [ ] `tests/test_cli_check.py`: the end-to-end cases of the design's test strategy, each with
  its exit code, and an invalid file opening no connection, with the same file fixed as the
  control. — RF-01 to RF-03, RF-06 to RF-09, RNF-03, RF-11
- [ ] See the new tests fail once: the flag and key precedence of the language swapped, the
  `except Exception` removed (the crash then exits `1`), the `--config`/`--host` group made
  non-exclusive, the streams left without `backslashreplace`, the file read even with `--host`,
  the configuration error printed to the standard output; each goes red, then the change is
  undone. — RF-03, RF-08, RF-09, RNF-03
- [ ] Quality gate. —

## Stage 4 — Documentation and closing

- [ ] `README.md` and `README.pt-BR.md`: the quick start with `uv run tlsvigil check --host
  example.com`, the example `tlsvigil.toml`, the table of keys with their defaults (with the
  domain written as registered and in its `xn--` form), the flags and the exit codes; "the only
  command that exists so far" removed. — RF-10
- [ ] `tests/test_readme_config.py`: the `toml` block of each README loads with `load_config` and
  holds the same entries in both; seen failing once with one host removed from one README.
  — RF-10
- [ ] `CLAUDE.md`: the command in "Common commands", the four modules in "Architecture", rows in
  "Before you touch", and the project overview no longer saying the CLI answers `--version`
  only. — RF-10
- [ ] `docs/stack.md`: `argparse`, `tomllib` and `json` in use; "planned" removed from the
  diagram. — RF-10
- [ ] `specs/README.md`: the `004-config-and-output` row with delivery `v0.4` and status `done`.
- [ ] `status: done` in the three files; deviations and measurements in the design.
- [ ] Quality gate. —
