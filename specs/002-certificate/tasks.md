---
feature: Certificate check — verified TLS handshake to host:port, days left, a failed verification as a finding, and the offline test server
status: done
date: 2026-11-27
related:
  - 002-certificate/requirements.md
  - 002-certificate/design.md
origin: conception
---

# 002 — Certificate check — Tasks

Stage 0 builds the offline harness first, so that no later test can reach the network. Stage 1
adds the parts of the module that need no socket, Stage 2 the check itself against the local
server, and Stage 3 the documentation and the closing of the spec.

The quality gate of every stage is, in order: `uv run ruff check .`, `uv run black --check .`,
`uv run mypy`, `uv run pytest --cov`.

## Stage 0 — Offline test harness

- [x] `pyproject.toml`, `uv.lock`: add `trustme` and `cryptography` to the dev group with `>=`
  floors at their current stable releases, then `uv lock` (never edit the lock by hand) and
  `uv sync`. — ADR-4, RNF-01. `trustme>=1.2.1` and `cryptography>=50.0.2`; the lock gained
  `trustme`, `cryptography`, `cffi`, `idna` and `pycparser`.
- [x] `tests/conftest.py`: `NetworkAccessError(AssertionError)` and the autouse guard over
  `socket.getaddrinfo`, `socket.socket.connect` and `connect_ex` (resolve `localhost` and IP
  literals only; connect to `127.0.0.0/8` and `::1` only), recording each allowed connect call.
  — RF-07, ADR-5. The guard lives in `tests/network_guard.py`, installed by the autouse fixture
  of `conftest.py` (design, deviation 1); it records every lookup and connect, allowed or not.
- [x] `tests/test_network_guard.py`: resolving `localhost` and connecting to a listening loopback
  socket succeed (positive control); resolving `example.com` and connecting to `192.0.2.1` and
  `2001:db8::1` raise `NetworkAccessError` naming the address. — RF-07, RF-08. The positive
  control is a real TLS handshake with the `serve` server; the refusals cover `connect` and
  `connect_ex` for IPv4, IPv6 and a name passed straight to `connect`, plus `create_connection`
  (10 tests).
- [x] See the guard tests fail once: widen the allow list to every address; the refusal tests go
  red, then the change is undone. — RF-07. Done on 2026-11-27 in two runs: every IP address
  allowed turned the four IPv4 and IPv6 refusals red; every name allowed turned the four name
  refusals red. With the guard open, the sabotaged runs did try the network (connect timeouts to
  the documentation addresses, a DNS lookup of `example.com`), which is what the guard prevents.
- [x] `tests/tls_server.py`: `LocalServer` context manager (bind `127.0.0.1:0`, daemon thread,
  modes `tls`, `silent` and `plain`, an accepted-connection counter, the thread's exceptions
  recorded, not raised; close and join on exit). — RF-07
- [x] `tests/certs.py`: with `cryptography`, a self-signed leaf with a SAN, and a leaf signed by a
  given `trustme.CA` without an Authority Key Identifier. — RF-07, ADR-4
- [x] `tests/conftest.py`: fixtures `ca` (a fresh `trustme.CA`), `serve` (a `LocalServer` for a
  leaf, with its full chain or the leaf only, the leaf-only PEM in `tmp_path`). — RF-07
- [x] Quality gate. — green on Python 3.15.2 on 2026-11-27: ruff and black clean, mypy no issues
  in 16 files, 25 passed, 100% coverage of `src/tlsvigil`; `uv lock --check` passes

## Stage 1 — Result type, validation, context and classification

- [x] `src/tlsvigil/certificate.py`: the module docstring, the four `StrEnum`s and the frozen
  `CertificateResult`, as in the design's data model. — RF-01, RF-03, RF-04, ADR-1
- [x] `src/tlsvigil/certificate.py`: `_validate(host, port, timeout, now)` with the rules of the
  design's "Input validation". — RF-06. The timeout must also be finite (design, deviation 2);
  its tests come with the check in Stage 2.
- [x] `src/tlsvigil/certificate.py`: `create_context()` with the three explicit settings of the
  design's table. — RF-05, RNF-02, ADR-2, ADR-7
- [x] `src/tlsvigil/certificate.py`: `classify(result, warn_days)`. — RF-02, ADR-1. It also
  refuses a verified result without days left (design, deviation 3).
- [x] `tests/test_classify.py`: `days_left` `N - 1` → `expiring`, `N` and `N + 1` → `ok`; threshold
  `0` → `ok` with `days_left` `0`; a negative threshold → `ValueError`; `invalid` and `error`
  outcomes → their status with thresholds `0` and `365`. — RF-02, RF-08. Also a `float` and a
  `bool` threshold, a bad threshold on an `invalid` result, and a verified result without days
  left (13 tests).
- [x] `tests/test_certificate.py`: `create_context()` has the TLS 1.2 minimum, neither
  `VERIFY_X509_STRICT` nor `VERIFY_X509_PARTIAL_CHAIN`, `hostname_checks_common_name` off,
  `CERT_REQUIRED` and `check_hostname` on. — RF-05, RNF-02, ADR-2. A positive control checks
  that `VERIFY_X509_TRUSTED_FIRST` survives the two cleared flags (5 tests).
- [x] See the new tests fail once: change `<` to `<=` in `classify`, drop the negative-threshold
  check, leave `VERIFY_X509_STRICT` on, set the minimum to TLS 1.1; each goes red, then the change
  is undone. — RF-02, RNF-02. Done on 2026-11-27 on Python 3.15.2: `<=` turned the
  exactly-the-threshold and the threshold-of-zero tests red; without the negative check, the two
  negative-threshold tests; `VERIFY_X509_STRICT` left on, the 3.12-verification test; TLS 1.1 as
  the minimum, the five context tests (the `DeprecationWarning` is an error under
  `filterwarnings`), and the minimum-version assertion alone with that warning ignored. Three more:
  accepting any number turned the `float` and `bool` cases red, removing the `days_left` guard its
  test, and leaving `hostname_checks_common_name` on its test.
- [x] Quality gate. — green on Python 3.15.2 on 2026-11-27: ruff and black clean, mypy no issues
  in 19 files, 43 passed; `certificate.py` at 69%, the missing lines being the body of
  `_validate`, whose tests are in Stage 2

## Stage 2 — The check against the local server

- [x] `src/tlsvigil/certificate.py`: `check_certificate(...)`: validation, `create_connection`,
  `wrap_socket` with `server_hostname`, the exception-to-result mapping of the design's flow, the
  verification-code table, the days left with `max(0, …)`, the issuer names; both sockets in
  `with` blocks. — RF-01, RF-03, RF-04, RF-05, RNF-03, ADR-1, ADR-3, ADR-6. The message of an
  `invalid` result is `verify_message`, falling back to the exception text; an empty exception
  text falls back to its type, so `message` is never empty.
- [x] `tests/test_certificate.py`, verified: 90 days; `N` days plus one hour gives `N`; two hours
  gives `0`; the issuer organization and common name are read; an IP certificate checked as
  `127.0.0.1`; a leaf without an Authority Key Identifier verifies. — RF-01, RF-05, RNF-02, RF-08.
  Also a second short of 30 days (`29`), a `now` later than the expiry (`0`, the `max`), a `now`
  in another time zone kept in UTC, and the default context refusing the test CA (the positive
  control of `client_context`). A `trustme` CA has no common name, so the common name is read
  from a trusted self-signed leaf (design, deviation 5).
- [x] `tests/test_certificate.py`, invalid: expired, not yet valid, issued for `other.test` and
  checked as `localhost`, issued for `localhost` and checked as `127.0.0.1`, self-signed, signed by
  an untrusted CA, leaf sent without its intermediate; each asserts the reason, a non-empty
  message, `None` for `days_left` and `not_after`, and one accepted connection on the server.
  — RF-03, RF-08. The same leaf sent with its intermediate verifies (positive control).
- [x] `tests/test_certificate.py`, error: refused (a bound-then-closed port, timeout 5 s), timeout
  (`silent` server, 0.2 s), not TLS (`plain` server), a name that does not resolve (`getaddrinfo`
  patched to raise `gaierror`); each asserts the kind, a message and `None` for `days_left`;
  dates relative to one `now` per test.
  — RF-04, RF-05, RF-08, RNF-04. Three failures loopback cannot produce are injected (design,
  deviation 6): a connect timeout and an unreachable network through `create_connection`, and a
  peer certificate without `notAfter` (or without an issuer) through `getpeercert`.
- [x] `tests/test_certificate.py`, TLS 1.1: the positive control with a client context that allows
  TLS 1.1 (skipped when the platform cannot complete it), then `error` `tls` with the default
  context; the `ssl` deprecation warnings caught in that test only. — RF-05, RF-08. The control
  completed on Python 3.15.2 on Windows (no skip); the `serve` fixture gained a `configure` hook
  for the server context (design, deviation 4).
- [x] `tests/test_certificate.py`, validation: the parametrized bad hosts, ports, timeouts and a
  naive `now` raise `ValueError` with no connect call recorded by the guard; a valid call to the
  local server is the control. — RF-06, RF-08. 26 bad arguments, each asserting that the message
  names the argument and that the guard recorded no lookup and no connect; three IPv6 addresses
  pass `_validate` as the control of the `:` rule.
- [x] See the new tests fail once: remove the `max(0, …)`, move code 62 to `untrusted`, delete
  `except TimeoutError`, map every `SSLError` to `other`, remove the `:` rule from the validation;
  each goes red on the test that guards it, then the change is undone. — RF-01, RF-03, RF-04,
  RF-06. Done on 2026-11-27 on Python 3.15.2, ten runs, each red on the test that guards it: no
  `max` (the later-`now` test); code 62 and code 64 to `untrusted` (the another-name and the
  name-by-address cases); no `except TimeoutError` after the handshake (the `silent` server) and
  after the connect (the injected timeout); every `SSLError` to `other` (the `plain` server and
  TLS 1.1); every verification to `other` (the seven invalid cases and the default-context test);
  no `:` rule (the two host-with-port cases); `now` not converted to UTC (its test); the issuer
  key ignored (the two issuer tests).
- [x] Coverage of `src/tlsvigil/certificate.py` at 100% of lines and branches, or each missing
  branch marked and explained in the code. — RF-08. 100% of lines and branches, with no branch
  marked.
- [x] Quality gate. — green on Python 3.15.2 on 2026-11-27: ruff and black clean, mypy no issues
  in 19 files, 101 passed in 6 s, 100% coverage of `src/tlsvigil`. The slowest test is the
  refused port (2 s, the Windows loopback); the other checks take under 0.1 s each once the local
  server also listens on `::1` (design, deviation 7).

## Stage 3 — Documentation and closing

- [x] `docs/stack.md`: `trustme` and `cryptography`, dev only, with why they were chosen. — ADR-4
- [x] `CLAUDE.md`: "Architecture" names `certificate.py` and the test harness (`conftest.py`
  guard, `tls_server.py`, `certs.py`). — RF-07
- [x] `README.md` and `README.pt-BR.md`: two limits in "Scope and limitations", in step (a
  revoked certificate is not detected; a port that starts in plain text and upgrades with STARTTLS
  is not checked). — RF-08 of spec `001`
- [x] `specs/README.md`: the `002-certificate` row with delivery `v0.2` and status `done`.
- [x] Traceability: every RF, RNF and ADR cited here exists, every RF is cited by a task, and the
  `status` is the same in the three files and the roadmap.
- [x] `status: done` in the three files; deviations and measurements in the design.
- [x] Quality gate. — green on Python 3.15.2 on 2026-11-27: ruff and black clean, mypy no issues
  in 19 files, 101 passed in 5.4 s, 100% coverage of `src/tlsvigil`; the full local gate takes
  8.2 s.
