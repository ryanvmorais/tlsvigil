---
feature: Domain expiry over RDAP — the IANA bootstrap, the expiration event, bounded HTTPS requests, and the offline RDAP server
status: done
date: 2026-11-27
related:
  - 003-rdap/requirements.md
  - 003-rdap/design.md
origin: conception
---

# 003 — Domain expiry over RDAP — Tasks

Stage 0 builds the local HTTPS server first, so that every later test has a registry to talk to
without the network. Stage 1 adds the parts of the module that need no socket (the types, the
validation, the bootstrap parsing, the answer parsing and the classification), Stage 2 the request
and the check against the local server, and Stage 3 the documentation and the closing of the
spec.

The quality gate of every stage is, in order: `uv run ruff check .`, `uv run black --check .`,
`uv run mypy`, `uv run pytest --cov`.

## Stage 0 — Offline HTTP test harness

- [x] `tests/http_server.py`: `LocalHTTPServer` context manager over
  `http.server.ThreadingHTTPServer`, bound to `127.0.0.1:0`, with the TLS handshake in the request
  thread; `Route` with a fixed answer, `redirect`, `silent`, `drip` and `endless`; each request
  recorded (path and headers); the threads' exceptions recorded, not raised; stop and join on exit.
  — RF-08, ADR-7
- [x] `tests/conftest.py`: the `https_server` fixture, a `LocalHTTPServer` with a leaf for
  `127.0.0.1` issued by the `ca` fixture. — RF-08, ADR-7
- [x] `tests/test_http_server.py`: with `client_context` and `http.client`, a fixed route answers
  its status, headers and body; a redirect route answers its `Location`; an unknown path is `404`;
  the requests are recorded; a client that does not trust the leaf fails the handshake and the
  server keeps answering the next client. — RF-08. This file is not in the design's layout; it is
  recorded as a deviation (deviation 1), and so are the tests of the other routes (deviation 3).
- [x] See the harness tests fail once: record no request, and answer `200` for an unknown path;
  each goes red, then the change is undone. — RF-08. Also sabotaged, each red on its own test: the
  `Content-Length` of the route ignored, the redirect sent without `Location`, the routes read
  from a copy taken at start, the thread errors dropped, `silent` answering at once, `drip`
  sending three bytes per write, `endless` sending one chunk, the `Content-Type` of `drip` and
  `endless` dropped, the `Connection: close` header dropped.
- [x] Quality gate. — 111 passed (10 new) in 7.7 s; ruff/black/mypy green; `src` at 100%

## Stage 1 — Types, validation, bootstrap, answer and classification

- [x] `src/tlsvigil/rdap.py`: the module docstring, the constants, the four `StrEnum`s, the frozen
  `DomainResult` and `Bootstrap`, as in the design's data model and interfaces. — RF-01, RF-03,
  RF-04, RF-05, ADR-1
- [x] `src/tlsvigil/rdap.py`: `_normalize_domain(domain)` and the checks of `RdapClient.__init__`
  (timeout, `bootstrap_url`) and of `now`, with the rules of the design's "Validation". — RF-07
- [x] `src/tlsvigil/rdap.py`: the bootstrap parser (the `services` shape, the base URL filter, the
  appended `/`) and `Bootstrap.base_url_for(name)` by the longest suffix. — RF-03, RF-04
- [x] `src/tlsvigil/rdap.py`: the reading of a domain answer (`json.loads` with `ValueError` and
  `RecursionError` caught, `objectClassName`, `ldhName`, `status`, the `expiration` events, the
  date parsing, the earliest date, the days left by floor division), as a function that takes the
  body, the name, the server and `now` and returns a `DomainResult`. — RF-01, RF-03, RF-05,
  RNF-03, RNF-04, ADR-8
- [x] `src/tlsvigil/rdap.py`: `classify(result, warn_days)`. — RF-02
- [x] `tests/test_rdap_validation.py`: the parametrized bad names, timeouts, an `http://`
  bootstrap URL and a naive `now`, each raising `ValueError`; `pão.com.br` with the `xn--` hint in
  the message; `Example.COM.` normalized to `example.com` as the control. — RF-07, RF-09
- [x] `tests/test_rdap_bootstrap.py`: a valid document; a service whose only URL is `http://`, one
  with user information, a query or a fragment, dropped; the `/` appended; `br` against `com.br`
  chosen by the longest suffix; a top-level domain absent; a document that is not an object, has
  no `services`, or has a malformed pair, refused as a whole. — RF-03, RF-04, RF-09
- [x] `tests/test_rdap_answer.py`: the reading of the documents in `tests/data/rdap/` with a
  fixed `now`: 90 days, `N` days plus one hour, two hours (`0`), one hour ago (`-1`), two
  `expiration` events; no `expiration` event (`no_expiration`); not JSON, `objectClassName` of
  `entity`, another `ldhName`, no `ldhName`, an unreadable, a naive and an out-of-range date, a
  lowercase `t` and `z` read; 300 000 nested `[`; markup and control characters in `ldhName` case
  and `status` kept as read. — RF-01, RF-03, RF-05, RNF-03, RNF-04, RF-09. This file is not in the
  design's layout (the design tests the reading through the local server); it is recorded as
  deviation 4.
- [x] `tests/data/rdap/`: the hand-written answers (dated `.com.br` and `.com`, two `expiration`
  events, no `expiration` event), with entities that carry a handle and roles only. — RF-08
- [x] `tests/test_rdap_classify.py`: `days_left` `N - 1`, `N`, `N + 1`, `-1` with threshold `0`;
  `undated` and `error` with thresholds `0` and `365`; a negative, `float` and `bool` threshold; a
  dated result without `days_left`. — RF-02, RF-09
- [x] See the new tests fail once: the floor division turned into `round`, `min` turned into
  `max` over the events, the `https` filter of the bootstrap removed, the longest suffix turned
  into the shortest, the `ldhName` comparison removed, `RecursionError` left uncaught, the
  `xn--` rule removed, `<` turned into `<=` in `classify`; each goes red on the test that guards
  it, then the change is undone. — RF-01 to RF-04, RF-07. Also sabotaged, each red on its own
  test: the user information, query and fragment filter removed, the `/` not appended, a
  malformed service skipped instead of refusing the document, `RecursionError` left uncaught in
  the bootstrap, the `ldhName` compared without lowercasing, the `objectClassName` check removed,
  a naive date accepted, `OverflowError` left uncaught, the date read without uppercasing, an
  unreadable date skipped, the 253-character limit removed, a numeric last label accepted, the
  trailing dot kept, a `bool` timeout accepted, an `http://` bootstrap URL accepted, a naive
  `now` accepted, the threshold checked after the outcome.
- [x] Quality gate. — 294 passed (183 new) in 10 s; ruff/black/mypy green; `src` at 100%

## Stage 2 — The request and the check against the local server

- [x] `src/tlsvigil/rdap.py`: `_get(url, cap)`: the deadline, `HTTPSConnection` with
  `create_context()` (or the given context), the request headers, the redirect loop on the same
  scheme, host and port up to `MAX_REDIRECTS`, the `Content-Length` check, the bounded `read1`
  loop, the exception and status table of the design; the connection closed in every branch.
  — RF-05, RF-06, RNF-01, RNF-02, RNF-04, ADR-2 to ADR-6
- [x] `src/tlsvigil/rdap.py`: `RdapClient.check_domain`: validation, the lazy bootstrap with its
  failure kept, `no_rdap_service` without a request, the domain request and the reading of the
  answer; every failure of the bootstrap fetch as `ERROR bootstrap` with its message. — RF-01,
  RF-03, RF-04, RF-05, ADR-1
- [x] `tests/test_rdap_check.py`, dated and undated: a dated answer with `expiration`, days left,
  `registry_name`, `statuses` and `server`; `no_expiration`; `no_rdap_service` for an absent
  top-level domain and for an `http://` only service, with no domain request recorded, and a
  covered domain as the control that one is. — RF-01, RF-03, RF-09
- [x] `tests/test_rdap_check.py`, bootstrap: two domains through one client and one bootstrap
  request recorded; a bootstrap answering `500`, one that is not JSON and one with a malformed
  `services`, each giving `bootstrap` with no domain request recorded. — RF-04, RF-09
- [x] `tests/test_rdap_check.py`, errors: `404`, `429` (one request recorded), `503` (`HTTP 503`
  in the message); a bound-then-closed port (`refused`, timeout 5 s); `silent` and `drip` with a
  timeout of 0.3 s (`timeout`); a leaf the client does not trust (`tls`); `getaddrinfo` patched to
  raise `gaierror` (`dns`); each asserting the kind, a message and `None` for `days_left`. — RF-05,
  RF-09, RNF-05. Also: the deadline passing before the body is read, on a clock patched in the
  module only; a client without a context, whose bootstrap fails verification; a path that
  `http.client` cannot encode (`other`, deviation 7); a bad name and a naive `now` sending no
  request, with a good call on the same client as the control.
- [x] `tests/test_rdap_check.py`, bounds and redirects: a `Content-Length` of 600 KiB and the
  `endless` route (`too_large`); a redirect to `localhost` for `127.0.0.1`, to `http://` and to
  another port (`redirect`, and the target recorded no request); one and three same-host
  redirects followed; a fourth refused. — RF-06, RNF-04, RF-09, ADR-4, ADR-5. Also: a port out of
  range and a redirect without `Location` (`redirect`), and a chunked body read whole.
- [x] `tests/test_rdap_check.py`, requests: the recorded `User-Agent: tlsvigil/<version>` and
  `Accept: application/rdap+json`, and no `Cookie`, `Authorization` or `Proxy-Authorization`; with
  `HTTPS_PROXY` and `ALL_PROXY` set to a documentation address, the local server is still reached
  (positive control) and the guard records no connection to that address. — RF-06, RF-09
- [x] See the new tests fail once: drop the bootstrap cache, remove the same-host test of the
  redirect, remove the `Content-Length` check and the running total, remove the deadline check,
  map every `SSLError` to `other`, send the request through `urllib.request.urlopen`; each goes red
  on the test that guards it, then the change is undone. — RF-04 to RF-06. The `Content-Length`
  check and the running total were removed one at a time (`invalid_response` and `timeout`
  instead of `too_large`); without the deadline the `drip` check was still running after 3 s;
  through `urlopen` the guard refused a connection to the proxy address. Also sabotaged: the
  bootstrap fetched before the name is validated, red on the bad-argument test.
- [x] Coverage of `src/tlsvigil/rdap.py` at 100% of lines and branches, or each missing branch
  marked and explained in the code. — RF-09. One `pragma: no cover`: the socket that
  `http.client` always leaves after `request()`.
- [x] Quality gate. — 329 passed (35 new) in 21 s; ruff/black/mypy green; `src` at 100% of lines
  and branches

## Stage 3 — Documentation and closing

- [x] `docs/stack.md`: the section on `urllib.request` and RDAP becomes `http.client` and RDAP, in
  use, with why `http.client` was chosen. — ADR-2. Also: the domain check marked built in the
  diagram and the opening, and `tests/http_server.py` named in the section on the test harness.
- [x] `CLAUDE.md`: "Architecture" names `rdap.py` and `http_server.py`; "Before you touch" gains
  the row for `src/tlsvigil/rdap.py` (spec `003`, ADR-2 to ADR-5). — RF-08. Also:
  `tests/data/rdap/` in the tree, and a row for `tests/http_server.py` (ADR-7, deviation 3).
- [x] `specs/README.md`: the `003-rdap` row with delivery `v0.3` and status `done`.
- [x] Traceability: every RF, RNF and ADR cited here exists, every RF is cited by a task, and the
  `status` is the same in the three files and the roadmap. — RF-01 to RF-09, RNF-01 to RNF-05 and
  ADR-1 to ADR-8 are each cited by a task (ADR-3 inside the range ADR-2 to ADR-5), and no
  other identifier is.
- [x] `status: done` in the three files; deviations and measurements in the design.
- [x] Quality gate. — 329 passed in 20 s; ruff/black/mypy green; `src` at 100% of lines and
  branches
