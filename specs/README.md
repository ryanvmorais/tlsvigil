# Specs

TLSVigil is built with spec-driven development. Each feature is designed as a spec under
`specs/NNN-name/` before implementation. Specs are plain Markdown and stay readable without any tool.

## Conventions

- **Numbering:** `NNN` sequential, three digits (`001`, `002`, …), in the order specs are opened.
  The name is short and in English (`001-foundation`).
- **Files:** each spec has `requirements.md` (what and why), `design.md` (how) and `tasks.md`
  (an executable breakdown). Each one carries the same frontmatter: `feature`, `status`, `date`,
  `related`, `origin`.
- **Phases and gates:** `requirements → design → tasks → implementation`. Every phase ends with
  explicit human approval before the next one starts.
- **Valid statuses:** `planned` → `draft` → `approved` → `in progress` → `done`. (`planned` is a
  roadmap entry without spec files yet. `origin: conception` for specs written before the
  implementation; `reverse-engineering` only to document something already finished.)
- **Acceptance criteria:** Given / When / Then, one bullet per scenario.
- **Traceability:** requirements are `RF-NN` (functional) and `RNF-NN` (non-functional). Each task
  and each design decision cites the requirement it satisfies.
- **Language:** specs are in English.

## How a spec is tested

1. **Logic is unit-tested.** The days left, the threshold, the RDAP parsing and the issue text are
   tested by calling the function with built inputs, not through the whole CLI.
2. **Tests never reach the network.** A certificate check runs against a local TLS server on
   `127.0.0.1` with a certificate made in the test; an RDAP answer is a recorded document served
   locally. No test talks to a real host, a real registry or the GitHub API.
3. **Hostile input is tested**, not assumed: every defense (a redirect to another host, an
   oversized RDAP answer, markup in a certificate field) has at least one test that feeds it the
   attack and expects a refusal.
4. **The CLI is tested through `main(argv)`** and `capsys`, and the exit codes are asserted.
5. **Every new test is seen failing once** before it is trusted: the line it guards is broken on
   purpose, the test goes red, and the change is undone. A test that asserts something does not
   happen also checks that the path ran.
6. **The quality gate is the last task of every stage**, with its real result recorded.

## Workflow

```text
requirements → design → tasks → implementation
```

No phase starts without the approval of the previous one. The `status:` in the frontmatter of each
file says where the spec is.

- `requirements.md`: opened as a `draft`; says what and why.
- `design.md`: written once the requirements are `approved`; says how.
- `tasks.md`: written once the design is `approved`; the tasks run one by one, and each stage
  records the real result of its quality gate.
- The status of every spec is in the [Roadmap](#roadmap) table below.

## Roadmap

Delivery is the milestone of the build (`v0.x`). No milestone is published: the first release is
`1.0.0`. A spec still to be built has no milestone yet (`—`).

| Spec | Scope | Delivery | Status |
|---|---|---|---|
| [`001-foundation`](001-foundation/) | package skeleton, `tlsvigil --version`, the local quality gate, CI, supply-chain hygiene, the documentation guards | `v0.1` | **done** |
| [`002-certificate`](002-certificate/) | the TLS check of `host:port`: verified handshake, days left, a failed verification as a finding; the offline test server | `v0.2` | **done** |
| [`003-rdap`](003-rdap/) | the domain expiry over RDAP: the IANA bootstrap, the `expiration` event, bounded HTTPS requests; the offline HTTP server | `v0.3` | **done** |
| `004-config-and-output` | the TOML configuration, the text and `--json` output, `--lang en\|pt-BR` and the exit codes | — | planned |
| `005-notifications` | one GitHub issue that is opened, updated and closed; Telegram and SMTP as optional channels; no duplicate warnings | — | planned |
| `006-action` | the composite Action in `action.yml` and the template repository with the daily workflow | — | planned |

## Maintenance notes

- **Zero runtime dependencies** crosses every spec: a feature that seems to need a package is
  redesigned on the standard library, or the decision is reopened in a new ADR.
- **The network reaches only what the configuration names** (specs `002`, `003`, `005`): hosts the
  user listed, the RDAP server the IANA bootstrap names, the GitHub API of the user's repository and
  the optional channels the user turned on.
- **The two READMEs move together**: a spec that adds a check, an option or an output updates
  `README.md` and `README.pt-BR.md` in the same PR (spec `001`, RF-08).
