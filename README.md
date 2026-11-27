# TLSVigil

**English** · [Português (Brasil)](README.pt-BR.md)

![Python](https://img.shields.io/badge/Python-3.12+-3776AB?logo=python&logoColor=white)
![runtime deps](https://img.shields.io/badge/runtime%20deps-0-brightgreen)
[![CI](https://img.shields.io/github/actions/workflow/status/ryanvmorais/tlsvigil/ci.yml?label=CI&logo=githubactions&logoColor=white)](https://github.com/ryanvmorais/tlsvigil/actions/workflows/ci.yml)
![license](https://img.shields.io/badge/license-Apache%202.0-blue?logo=apache&logoColor=white)

**TLSVigil is a zero-dependency TLS certificate and domain expiry monitor: a command-line tool and
a GitHub Action that warn you, before it happens, that the certificate of your site or the
registration of your domain is about to expire.**

An expired certificate takes a site down with a browser warning that sends visitors away, and an
expired domain takes the site and its mail down together. Let's Encrypt stopped sending expiry
reminder e-mails in June 2025, and certificates are getting shorter lives every year, so the
renewal that fails silently is now the common case. TLSVigil checks each host and each domain once
a day and tells you while there is still time to act.

It is built for small businesses, site owners and freelancers who look after a few sites, and it
needs no server: it runs on a daily schedule in a GitHub repository of yours, on the free GitHub
Actions minutes, and opens an issue there when something needs attention.

> **Status:** in development, not released yet; the first release will be `1.0.0`. This README
> describes the tool as it is specified in [specs/](specs/). Until the first release, only
> `tlsvigil --version` runs.

## What it checks

TLSVigil answers one question for every name you give it: **how many days are left, and is that
enough?** A host or a domain is reported when it has fewer days left than your warning threshold,
and a certificate that fails verification is reported at once, whatever its date.

| Check | What it tells you | Reads | Network |
|---|---|---|---|
| Certificate | Days until the certificate of `host:port` expires, and whether it fails verification (expired, wrong name, untrusted chain) | The certificate the server presents in the TLS handshake | A TLS connection to each host you list |
| Domain | Days until the registration of the domain expires | The `expiration` event of the registry's RDAP record | HTTPS to the RDAP server the IANA bootstrap names for the top-level domain |

The result goes to one GitHub issue in your repository, through the `GITHUB_TOKEN` the workflow
already has: the issue is opened when something needs attention, updated on the next runs and
closed when everything is fine again, so a problem that lasts a week is one issue, not seven.
Telegram and e-mail (SMTP) are optional extra channels. From the command line, the same result is
printed as text or as JSON (`--json`), in English or Brazilian Portuguese (`--lang pt-BR`), with
an exit code a script can test.

### What you can rely on

- **Passive.** It opens a TLS connection the way a browser does and asks a public registration
  service. It sends no attack payload and logs in nowhere.
- **Zero runtime dependencies.** The package uses the Python standard library only, so what runs
  in your CI is the interpreter and this code.
- **Verified TLS only.** The certificate date is read only from a chain that passed verification;
  a chain that fails is the warning itself, never a silent pass.
- **Bounded requests.** Each RDAP request is HTTPS, to a server the IANA bootstrap names, with a
  limit on its time and on the size of the answer.
- **Your secrets stay secrets.** Tokens and passwords come from the CI's secret store and never
  reach an output, a log line or an issue.

## Quick start

Not published yet, so for now run it from a clone. You need [uv](https://docs.astral.sh/uv/).

```bash
# Clone the repository and install the environment
git clone https://github.com/ryanvmorais/tlsvigil.git
cd tlsvigil
uv sync

# Check that the command runs (the only command that exists so far)
uv run tlsvigil --version
```

## TLSVigil vs. other tools

| | TLSVigil | [Uptime Kuma](https://github.com/louislam/uptime-kuma) | [Red Sift Certificates Lite](https://redsift.com/) | [action-check-domain](https://github.com/codex-team/action-check-domain) |
|---|---|---|---|---|
| Shape | A CLI and a GitHub Action, standard library only | A self-hosted monitoring service | A hosted service | A GitHub Action |
| Where it runs | Your GitHub repository, or any machine with Python | A server you run | The vendor's cloud | Your GitHub repository |
| Certificate expiry | Yes | Yes | Yes, up to 250 certificates on the free plan | Yes |
| Domain expiry | Yes, over RDAP | Yes | No | Yes |
| How it warns | One issue that updates and closes itself; Telegram or e-mail if you want | Over 90 notification services | E-mail, 7 days before | Outputs for the next step; the example opens an issue with another Action |

_Comparison checked against the official pages in November 2026._

If you already run Uptime Kuma, keep it: it watches uptime too. TLSVigil is for when the expiry
dates are all you need and you would rather not keep a server running for them.

## Scope and limitations

TLSVigil is deliberately small. For each limit below, what to reach for instead.

- **Some top-level domains have no RDAP service.** The IANA bootstrap lists none for `.de`, `.io`,
  `.co`, `.eu`, `.cn`, `.ru` and `.jp`, among others, so the expiry of those domains is not
  checked; their certificates still are. *Instead:* turn on the renewal reminders and the
  automatic renewal of your registrar.
- **Some registries leave the date out.** RDAP does not require the `expiration` event, and a
  record without it gets no domain warning; the result says the date is unknown. *Instead:* the
  same as above.
- **A revoked certificate is not detected.** The check verifies the chain and the name and reads
  the expiry date, but does not ask the certificate authority (OCSP or CRL) whether the
  certificate was revoked before that date. *Instead:* a monitor that checks revocation, or the
  account page of your certificate authority.
- **A port that starts in plain text is not checked.** The check speaks TLS from the first byte,
  as on ports 443, 465 or 993; a server that starts in plain text and upgrades with STARTTLS (SMTP
  on 25 or 587, IMAP on 143, XMPP) ends as a TLS error. *Instead:* point it at the implicit-TLS
  port of the same server when it has one (465 for SMTP, 993 for IMAP).
- **It sees what the internet sees.** The check runs from a GitHub-hosted runner, so a host that
  is reachable only inside your network is not covered. *Instead:* run the command line on a
  machine inside that network, from `cron` or a scheduled task.
- **The schedule is best effort.** GitHub may start a scheduled workflow late, or skip a run when
  its runners are under heavy load. *Instead:* keep the warning threshold at several days, so one
  missed run never costs you the warning.
- **It is not an uptime monitor.** It does not tell you that a site is down, only that a
  certificate or a domain is about to expire. *Instead:*
  [Uptime Kuma](https://github.com/louislam/uptime-kuma) on a server of yours.

## Development

Python 3.12 is the floor (CI tests 3.12 and the newest stable release). The quality gate runs
before every commit through a versioned hook:

```bash
# Activate the pre-commit hook once per clone
git config core.hooksPath .githooks

# Lint (the hook and the CI run the same four steps, in this order)
uv run ruff check .

# Check the formatting
uv run black --check .

# Check the types (strict)
uv run mypy

# Run the tests with coverage
uv run pytest --cov
```

Each technology of the stack, and why it was chosen, is explained in [docs/stack.md](docs/stack.md).
Features are specified under [specs/](specs/) before they are built.

Contributions are welcome: see [CONTRIBUTING.md](CONTRIBUTING.md). To report a vulnerability in
TLSVigil itself, use the private channel described in [SECURITY.md](SECURITY.md), never a public
issue.

## License

[Apache License 2.0](LICENSE).
