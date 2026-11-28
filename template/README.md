# TLSVigil template

A daily check of your TLS certificates and domain registrations, run by GitHub Actions in your own
repository, that keeps one issue open while something is close to expiring.

**Language:** English · [Português (Brasil)](README.pt-BR.md)

Every day the workflow runs [TLSVigil](https://github.com/ryanvmorais/tlsvigil) on the hosts and
domains of [tlsvigil.toml](tlsvigil.toml). The certificate date comes from a verified TLS handshake;
the domain date from RDAP, the registries' public API. When one of them needs attention, the run
opens an issue in this repository and comments on it as the days count down; when all is fine
again, it closes the issue. No server, no account and no secret to start: the issue is written
with the workflow's own token.

## Set up in four steps

1. **Create your repository.** Click **Use this template** → **Create a new repository**. Public or
   private both work.
2. **Name what to check.** Edit `tlsvigil.toml`: replace `example.com` with your hosts and domains,
   and commit to `main`. The commit starts a check right away; open the **Actions** tab to see it,
   and its summary for the result.
3. **Watch the repository.** Click **Watch** → **All Activity**. The issue is opened by
   `github-actions[bot]`, and GitHub sends its e-mails and notifications only to those who watch
   the repository; "All Activity" also brings the pull requests of a new TLSVigil release. In a
   busy repository, **Custom** → **Issues** is quieter.
4. **Optional: Telegram or e-mail.** Add the channel to `channels` in `tlsvigil.toml`, add its
   secrets under **Settings** → **Secrets and variables** → **Actions**, and uncomment its lines in
   `.github/workflows/tlsvigil.yml`.

## What the colour of a run means

| Run | Meaning |
|---|---|
| Green | The checks ran and every notification was delivered; a warning, a host that could not be reached among them, is in the issue. |
| Red | Nothing could be checked (a mistake in `tlsvigil.toml`, for one), a notification was not delivered, or TLSVigil hit a bug; the run's summary says which. |

A certificate about to expire is an issue, not a red run, so a red run always means the check
itself needs you.

## Limits of GitHub Actions

- A scheduled run can start late, and under heavy load GitHub may drop it; the next day's run
  catches up.
- In a public repository, GitHub turns the schedule off after 60 days with no activity; turn it
  back on in the **Actions** tab.
- Create the repository from the template, not as a fork: a fork starts with its scheduled
  workflows off, and secrets do not reach it.
- In a private repository, each run spends minutes of your plan's Actions quota; a check takes
  about a minute.

## More

Every key of `tlsvigil.toml`, the inputs of the Action and the command line are documented in
the [TLSVigil repository](https://github.com/ryanvmorais/tlsvigil).
