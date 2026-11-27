---
feature: Domain expiry over RDAP — the IANA bootstrap, the expiration event, bounded HTTPS requests, and the offline RDAP server
status: approved
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

- [ ] `tests/http_server.py`: `LocalHTTPServer` context manager over
  `http.server.ThreadingHTTPServer`, bound to `127.0.0.1:0`, with the TLS handshake in the request
  thread; `Route` with a fixed answer, `redirect`, `silent`, `drip` and `endless`; each request
  recorded (path and headers); the threads' exceptions recorded, not raised; stop and join on exit.
  — RF-08, ADR-7
- [ ] `tests/conftest.py`: the `https_server` fixture, a `LocalHTTPServer` with a leaf for
  `127.0.0.1` issued by the `ca` fixture. — RF-08, ADR-7
- [ ] `tests/test_http_server.py`: with `client_context` and `http.client`, a fixed route answers
  its status, headers and body; a redirect route answers its `Location`; an unknown path is `404`;
  the requests are recorded; a client that does not trust the leaf fails the handshake and the
  server keeps answering the next client. — RF-08. This file is not in the design's layout; it is
  recorded as a deviation.
- [ ] See the harness tests fail once: record no request, and answer `200` for an unknown path;
  each goes red, then the change is undone. — RF-08
- [ ] Quality gate. — <result>

## Stage 1 — Types, validation, bootstrap, answer and classification

- [ ] `src/tlsvigil/rdap.py`: the module docstring, the constants, the four `StrEnum`s, the frozen
  `DomainResult` and `Bootstrap`, as in the design's data model and interfaces. — RF-01, RF-03,
  RF-04, RF-05, ADR-1
- [ ] `src/tlsvigil/rdap.py`: `_normalize_domain(domain)` and the checks of `RdapClient.__init__`
  (timeout, `bootstrap_url`) and of `now`, with the rules of the design's "Validation". — RF-07
- [ ] `src/tlsvigil/rdap.py`: the bootstrap parser (the `services` shape, the base URL filter, the
  appended `/`) and `Bootstrap.base_url_for(name)` by the longest suffix. — RF-03, RF-04
- [ ] `src/tlsvigil/rdap.py`: the reading of a domain answer (`json.loads` with `ValueError` and
  `RecursionError` caught, `objectClassName`, `ldhName`, `status`, the `expiration` events, the
  date parsing, the earliest date, the days left by floor division), as a function that takes the
  body, the name, the server and `now` and returns a `DomainResult`. — RF-01, RF-03, RF-05,
  RNF-03, RNF-04, ADR-8
- [ ] `src/tlsvigil/rdap.py`: `classify(result, warn_days)`. — RF-02
- [ ] `tests/test_rdap_validation.py`: the parametrized bad names, timeouts, an `http://`
  bootstrap URL and a naive `now`, each raising `ValueError`; `pão.com.br` with the `xn--` hint in
  the message; `Example.COM.` normalized to `example.com` as the control. — RF-07, RF-09
- [ ] `tests/test_rdap_bootstrap.py`: a valid document; a service whose only URL is `http://`, one
  with user information, a query or a fragment, dropped; the `/` appended; `br` against `com.br`
  chosen by the longest suffix; a top-level domain absent; a document that is not an object, has
  no `services`, or has a malformed pair, refused as a whole. — RF-03, RF-04, RF-09
- [ ] `tests/test_rdap_answer.py`: the reading of the documents in `tests/data/rdap/` with a
  fixed `now`: 90 days, `N` days plus one hour, two hours (`0`), one hour ago (`-1`), two
  `expiration` events; no `expiration` event (`no_expiration`); not JSON, `objectClassName` of
  `entity`, another `ldhName`, no `ldhName`, an unreadable, a naive and an out-of-range date, a
  lowercase `t` and `z` read; 300 000 nested `[`; markup and control characters in `ldhName` case
  and `status` kept as read. — RF-01, RF-03, RF-05, RNF-03, RNF-04, RF-09. This file is not in the
  design's layout (the design tests the reading through the local server); it is recorded as a
  deviation.
- [ ] `tests/data/rdap/`: the hand-written answers (dated `.com.br` and `.com`, two `expiration`
  events, no `expiration` event), with entities that carry a handle and roles only. — RF-08
- [ ] `tests/test_rdap_classify.py`: `days_left` `N - 1`, `N`, `N + 1`, `-1` with threshold `0`;
  `undated` and `error` with thresholds `0` and `365`; a negative, `float` and `bool` threshold; a
  dated result without `days_left`. — RF-02, RF-09
- [ ] See the new tests fail once: the floor division turned into `round`, `min` turned into
  `max` over the events, the `https` filter of the bootstrap removed, the longest suffix turned
  into the shortest, the `ldhName` comparison removed, `RecursionError` left uncaught, the
  `xn--` rule removed, `<` turned into `<=` in `classify`; each goes red on the test that guards
  it, then the change is undone. — RF-01 to RF-04, RF-07
- [ ] Quality gate. — <result>

## Stage 2 — The request and the check against the local server

- [ ] `src/tlsvigil/rdap.py`: `_get(url, cap)`: the deadline, `HTTPSConnection` with
  `create_context()` (or the given context), the request headers, the redirect loop on the same
  scheme, host and port up to `MAX_REDIRECTS`, the `Content-Length` check, the bounded `read1`
  loop, the exception and status table of the design; the connection closed in every branch.
  — RF-05, RF-06, RNF-01, RNF-02, RNF-04, ADR-2 to ADR-6
- [ ] `src/tlsvigil/rdap.py`: `RdapClient.check_domain`: validation, the lazy bootstrap with its
  failure kept, `no_rdap_service` without a request, the domain request and the reading of the
  answer; every failure of the bootstrap fetch as `ERROR bootstrap` with its message. — RF-01,
  RF-03, RF-04, RF-05, ADR-1
- [ ] `tests/test_rdap_check.py`, dated and undated: a dated answer with `expiration`, days left,
  `registry_name`, `statuses` and `server`; `no_expiration`; `no_rdap_service` for an absent
  top-level domain and for an `http://` only service, with no domain request recorded, and a
  covered domain as the control that one is. — RF-01, RF-03, RF-09
- [ ] `tests/test_rdap_check.py`, bootstrap: two domains through one client and one bootstrap
  request recorded; a bootstrap answering `500`, one that is not JSON and one with a malformed
  `services`, each giving `bootstrap` with no domain request recorded. — RF-04, RF-09
- [ ] `tests/test_rdap_check.py`, errors: `404`, `429` (one request recorded), `503` (`HTTP 503`
  in the message); a bound-then-closed port (`refused`, timeout 5 s); `silent` and `drip` with a
  timeout of 0.3 s (`timeout`); a leaf the client does not trust (`tls`); `getaddrinfo` patched to
  raise `gaierror` (`dns`); each asserting the kind, a message and `None` for `days_left`. — RF-05,
  RF-09, RNF-05
- [ ] `tests/test_rdap_check.py`, bounds and redirects: a `Content-Length` of 600 KiB and the
  `endless` route (`too_large`); a redirect to `localhost` for `127.0.0.1`, to `http://` and to
  another port (`redirect`, and the target recorded no request); one and three same-host
  redirects followed; a fourth refused. — RF-06, RNF-04, RF-09, ADR-4, ADR-5
- [ ] `tests/test_rdap_check.py`, requests: the recorded `User-Agent: tlsvigil/<version>` and
  `Accept: application/rdap+json`, and no `Cookie`, `Authorization` or `Proxy-Authorization`; with
  `HTTPS_PROXY` and `ALL_PROXY` set to a documentation address, the local server is still reached
  (positive control) and the guard records no connection to that address. — RF-06, RF-09
- [ ] See the new tests fail once: drop the bootstrap cache, remove the same-host test of the
  redirect, remove the `Content-Length` check and the running total, remove the deadline check,
  map every `SSLError` to `other`, send the request through `urllib.request.urlopen`; each goes red
  on the test that guards it, then the change is undone. — RF-04 to RF-06
- [ ] Coverage of `src/tlsvigil/rdap.py` at 100% of lines and branches, or each missing branch
  marked and explained in the code. — RF-09
- [ ] Quality gate. — <result>

## Stage 3 — Documentation and closing

- [ ] `docs/stack.md`: the section on `urllib.request` and RDAP becomes `http.client` and RDAP, in
  use, with why `http.client` was chosen. — ADR-2
- [ ] `CLAUDE.md`: "Architecture" names `rdap.py` and `http_server.py`; "Before you touch" gains
  the row for `src/tlsvigil/rdap.py` (spec `003`, ADR-2 to ADR-5). — RF-08
- [ ] `specs/README.md`: the `003-rdap` row with delivery `v0.3` and status `done`.
- [ ] Traceability: every RF, RNF and ADR cited here exists, every RF is cited by a task, and the
  `status` is the same in the three files and the roadmap.
- [ ] `status: done` in the three files; deviations and measurements in the design.
- [ ] Quality gate. — <result>
