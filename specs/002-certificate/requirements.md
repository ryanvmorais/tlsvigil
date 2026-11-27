---
feature: Certificate check — verified TLS handshake to host:port, days left, a failed verification as a finding, and the offline test server
status: in progress
date: 2026-11-27
related:
  - 001-foundation/design.md
origin: conception
---

# 002 — Certificate check

## Context and problem

The first question TLSVigil answers is how many days the certificate of a host has left. Spec
`001` settled how that is read (ADR-1): the date comes from a TLS handshake whose chain passed
verification, with the standard library's `ssl` module, and a chain that fails verification is the
warning itself, never a reason to connect again without verification to read the date anyway. This
spec turns that decision into the first module that opens a socket.

A check has more outcomes than "valid until". A certificate can be valid and far from its expiry,
valid and inside the warning threshold, or rejected (expired, not valid yet, issued for another
name, signed by an authority the system does not trust, sent without its intermediate). The host
can also be out of reach: the name does not resolve, nothing listens on the port, the server never
answers, or it answers without a TLS version the client accepts. Every one of these has to reach
the user as a distinct result, because a check that fails quietly is exactly the failure TLSVigil
exists to prevent.

This is also the first spec whose tests need a network, and the tests never reach a real one
(`specs/README.md`, "How a spec is tested"). The spec therefore brings the offline harness every
later spec reuses: a TLS server on `127.0.0.1` with certificates made during the test, and a guard
that fails any test trying to connect anywhere else.

## Goals

- A **certificate check** of one `host:port` that returns a result instead of raising: the days
  left and the expiry date when the chain verifies, the reason when it does not, the kind of error
  when the host cannot be checked.
- A **classification against a warning threshold**: `ok`, `expiring`, `invalid` or `error`, so
  that the output and the notifications of later specs only have to present it.
- A **bounded, passive connection**: one TLS handshake per check, with a timeout, that sends
  nothing after the handshake and closes the connection.
- A **verdict that does not depend on the Python version** the check runs on, from the 3.12 floor
  to the newest stable release.
- An **offline test harness**: a local TLS server, certificates with chosen dates and names made
  during the test, and a guard that refuses any connection outside the loopback interface.

## Non-goals

- The command-line interface for the check, the configuration file, the text and JSON output, the
  language switch and the exit codes: spec `004`. This spec delivers the module and its tests.
- The domain registration expiry over RDAP: spec `003`.
- Revocation (OCSP, CRL): the check does not ask whether a certificate was revoked.
- Fetching a missing intermediate certificate (AIA): it would reach a server the configuration does
  not name, so a server that sends an incomplete chain is reported as such.
- Protocols that start in plain text and upgrade to TLS (STARTTLS on SMTP, IMAP, XMPP and others).
  Ports that speak TLS from the first byte (443, 465, 993, 995 and the like) are checked.
- A trust store other than the one of the system the check runs on, a client certificate, and a
  connection through a proxy.
- Details of certificates other than the one the server presents for the host (the expiry of an
  intermediate is covered only through verification: an expired intermediate fails it).
- Checking several hosts in parallel.

## Personas

- As a **site owner**, I want to know how many days the certificate of my site has left, so that I
  have time to fix a renewal that did not happen.
- As a **site owner**, I want a certificate that browsers would reject reported as broken, with the
  reason, even when its date is far away, so that a wrong name or a missing intermediate does not
  hide behind a good date.
- As a **site owner**, I want a host that could not be checked reported as such, never as fine, so
  that a typo in a host name or a closed port does not silence the warning.
- As a **contributor**, I want tests that build their own certificates and server and cannot reach
  the internet, so that the suite is deterministic and runs offline.

## Functional requirements

### RF-01 — A verified certificate gives its days left

- **Given** a host whose certificate chain verifies against the system trust store and whose
  certificate names the host
  **When** the check runs for that `host` and `port`
  **Then** the result carries the expiry date (`notAfter`, in UTC) and the whole days left until
  it, rounded down, counted from the time the check was given (the current time by default).
- **Given** a certificate that expires in less than 24 hours and still verifies
  **When** the check runs
  **Then** the days left is `0`, not a negative number.
- **Given** a verified certificate
  **When** the check runs
  **Then** the result also carries the issuer's organization and common name as plain text, when
  the certificate has them.

### RF-02 — Classification against the warning threshold

- **Given** a verified certificate and a threshold of `N` days (an integer, `0` or more)
  **When** the result is classified
  **Then** it is `ok` when the days left is `N` or more, and `expiring` when it is fewer than `N`.
- **Given** a threshold of `0`
  **When** a verified certificate is classified
  **Then** it is always `ok`: the date alone never warns.
- **Given** a result whose chain failed verification, or a host that could not be checked
  **When** it is classified
  **Then** it is `invalid` or `error`, whatever the threshold.
- **Given** a negative threshold
  **When** the classification is asked for
  **Then** it is refused with a `ValueError`.

### RF-03 — A failed verification is a finding, with a reason

- **Given** a certificate that failed verification
  **When** the check runs
  **Then** the result is `invalid`, carries no days left and no expiry date, and names one reason:
  - `expired` — the certificate's validity period has ended;
  - `not_yet_valid` — its validity period has not started;
  - `hostname_mismatch` — it does not name the host that was checked;
  - `untrusted` — the chain does not lead to an authority the system trusts (self-signed, unknown
    issuer, or an intermediate the server did not send);
  - `other` — any other verification failure.
- **Given** an `invalid` result
  **When** it is read
  **Then** it also carries the verification message of the TLS library as plain text, so that the
  user sees what was wrong.
- **Given** a certificate that failed verification
  **When** the check runs
  **Then** no second connection is opened, with or without verification.

### RF-04 — A host that cannot be checked is an error, never a pass

- **Given** a host whose name does not resolve, a port where nothing listens, a server that does
  not answer within the timeout, or a server that does not complete a TLS handshake for a reason
  other than its certificate (no shared protocol version or cipher, not TLS at all)
  **When** the check runs
  **Then** the result is `error`, carries no days left, and names one kind: `dns`, `refused`,
  `timeout`, `tls` or `other`, with the message of the failure as plain text.
- **Given** any failure of the network or of the TLS library
  **When** the check runs
  **Then** the check returns a result and does not raise.

### RF-05 — The connection is bounded and passive

- **Given** a check
  **When** it connects
  **Then** it opens one TCP connection, sends the host name in the TLS handshake (SNI) when the
  host is a name and not an IP address, completes or fails the handshake, sends no application
  data, and closes the connection in every outcome.
- **Given** a timeout of `T` seconds (a number greater than `0`; 10 by default)
  **When** the server is slow to accept or to answer the handshake
  **Then** the connection and each step of the handshake give up after `T` seconds and the result
  is `error` with the kind `timeout`.
- **Given** a server that offers only TLS 1.1 or older
  **When** the check runs
  **Then** the handshake fails and the result is `error` with the kind `tls`: the oldest version
  accepted is TLS 1.2.
- **Given** a host that is an IPv6 or an IPv4 address
  **When** the check runs
  **Then** the certificate must name that address for the chain to verify.

### RF-06 — Inputs are validated before any connection

- **Given** a port outside `1` to `65535`, an empty host, a host with a scheme, a path or
  whitespace in it, a host that carries its own port (`name:443`, `[::1]:443`; an IPv6 address is
  given bare, `::1`), or a timeout that is not greater than `0`
  **When** the check is called
  **Then** it is refused with a `ValueError` and no connection is attempted.

### RF-07 — Offline test harness

- **Given** the test suite
  **When** a test needs a TLS server
  **Then** a fixture starts one on `127.0.0.1` with a certificate made for that test (chosen names,
  chosen validity dates, a chosen issuer, with or without its intermediate), and stops it when the
  test ends.
- **Given** any test
  **When** it tries to resolve or connect to an address outside the loopback interface
  (`127.0.0.0/8`, `::1`)
  **Then** the attempt is refused and the test fails with a message that names the address.
- **Given** the guard of the previous scenario
  **When** its own tests run
  **Then** a connection to the local server is allowed (positive control) and a connection to a
  public address is refused without reaching the network.

## Non-functional requirements

### RNF-01 — Standard library only

The check uses the `ssl` and `socket` modules of the standard library (spec `001`, ADR-1). Making
certificates for the tests may use a development dependency; the package itself declares none.

### RNF-02 — Same verdict on every supported Python

For the same certificate, the same server and the same trust anchors, the result (status, reason,
days left) is the same on Python 3.12 and on every newer version the CI tests, on Linux and on
Windows. Where the defaults
of `ssl` changed between versions, the check sets the behaviour explicitly instead of inheriting
it.

### RNF-03 — External text stays data

The issuer names and the messages a result carries are kept as plain strings, exactly as read.
They are never interpreted, and later specs escape them before they reach an issue or a
notification. No secret exists in this spec, and none is added to a result.

### RNF-04 — Fast, deterministic tests

The certificate tests run offline, in a few seconds in total, and do not depend on the time of
day: the days left is computed against a time the test gives, and the certificates are made with
dates relative to the moment the test runs. The timeout scenario uses a server that never answers
and a timeout of a fraction of a second.

## Open questions

None.

## Resolved decisions

1. **No command in this spec** (2026-11-27): the check is a Python function, and the command, its
   output and its exit codes arrive together in spec `004`, so the CLI is not shaped twice.
2. **An unreachable host is reported** (2026-11-27): a host that cannot be checked is an `error`
   result, not skipped, because a silent skip looks the same as a pass to the user.
3. **An incomplete chain is reported as `untrusted`** (2026-11-27): the check does not fetch the
   missing intermediate, which would reach a server the configuration does not name. Clients that
   do not fetch it either (many command-line tools, libraries and older devices) fail on the same
   server, so the warning is real.

## Tests

### RF-08 — Certificate test suite

The suite covers RF-01 to RF-07 against the local server: a valid certificate far from and close
to its expiry, under 24 hours from its expiry, expired, not yet valid, issued for another name,
self-signed, signed by an untrusted authority, sent without its intermediate; an IP address
certificate; a closed port, a server that never answers, a server that does not speak TLS and a
server limited to TLS 1.1 where the test platform can still offer it; the input validation; the
classification boundaries (`N - 1`, `N`, `0`, negative); and the network guard with its positive
control. A name that does not resolve is tested without a real DNS lookup.
