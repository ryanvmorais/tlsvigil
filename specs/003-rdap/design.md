---
feature: Domain expiry over RDAP — the IANA bootstrap, the expiration event, bounded HTTPS requests, and the offline RDAP server
status: approved
date: 2026-11-27
related:
  - 003-rdap/requirements.md
  - 002-certificate/design.md
  - 001-foundation/design.md
origin: conception
---

# 003 — Domain expiry over RDAP — design

## Overview

One new module, `src/tlsvigil/rdap.py`, holds the whole check. An `RdapClient` lives for one run:
it validates its settings, fetches the IANA bootstrap the first time a domain needs it, keeps that
bootstrap (or the failure to get it) for the rest of the run, and answers
`client.check_domain(name)` with a `DomainResult` value. A check validates the name, finds the
HTTPS base URL whose labels are the longest suffix of the name, sends one `GET
<base>domain/<name>` and turns every way the attempt can end into a result: a dated record with
its expiration and days left, an undated one with the reason, or an error with its kind. Nothing is
raised for a network, TLS, HTTP or parsing failure. A separate pure function, `classify(result,
warn_days)`, turns a result into `ok`, `expiring`, `unknown` or `error`, as in spec `002`.

Requests go through `http.client` with the verified context of the certificate check (ADR-2,
ADR-6), so no proxy, cookie or redirect policy is inherited from the environment. Each request has
one deadline for the whole exchange and a size cap for the body (ADR-3, ADR-4), and a redirect is
followed only on the same scheme, host and port (ADR-5). The tests serve hand-written documents
from a local HTTPS server on `127.0.0.1`, with a certificate from the test authority of spec
`002`, and the network guard keeps refusing everything else (ADR-7).

## Module layout

```text
src/tlsvigil/
  rdap.py                          # new: RdapClient, the result type, the bootstrap, classify()
tests/
  http_server.py                   # new: LocalHTTPServer, routes with chosen statuses and bodies
  conftest.py                      # changed: the https_server fixture
  data/rdap/                       # new: hand-written RDAP domain answers (JSON)
  test_rdap_validation.py          # new: domain, timeout and URL validation (RF-07)
  test_rdap_bootstrap.py           # new: bootstrap parsing and the longest suffix (RF-04)
  test_rdap_check.py               # new: the check against the local server (RF-01, RF-03 to RF-06)
  test_rdap_classify.py            # new: the classification boundaries (RF-02)
docs/stack.md                      # the RDAP section: `http.client`, in use
CLAUDE.md                          # architecture: the new module and the HTTP test server
```

## Data model

```python
class Outcome(StrEnum):          # what the registry gave
    DATED = "dated"
    UNDATED = "undated"
    ERROR = "error"

class Status(StrEnum):           # what classify() gives (RF-02)
    OK = "ok"
    EXPIRING = "expiring"
    UNKNOWN = "unknown"
    ERROR = "error"

class UnknownReason(StrEnum):    # why the date is unknown (RF-03)
    NO_RDAP_SERVICE = "no_rdap_service"
    NO_EXPIRATION = "no_expiration"

class ErrorKind(StrEnum):        # why the domain could not be checked (RF-04, RF-05, RF-06)
    BOOTSTRAP = "bootstrap"
    DNS = "dns"
    REFUSED = "refused"
    TIMEOUT = "timeout"
    TLS = "tls"
    NOT_FOUND = "not_found"
    RATE_LIMITED = "rate_limited"
    HTTP = "http"
    REDIRECT = "redirect"
    TOO_LARGE = "too_large"
    INVALID_RESPONSE = "invalid_response"
    OTHER = "other"

@dataclass(frozen=True, slots=True)
class DomainResult:
    domain: str                          # the name asked for, normalized (RF-07)
    checked_at: datetime                 # the `now` of the check, aware, UTC
    outcome: Outcome
    server: str | None = None            # host name of the RDAP server asked (RF-01)
    expiration: datetime | None = None   # DATED only, UTC
    days_left: int | None = None         # DATED only; negative once the date has passed
    registry_name: str | None = None     # `ldhName` as the registry returned it
    statuses: tuple[str, ...] = ()       # the `status` values, as plain text
    reason: UnknownReason | None = None  # UNDATED only
    error_kind: ErrorKind | None = None  # ERROR only
    message: str | None = None           # ERROR only, plain text

@dataclass(frozen=True, slots=True)
class Bootstrap:
    services: tuple[tuple[tuple[str, ...], str], ...]   # (labels of an entry, HTTPS base URL)
```

The enums share their names with those of `certificate.py` and live in their own module; a
caller that uses both imports the modules, not the names (`rdap.Status`, `certificate.Status`).

## Components

### Validation (RF-07)

`_normalize_domain(domain)` runs before any request and raises `ValueError` with a message that
names the problem. The name must be a `str`; it is refused when it is empty or carries
whitespace, `://`, `/`, `:`, `@` or `_`; when it has a character outside ASCII (the message says to
give the `xn--` form); when, after one trailing dot is removed and the name is lowercased, it has
fewer than two labels, an empty label, a label that does not match
`[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?`, more than 253 characters, or a last label made only of
digits (which also refuses every IPv4 address; an IPv6 address already fails on `:`).

`RdapClient.__init__` refuses a timeout that is not a finite `int` or `float` greater than `0` (a
`bool` included, as in deviation 2 of spec `002`) and a `bootstrap_url` that is not `https://`
with a host. `check_domain` refuses a naive `now`.

### The client and the bootstrap (RF-04; ADR-1)

```text
check_domain(name, now)
  normalize(name)                                   ── ValueError, before any request
  bootstrap = self._bootstrap()                     ── first call fetches; later calls reuse
     failure (any kind, any message) is kept        ── ERROR bootstrap, message of the failure
  base = bootstrap.base_url_for(name)               ── None → UNDATED no_rdap_service, no request
  answer = self._get(base + "domain/" + name)       ── see "The request"
  parse(answer)                                     ── see "Reading the answer"
```

- **Fetched once, lazily.** `_bootstrap()` fetches `bootstrap_url` on the first check that needs
  it and stores either the parsed `Bootstrap` or the `ERROR` result it led to. A run with no
  domain never fetches it, and a run whose bootstrap failed sends no other request.
- **Parsing.** The document must be a JSON object whose `services` is a list of pairs `[entries,
  urls]`, each a list of strings; anything else makes the bootstrap invalid as a whole (`ERROR
  bootstrap`, `invalid bootstrap document`). `version` and `publication` are not read. Entries
  are lowercased and split into labels.
- **The base URL of a service** is its first URL that starts with `https://`, has a host, and has
  no user information, query or fragment; a `/` is appended when it does not end with one (RFC
  9224, section 3). A service without such a URL is dropped, so its top-level domains end as
  `no_rdap_service`.
- **Longest suffix.** `base_url_for(name)` tries the suffixes of the name's labels from the
  longest to the shortest and returns the base URL of the first entry that equals one (RFC 9224,
  section 4).

### The request (RF-05, RF-06; ADR-2 to ADR-5)

`_get(url)` is used for the bootstrap and for the domain record alike.

```text
deadline = monotonic() + timeout
loop, at most 1 + MAX_REDIRECTS times:
  HTTPSConnection(host, port, timeout=remaining, context=create_context())
  GET path  Host, Accept: application/rdap+json, Accept-Encoding: identity,
            User-Agent: tlsvigil/<version>, Connection: close
  getresponse()                                     ── status line and headers
  301 302 303 307 308 → Location resolved against the URL (urljoin)
       same scheme, host (lowercase) and port → next turn of the loop
       otherwise, or no Location, or past the limit → ERROR redirect
  Content-Length > cap → ERROR too_large, body not read
  read1(64 KiB) until EOF; before each read the socket timeout = remaining time;
       total > cap → ERROR too_large; remaining <= 0 → ERROR timeout
  connection closed in every branch (try/finally)
```

| Exception or status | Result |
|---|---|
| `socket.gaierror` | `ERROR dns` |
| `ConnectionRefusedError` | `ERROR refused` |
| `TimeoutError`, or the deadline passed | `ERROR timeout` |
| `ssl.SSLError` (a failed verification of the RDAP server included) | `ERROR tls` |
| `http.client.HTTPException`, any other `OSError` | `ERROR other` |
| `404` | `ERROR not_found` |
| `429` | `ERROR rate_limited` |
| any other status outside `2xx` | `ERROR http`, message `HTTP <code>` |

`ssl.SSLError` is caught before `OSError`, of which it is a subclass. During the bootstrap fetch,
every row of this table becomes `ERROR bootstrap`, with the message of the row (RF-04). The
version in the `User-Agent` is `tlsvigil.__version__`, the one `tlsvigil --version` prints.

### Reading the answer (RF-01, RF-03, RF-05; ADR-8)

```text
json.loads(body)                       ── ValueError, RecursionError → ERROR invalid_response
object, objectClassName == "domain"    ── otherwise → ERROR invalid_response
ldhName, lowercased, without one trailing dot, == the name asked for
                                       ── missing or different → ERROR invalid_response
status: the string items of the list, in order (a missing or non-list status gives ())
events: items whose eventAction == "expiration"
   none → UNDATED no_expiration (with registry_name, statuses, server)
   every eventDate parsed; one that cannot be → ERROR invalid_response
   expiration = the earliest, in UTC; days_left = (expiration - now) // timedelta(days=1)
```

- **Dates.** An `eventDate` must be a string that `datetime.fromisoformat` reads, after the
  string is uppercased (RFC 3339 allows a lowercase `t` and `z`), and that carries an offset; a
  naive or unreadable date, or one out of range, is `invalid_response`. `fromisoformat` reads the
  same RFC 3339 forms on 3.12 and later, which RNF-02 asks for.
- **Rounding down.** The floor division gives `0` in the last 24 hours and `-1` in the first 24
  hours after the date (RF-01).
- **Bounds on the parser.** The body reaching `json.loads` is already under the cap; a deeply
  nested body raises `RecursionError` and a number of more than 4300 digits raises `ValueError`,
  and both are caught (RNF-04).
- **Text as data.** `registry_name`, `statuses` and `message` are the strings as read; `server`
  is the host of the URL that was asked, never a name taken from the answer (RNF-03).

### Classification (RF-02; ADR-1 of spec `002`)

`classify(result, warn_days)` follows deviation 3 of spec `002`: it refuses a threshold that is
not an `int` (a `bool` or `float` included) or is negative, before it looks at the outcome; then
it returns `UNKNOWN` for `UNDATED`, `ERROR` for `ERROR`, and for `DATED` `EXPIRING` when
`days_left < warn_days`, `OK` otherwise. A dated result without `days_left` (only one built by
hand) is refused with `ValueError`. A passed date is negative and therefore `EXPIRING` with any
threshold.

### Test harness (RF-08; ADR-7)

- **`LocalHTTPServer`** (`tests/http_server.py`). A context manager around
  `http.server.ThreadingHTTPServer`, bound to `127.0.0.1:0`, that wraps each accepted connection
  with a server context for a `trustme` leaf issued for `127.0.0.1`. The TLS handshake runs in the
  request thread, so a client that rejects the certificate never blocks the server, and the thread
  records its own exceptions as `LocalServer` does. Each path is given a route:
  - `Route(status, body, headers)`: a fixed answer;
  - `Route.redirect(status, location)`;
  - `Route.silent()`: reads the request and never answers, until the server stops;
  - `Route.drip(byte_interval)`: sends headers, then one byte at a time, forever;
  - `Route.endless()`: sends headers without `Content-Length`, then chunks, forever.

  The server records each request (path and headers), so a test can count the bootstrap fetches
  and read the `User-Agent`. `__exit__` stops the server and joins its threads.
- **`https_server` fixture** (`tests/conftest.py`): starts a `LocalHTTPServer` with a leaf from
  the `ca` fixture; the tests build the bootstrap document in code, with the server's own
  `https://127.0.0.1:<port>/rdap/` as the base URL, and give the client `client_context`.
- **Documents** (`tests/data/rdap/`): domain answers written by hand after the shape of a `.br`
  and a `.com` answer (the `objectClassName`, `ldhName`, `status`, `events`, `entities`, `links`
  and `notices` members). Names are `example.com.br` and `example.com`, dates are fixed, and each
  entity has a handle and roles only: no name, address, e-mail or telephone (RF-08). The tests
  pass a fixed `now`.

## Interfaces

Python only; no command, file or network contract changes for the user (requirements, resolved
decision 1).

```python
BOOTSTRAP_URL: Final = "https://data.iana.org/rdap/dns.json"
MAX_BOOTSTRAP_BYTES: Final = 2 * 1024 * 1024
MAX_DOMAIN_BYTES: Final = 512 * 1024
MAX_REDIRECTS: Final = 3

class RdapClient:
    def __init__(
        self,
        *,
        timeout: float = 10.0,
        context: ssl.SSLContext | None = None,
        bootstrap_url: str = BOOTSTRAP_URL,
    ) -> None: ...

    def check_domain(self, domain: str, *, now: datetime | None = None) -> DomainResult: ...

def classify(result: DomainResult, warn_days: int) -> Status: ...
```

`context` and `bootstrap_url` exist so that the tests can add a trust anchor and point at the
local server; `None` means `create_context()` of `certificate.py`. The names exported are
`RdapClient`, `classify`, `DomainResult`, `Bootstrap`, the four enums and the constants.

**Network contract** (third party): `GET https://data.iana.org/rdap/dns.json` once per run, then
`GET <base>domain/<name>` once per domain, each answered with `application/rdap+json` (RFC 9083
for the domain object, RFC 9224 for the bootstrap).

## ADRs

### ADR-1 — One client per run holds the bootstrap, fetched on first use

**Decision.** `RdapClient` fetches the bootstrap on the first domain that needs it and keeps the
parsed document, or the failure, for its lifetime; spec `004` creates one client per run.
**Alternatives.** (a) Fetch the bootstrap for every domain. (b) A module-level cache. (c) The
caller fetches the bootstrap and passes it to each check.
**Why.** (a) sends the IANA one request per domain for a file that changes a few times a year.
(b) is global state that leaks from one test to the next and outlives a run. (c) pushes the
handling of a failed fetch onto every caller, against the "returns a result, never raises" rule
of spec `002` (ADR-1). A lazy fetch also means a configuration without domains sends nothing.
**Trade-off.** The client is stateful: a bootstrap failure early in a run fails every domain of
that run, which is what RF-04 asks for, and a long-lived client would keep an old bootstrap
(nothing in this project keeps one past a run).

### ADR-2 — Requests go through `http.client`, not `urllib.request`

**Decision.** `_get` builds an `http.client.HTTPSConnection` per request and handles the status,
the redirect and the reading of the body itself.
**Alternatives.** (a) `urllib.request.build_opener` with a redirect handler that refuses and an
empty `ProxyHandler`, as `docs/stack.md` described it. (b) `requests` or `httpx`.
**Why.** The opener brings what this check has to switch off: the proxy variables of the
environment (RF-06), the `file:` and `ftp:` handlers, a redirect policy written as a hook, and
error statuses raised as `HTTPError`. With `http.client` there is nothing to switch off: no
environment is read, a redirect is a status the code looks at, and every status is a value. (b)
is a dependency (spec `001`, ADR-1).
**Trade-off.** About thirty lines that the opener would have given (the redirect loop, the
`Location` join), and no connection reuse; each request is one connection, which is all a daily
run needs.

### ADR-3 — One deadline bounds the whole request

**Decision.** Each request gets `deadline = monotonic() + timeout`. The connection is opened with
the time left, the socket timeout is set to the time left before the status line and before each
read of the body, and the read stops when the deadline passes. Redirects share the deadline of the
request that started them.
**Alternatives.** (a) The socket timeout alone, as in spec `002` (ADR-6). (b) A thread or a timer
that closes the socket at the deadline.
**Why.** A socket timeout bounds each read, not the sum: a server that sends one byte every few
seconds keeps (a) busy for as long as it likes, and RF-06 names a slow body. (b) relies on closing
a socket from another thread to wake a blocked read, which behaves differently on Linux and on
Windows.
**Trade-off.** The status line and the headers are read by `http.client` in one call: a server
that drips its headers one byte at a time can stretch that call beyond the deadline, up to the
header limits of `http.client` (65 536 bytes per line, 100 headers). Name resolution keeps the
system resolver's own timeout, as in spec `002`.

### ADR-4 — Size caps of 2 MiB for the bootstrap and 512 KiB for a domain, with no compression

**Decision.** The body is read in chunks of 64 KiB and the request fails as `too_large` past the
cap; a `Content-Length` over the cap fails before the body is read. `Accept-Encoding: identity` is
sent and nothing is decompressed.
**Alternatives.** (a) One cap for both. (b) Accept gzip and cap the decompressed size.
**Why.** The IANA DNS bootstrap is tens of kilobytes and a domain answer a few kilobytes to a few
tens; the caps leave more than ten times that room and still keep memory and the JSON parser
small (RNF-04). Without compression there is no compression bomb to defend against; a server that
compresses anyway sends bytes that are not JSON, which ends as `invalid_response`.
**Trade-off.** A registry that someday answers with more than 512 KiB, or the bootstrap growing
past 2 MiB, fails as `too_large` until the cap is raised in a new spec.

### ADR-5 — A redirect is followed only on the same scheme, host and port, three times at most

**Decision.** A `301`, `302`, `303`, `307` or `308` whose `Location`, resolved against the URL,
keeps `https`, the same host (case-insensitive) and the same port, is followed as a new `GET`, up
to `MAX_REDIRECTS = 3`. Any other redirect, a redirect without `Location`, or a fourth one is
`ERROR redirect`.
**Alternatives.** (a) Refuse every redirect. (b) Follow any HTTPS redirect, as RFC 7480 allows.
**Why.** The rule of the project is that the network reaches only what the configuration and the
bootstrap name; (b) would let a registry, or anyone able to forge its answer, send the tool to any
host. A same-host redirect (a moved path, a trailing slash) reaches nothing new, and refusing it
(a) would fail a registry over a cosmetic change.
**Trade-off.** A registry that delegates to another server through a redirect is reported as an
error with the kind `redirect` instead of being followed.

### ADR-6 — The RDAP server is verified with the certificate check's context

**Decision.** Every request uses `create_context()` of `certificate.py` (the system trust store,
TLS 1.2 or newer, the 3.12 verification flags, no common-name fallback), and a failed verification
of the RDAP server is `ERROR tls`.
**Alternatives.** (a) `ssl.create_default_context()` as each Python builds it. (b) A separate
context function for RDAP.
**Why.** (a) changes its verification flags with the Python version (spec `002`, ADR-2), against
RNF-02. (b) would duplicate settings that have no reason to differ. Unlike a monitored host, an
RDAP server whose certificate fails is not a finding about the user's domain: the domain simply
could not be checked.
**Trade-off.** `rdap.py` imports `certificate.py`, so a change to the context of the certificate
check also changes the context of the RDAP requests, which is the intent.

### ADR-7 — The tests serve hand-written documents from a local HTTPS server

**Decision.** `LocalHTTPServer` on the standard library serves documents written for the tests,
over TLS with a `trustme` leaf, and the real `RdapClient` talks to it.
**Alternatives.** (a) Answers recorded from real registries, committed as they came. (b) A mock of
`http.client` or `urllib` (`unittest.mock`, `responses`, `pytest-httpserver`).
**Why.** A recorded answer carries the entities of a real registration, with names, addresses and
e-mails (RF-08). A mock skips the code that matters most here, the socket timeouts, the TLS
verification, the redirect loop and the bounded read, and the packages are dependencies. The
local server runs that code unchanged, and the network guard of spec `002` already allows it.
**Trade-off.** The documents follow the shape of real answers but are not them; a registry quirk
that none of them reproduces is found in use, and becomes a new document and a test.

### ADR-8 — The answer must name the domain asked for

**Decision.** The `ldhName` of the answer, lowercased and without a trailing dot, must equal the
name asked for, or the result is `invalid_response`.
**Alternatives.** Trust the answer to be about the domain in the URL.
**Why.** A registry that answers for another object (a parent domain, a placeholder, a proxy's
error page in JSON) would otherwise give another domain's date as the user's. Checking one member
costs nothing and turns that into an error the user sees.
**Trade-off.** A registry that omits `ldhName` cannot be checked; RFC 9083 lists it for every
domain object, and the `.br` and `.com` answers carry it.

## Impact on existing code

- **No change** to `cli.py` or the CLI tests: the module is not wired to a command (requirements,
  resolved decision 1).
- **`certificate.py`:** unchanged; `rdap.py` imports `create_context()` and keeps its own
  one-line helper for the message of an exception.
- **`tests/conftest.py`:** gains the `https_server` fixture; the network guard is unchanged.
- **No new dependency**, runtime or dev: the server is `http.server`, the certificates come from
  the `trustme` already in the dev group.
- **Docs:** the section of `docs/stack.md` on `urllib.request` and RDAP becomes `http.client` and
  RDAP, in use (ADR-2); `CLAUDE.md` gains the module and the HTTP test server in "Architecture"
  and a row in "Before you touch". The READMEs already describe the domain check and its limits
  (no RDAP service, no `expiration` event); the two rules on how a domain is written (as
  registered, in its `xn--` form) belong to the configuration of spec `004` and are documented
  there.

## Test strategy

- **Against the local server** (`test_rdap_check.py`), with `client_context` and a fixed `now`:
  - dated: an expiration 90 days away, `N` days plus one hour away (days left `N`), two hours away
    (`0`), one hour ago (`-1`), and two `expiration` events (the earliest wins); each asserts the
    expiration, the days left, `registry_name`, `statuses` and `server`;
  - undated: a record without `expiration` (`no_expiration`, with the other fields); a top-level
    domain absent from the bootstrap and one whose only URL is `http://` (`no_rdap_service`), each
    asserting that the server saw no domain request, with a covered domain as the control that it
    does see one;
  - bootstrap: two domains through one client and one bootstrap request; a bootstrap answering
    `500`, one that is not JSON and one whose `services` is malformed, each giving `bootstrap`
    and no domain request; the longest suffix chosen between `br` and `com.br` entries
    (`test_rdap_bootstrap.py`, pure);
  - errors: `404`, `429` (one request only), `503` (`http`, `HTTP 503`), a body that is not JSON,
    an `objectClassName` of `entity`, an `ldhName` of another domain, an unreadable `eventDate`
    and a naive one (`invalid_response`); a port bound and closed (`refused`, timeout 5 s, as in
    spec `002`); the `silent` route and the `drip` route with a timeout of 0.3 s (`timeout`); a
    leaf the client does not trust (`tls`); a name that does not resolve, with
    `socket.getaddrinfo` patched to raise `gaierror` (`dns`);
  - bounds: a `Content-Length` of 600 KiB and the `endless` route (`too_large`); 300 000 nested
    `[` (`invalid_response`, not a `RecursionError` escaping); markup, control characters and a
    `javascript:` link in `ldhName` case and `status` values carried unchanged;
  - redirects: to another host (`localhost` for `127.0.0.1`), to `http://`, to another port
    (`redirect`, and the target saw no request); one and three same-host redirects followed;
    a fourth refused;
  - requests: the recorded headers carry `User-Agent: tlsvigil/<version>`, `Accept:
    application/rdap+json` and no `Cookie`, `Authorization` or `Proxy-Authorization`; with
    `HTTPS_PROXY` and `ALL_PROXY` set to a documentation address, the check still reaches the
    local server (positive control) and the guard records no connection to that address.
- **Validation** (`test_rdap_validation.py`): a parametrized table of bad names (empty, `https://`,
  a path, a port, whitespace, `_`, one label, an empty label, a 64-character label, 254
  characters, `pão.com.br` with the `xn--` hint in the message, `192.0.2.1`, `2001:db8::1`), bad
  timeouts (`0`, `-1`, `inf`, `nan`, `True`), an `http://` bootstrap URL and a naive `now`, each
  raising `ValueError` with the guard's records empty, and `Example.COM.` checked as
  `example.com` as the control.
- **Pure** (`test_rdap_classify.py`): `days_left` `N - 1`, `N`, `N + 1`, `-1` with threshold `0`;
  `undated` and `error` with any threshold; a negative, `float` and `bool` threshold.
- **Guard:** the existing `test_network_guard.py` stays as it is; every RDAP test runs under it, so
  any request that left the loopback interface fails the test.
- **Seen failing:** before each test is trusted, the line it guards is broken once: the floor
  division turned into `round`, `min` turned into `max` over the events, the `https` filter of the
  bootstrap removed, the bootstrap cache dropped, the same-host test of the redirect removed, the
  `Content-Length` check and the running total removed, the deadline check removed, the
  `ldhName` comparison removed, `RecursionError` left uncaught, the request sent through
  `urllib.request.urlopen` (which reads the proxy variables), and the `xn--` rule removed. Each
  sabotage and its red run go into the tasks and the pull request.
- **Coverage:** `src/tlsvigil/rdap.py` at 100% lines and branches; a branch only one platform
  reaches is marked and explained in the code.
