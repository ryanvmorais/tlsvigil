# Security Policy

## Supported Versions

Only the latest release receives security fixes. A fix comes out as a patch release.

| Version | Supported |
|---|---|
| latest release (from `1.0.0`) | yes |
| older releases | no |

## Reporting a Vulnerability

Please report security issues in TLSVigil itself **privately**, through GitHub Security
Advisories: open the repository's **Security and quality** tab and choose **Report a vulnerability**.
Do not open a public issue or pull request for it.

Include what is needed to reproduce it: the TLSVigil version (`tlsvigil --version`) or commit,
the command, configuration or workflow, what you expected and what happened, and the impact you
see. Reproduce it against a host or a test server you run yourself; never include a real token or
password in the report.

We aim to **acknowledge a report within 7 days** and to send a first assessment within 14.

## Disclosure Policy

We follow coordinated disclosure. A fix is prepared privately, released, and then the advisory is
published; the target is **90 days** from the report at most, sooner when a fix is ready. We credit
the reporter in the advisory unless asked not to.

## Authorized use only

TLSVigil is a passive monitor: it opens a TLS connection to a host the way a browser does, reads
the certificate, and asks the public registration service (RDAP) when a domain expires. It sends
no attack payload. Point it at hosts and domains you operate or are responsible for, and keep the
schedule modest: the registration services are shared public infrastructure.

The authors accept no liability for misuse of this tool.

## Scope

**Report here** (a vulnerability in TLSVigil itself): anything that lets a crafted input turn the
tool against its user or a third party, or that breaks one of its safety guarantees. For example:

- a connection to a host, a port or a URL that is neither in the configuration nor an RDAP server
  named by the IANA bootstrap (for example, through a redirect or a crafted RDAP response);
- an RDAP request that is not HTTPS, or a response read without a bound on its size or its time;
- a certificate reported as valid although its verification failed;
- a token or a password (the GitHub token, a Telegram bot token, an SMTP password) reaching an
  output, a log line, an issue, a notification or an error message;
- text from a certificate, an RDAP response or a configuration file reaching an issue or a
  notification in a form that is rendered as markup, a link or a mention instead of plain text;
- a configuration file that makes the tool run code or write outside the files it was given;
- in the GitHub Action: an input reaching a shell command as code (script injection), or the
  Action needing a permission beyond the ones its documentation lists.

**Not a vulnerability in TLSVigil**: a certificate or a domain that is about to expire (that is the
tool doing its job, and the owner of the host is the one to act), a wrong date or a missed warning
(open a regular bug report instead), and a top-level domain whose registry publishes no expiry
date over RDAP.
