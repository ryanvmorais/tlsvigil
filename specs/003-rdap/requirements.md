---
feature: Domain expiry over RDAP — the IANA bootstrap, the expiration event, bounded HTTPS requests, and the offline RDAP server
status: approved
date: 2026-11-27
related:
  - 001-foundation/design.md
  - 002-certificate/requirements.md
origin: conception
---

# 003 — Domain expiry over RDAP

## Context and problem

The second question TLSVigil answers is how many days the registration of a domain has left. A
certificate that renews itself is no help once the domain behind it lapses: the site, the e-mail
and the certificate all stop together, and a lapsed domain can be registered by someone else.

Spec `001` settled where the date comes from (ADR-2): the RDAP server that the IANA bootstrap names
for the top-level domain, the `expiration` event of the domain record, treated as optional, and no
fallback to WHOIS. Spec `002` delivered the certificate check as a library function with an
offline test harness. This spec does the same for domains: one module that asks a registry over
HTTPS and returns a result instead of raising, and the local server its tests talk to.

RDAP adds outcomes the certificate check does not have. Some top-level domains publish no RDAP
service at all (`.de`, `.io`, `.co`, `.eu`, `.cn`, `.ru` and `.jp`, among others), and some
registries answer without an `expiration` event (RDAP does not require it). Both leave the date
unknown, and the user has to see that it is unknown, not a pass. The registry can also be out of
reach, refuse the request, limit its rate, or answer with something that is not an RDAP domain
record.

This is also the first spec in which the tool reads an answer written by a server on the
internet. The answer is bounded in time and size, the request never follows a redirect to another
host, and nothing in the answer is trusted beyond what is read from it as plain data.

## Goals

- A **domain check** that returns a result instead of raising: the expiration date and the days
  left when the registry gives them, the reason when the date is unknown, the kind of error when
  the domain cannot be checked.
- A **classification against a warning threshold**: `ok`, `expiring`, `unknown` or `error`, so
  that the output and the notifications of later specs only have to present it.
- The **RDAP server chosen by the IANA bootstrap**, fetched once per run and shared by every
  domain checked in that run.
- **Bounded, passive requests**: HTTPS only, a timeout, a cap on the size of the answer, no
  redirect to another host, no retry and no proxy.
- An **offline test harness for HTTP**: a local HTTPS server that serves recorded RDAP and
  bootstrap documents, so that no test reaches a real registry.

## Non-goals

- The command-line interface, the configuration file, the output, the language switch and the
  exit codes: spec `004`. This spec delivers the module and its tests.
- **WHOIS**, for any top-level domain, as a fallback or otherwise (spec `001`, ADR-2).
- **Deriving the registered domain from a host name.** The user lists the domain as registered
  (`example.com.br`, not `www.example.com.br`); finding it from a host name needs the Public
  Suffix List, a dataset the tool would have to ship and keep current.
- Following the link to the registrar's RDAP server, or any other link in the answer: it would
  reach a server the bootstrap does not name.
- Interpreting the statuses of the domain (`client hold`, `pending delete`, `redemption period`
  and others) into a warning of their own. They are carried as plain text for the output to show.
- Contact data of the registrant, the registrar or anyone else in the record: it is not read.
- Keeping the bootstrap between runs, a bootstrap shipped inside the package, or a bootstrap
  server other than the IANA one.
- Converting internationalized names (with letters outside ASCII) to their `xn--` form.
- Checking several domains in parallel, and retrying a failed request.

## Personas

- As a **domain owner**, I want to know how many days the registration of my domain has left, so
  that a failed automatic renewal or an expired card does not take my site and e-mail down.
- As a **domain owner**, I want a domain whose date the registry does not publish reported as
  unknown, with the reason, so that silence is never read as "renewed".
- As a **domain owner**, I want a domain that could not be checked reported as such, so that a
  typo, a lapsed domain or a registry that is down does not silence the warning.
- As a **contributor**, I want the RDAP tests to run against recorded answers served locally, so
  that the suite is deterministic, offline and free of real registrants' data.

## Functional requirements

### RF-01 — A dated record gives its days left

- **Given** a domain whose RDAP record has an event with the action `expiration` and a valid date
  **When** the check runs for that domain
  **Then** the result carries the expiration date (in UTC) and the whole days left until it,
  rounded down, counted from the time the check was given (the current time by default).
- **Given** an expiration date that has already passed
  **When** the check runs
  **Then** the days left is negative (rounded down), and the result is still a dated result.
- **Given** a record with more than one `expiration` event
  **When** the check runs
  **Then** the earliest date is used.
- **Given** a dated record
  **When** the check runs
  **Then** the result also carries, as plain text, the domain name the registry returned, the
  statuses of the domain, and the host name of the RDAP server that answered.

### RF-02 — Classification against the warning threshold

- **Given** a dated result and a threshold of `N` days (an integer, `0` or more)
  **When** the result is classified
  **Then** it is `ok` when the days left is `N` or more, and `expiring` when it is fewer than `N`;
  an expiration date that has passed is therefore `expiring` with any threshold, `0` included.
- **Given** a result whose date is unknown
  **When** it is classified
  **Then** it is `unknown`, whatever the threshold.
- **Given** a domain that could not be checked
  **When** it is classified
  **Then** it is `error`, whatever the threshold.
- **Given** a negative threshold
  **When** the classification is asked for
  **Then** it is refused with a `ValueError`.

### RF-03 — An unknown date is reported, with a reason

- **Given** a domain whose top-level domain has no HTTPS RDAP service in the bootstrap
  **When** the check runs
  **Then** the result says the date is unknown with the reason `no_rdap_service`, and no request
  is sent for the domain.
- **Given** a domain whose RDAP record has no `expiration` event
  **When** the check runs
  **Then** the result says the date is unknown with the reason `no_expiration`, and still carries
  the domain name, the statuses and the server of RF-01.

### RF-04 — The RDAP server comes from the IANA bootstrap

- **Given** the bootstrap file of the IANA for DNS (RFC 9224)
  **When** a domain is checked
  **Then** the server is the HTTPS base URL of the entry whose label sequence is the longest
  suffix of the domain, and a base URL that does not use HTTPS is never used.
- **Given** a run that checks several domains
  **When** the bootstrap is needed
  **Then** it is fetched once and shared by every domain of the run.
- **Given** a bootstrap that cannot be fetched, or that is not a valid bootstrap document
  **When** a domain is checked
  **Then** the result is `error` with the kind `bootstrap`, and no request is sent to any RDAP
  server.

### RF-05 — A domain that cannot be checked is an error, never a pass

- **Given** an RDAP server whose name does not resolve, that refuses the connection, that does not
  answer within the timeout, or whose certificate fails verification
  **When** the check runs
  **Then** the result is `error` with the kind `dns`, `refused`, `timeout` or `tls`.
- **Given** an RDAP server that answers `404`
  **When** the check runs
  **Then** the result is `error` with the kind `not_found`: the registry has no such domain.
- **Given** an RDAP server that answers `429`
  **When** the check runs
  **Then** the result is `error` with the kind `rate_limited`, and the request is not repeated.
- **Given** an RDAP server that answers any other status outside `2xx`
  **When** the check runs
  **Then** the result is `error` with the kind `http`, and the message carries the status code.
- **Given** an answer that is not JSON, is not a domain object (`objectClassName` other than
  `domain`), names a domain other than the one asked for, or carries an `expiration` date that
  cannot be read
  **When** the check runs
  **Then** the result is `error` with the kind `invalid_response`.
- **Given** any failure of the network, of TLS, of HTTP or of the parsing
  **When** the check runs
  **Then** the check returns a result and does not raise, and the result carries the message of
  the failure as plain text, with the kind `other` when no kind above applies.

### RF-06 — The requests are bounded and passive

- **Given** any request (the bootstrap or a domain record)
  **When** it is sent
  **Then** it is a `GET` over HTTPS with a verified certificate and TLS 1.2 or newer, asks for
  `application/rdap+json`, identifies the tool and its version in the `User-Agent`, and sends no
  cookie, credential or other header that carries data.
- **Given** a timeout of `T` seconds (a number greater than `0`; 10 by default)
  **When** a server is slow to connect, to answer or to send the body
  **Then** the request gives up after `T` seconds and the result is `error` with the kind
  `timeout`.
- **Given** an answer larger than the cap set for it in the design
  **When** it is read
  **Then** the reading stops at the cap, and the result is `error` with the kind `too_large`.
- **Given** a redirect to another host, to another scheme or to another port
  **When** the answer arrives
  **Then** it is not followed, and the result is `error` with the kind `redirect`.
- **Given** a redirect to another path on the same host, scheme and port
  **When** the answer arrives
  **Then** it is followed, up to the limit set in the design; past it, the result is `error` with
  the kind `redirect`.
- **Given** proxy variables in the environment (`HTTPS_PROXY` and the like)
  **When** a request is sent
  **Then** they are ignored, as in the certificate check (spec `002`).

### RF-07 — The domain is validated before any request

- **Given** an empty name, a name with a scheme, a path, a port, whitespace or a character outside
  letters, digits, hyphens and dots, a name with a single label (a top-level domain alone), an IP
  address, a label longer than 63 characters or a name longer than 253, or a name with letters
  outside ASCII
  **When** the check is called
  **Then** it is refused with a `ValueError` and no request is sent; for letters outside ASCII, the
  message says to give the `xn--` form of the name.
- **Given** a name in capital letters or with one trailing dot (`Example.COM.`)
  **When** the check is called
  **Then** it is checked as the same name in lowercase, without the dot.
- **Given** a timeout that is not greater than `0`
  **When** the check is called
  **Then** it is refused with a `ValueError`.

### RF-08 — Offline HTTP test harness

- **Given** the test suite
  **When** a test needs an RDAP server or the bootstrap
  **Then** a fixture starts a local HTTPS server on `127.0.0.1`, with a certificate from the test
  authority of spec `002`, that serves the documents and statuses the test chooses (an answer,
  `404`, `429`, a redirect, an oversized body, a body that never ends), and stops it when the test
  ends.
- **Given** the recorded documents the tests serve
  **When** they are read
  **Then** they keep the shape of real registry answers and contain no personal data: names,
  addresses, e-mails and telephone numbers are absent or synthetic.
- **Given** the network guard of spec `002`
  **When** the RDAP tests run
  **Then** it still refuses any address outside the loopback interface.

## Non-functional requirements

### RNF-01 — Standard library only

The check uses `urllib`, `http`, `json`, `ssl` and `datetime` of the standard library (spec
`001`, ADR-1). The package declares no dependency.

### RNF-02 — Same verdict on every supported Python

For the same documents served by the same server, the result (status, reason or kind, days left)
is the same on Python 3.12 and on every newer version the CI tests, on Linux and on Windows. Where
a default of `urllib`, `ssl` or the date parser changed between versions, the check sets the
behaviour explicitly instead of inheriting it.

### RNF-03 — External text stays data

The domain name, the statuses, the server name and the messages a result carries are kept as
plain strings, exactly as read. They are never interpreted, and later specs escape them before
they reach an issue or a notification. Nothing in an answer decides which server is asked next.
No secret exists in this spec, and none is added to a result.

### RNF-04 — Hostile answers are refused within the bounds

Given an answer that is very large, never ends, is nested very deeply, or carries markup, control
characters or dates out of range, the check ends within its timeout and its size cap, returns an
`error` result or carries the text as data, and does not raise.

### RNF-05 — Fast, deterministic tests

The RDAP tests run offline, in a few seconds in total, and do not depend on the time of day: the
days left is computed against a time the test gives. The timeout scenarios use a server that never
answers and a timeout of a fraction of a second.

## Open questions

None.

## Resolved decisions

1. **No command in this spec** (2026-11-27): as in spec `002`, the check is a Python function,
   and the command, its output and its exit codes arrive in spec `004`.
2. **An unknown date is a result of its own** (2026-11-27): a domain with no RDAP service or no
   `expiration` event is `unknown`, never `ok`, so that the output can say what was not checked.
   Whether `unknown` opens a warning is decided in spec `005`.
3. **A passed expiration date is `expiring`, not a separate status** (2026-11-27): the days left
   goes negative and the threshold rule already makes it a warning, while the registry's statuses
   (grace or redemption period) say what the passed date means.
4. **The domain is given as registered** (2026-11-27): no Public Suffix List, so a host name such
   as `www.example.com` is answered by the registry as `not_found`.
5. **Internationalized names are given in their `xn--` form** (2026-11-27): the `idna` codec of
   the standard library follows IDNA 2003, while registries follow IDNA 2008, and the two map some
   names to different domains; a silent conversion could ask about someone else's domain.
6. **The earliest of several `expiration` events wins** (2026-11-27): an early warning costs a
   look, a late one costs the domain.
7. **No retry** (2026-11-27): the check runs again on the next scheduled run, and a retry would
   double the load on a registry that is already refusing or limiting requests.

## Tests

### RF-09 — RDAP test suite

The suite covers RF-01 to RF-08 against the local server: a dated record far from and close to
its expiration, a passed date, several `expiration` events, a record without one, a top-level
domain without RDAP service and one whose only base URL is not HTTPS, the longest-suffix choice of
the bootstrap, one bootstrap fetch for several domains, a bootstrap that fails and one that is
malformed; `404`, `429`, another error status, a body that is not JSON, an object that is not a
domain, a domain other than the one asked for, an unreadable date; a server that never answers,
an oversized body and one that never ends, a deeply nested body, a redirect to another host, to
HTTP and to another port, a redirect on the same host within and past the limit; the proxy
variables ignored; the input validation; the classification boundaries (`N - 1`, `N`, `0`,
negative days, negative threshold); and the guard still refusing a public address. A name that
does not resolve is tested without a real DNS lookup.
