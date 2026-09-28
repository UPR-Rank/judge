# UPR Judge

Competitive programming judge built on **MOG (Matcom Online Grader)**, the
platform that runs the ICPC Caribbean Finals. This fork is a modernization of
MOG: same contest model, same submission semantics, same verdicts, but with
the judge refactored into testable modules and the grading moved onto a real
queue so capacity is no longer tied to a single polling process.

Upstream is [MatcomOnlineGrader/judge](https://github.com/MatcomOnlineGrader/judge).
The original README is preserved verbatim in
[README.matcomgrader.md](README.matcomgrader.md), and the MIT notice in
[LICENSE](LICENSE) is unchanged.

## What it does

A competitor submits source code for a problem. The judge compiles it,
runs it against every test case inside a sandbox, compares the output with
the answer using the problem's checker, and records a verdict with the time
and memory it used. Scores and standings come from those verdicts.

## How grading works

```
web request ──▶ PostgreSQL (submission saved as Pending)
                     │
                     └──▶ Redis: id of the submission to grade
                                    │
                             Celery worker claims it
                             (PostgreSQL row locked)
                                    │
                                    ▼
                          ┌───────────────────────┐
                          │  compile (own toolchain)│
                          │  run each test case    │  ← safeexec, no network,
                          │  compare with checker  │    unprivileged user
                          └───────────────────────┘
                                    │
                                    ▼
                          verdict + time + memory ──▶ PostgreSQL
```

**Redis is a hint; PostgreSQL is the truth.** A submission stays `Pending` in
the database until a worker claims it, so a lost message — or Redis being down
at the moment someone submits — costs a delay, not a submission. Messages are
sent on transaction commit, so a worker never receives an id it cannot find
yet.

**Claims are atomic.** A worker takes a submission with `SELECT … FOR UPDATE
NOWAIT` and flips it to `Compiling` in the same transaction, so a second
worker moves on instead of grading the same work twice.

**Tasks are idempotent.** A task claims by id and finds nothing to do when the
submission is no longer pending, which is what makes `acks_late` safe: a
worker that dies hands the message back, and the redelivery grades nothing.

**A reaper recovers dead workers.** `Submission.claimed_at` records when a
worker claimed a submission, which is what tells a slow worker from a dead
one. Claims older than the timeout go back to the queue, re-checked under a
row lock so a submission that finished meanwhile is not graded twice.

### The sandbox

Submissions are untrusted code, so:

- The worker container runs `grader-firewall.sh` before starting: iptables
  permits output from root (the worker itself, which needs Redis and
  PostgreSQL) and drops everything else.
- `safeexec` is setuid-root and drops privileges into an unprivileged `judge`
  user before running anything, and applies the time, memory and process
  limits.
- Each submission gets a fresh working folder that is removed afterwards.
- The submission runs with no network, as an unprivileged user.

## Architecture

| Service | Role |
| --- | --- |
| `api` | Django + Gunicorn: contests, problems, submissions, scoring |
| `worker` | Grader image; consumes the queue, compiles and judges |
| `beat` | Schedules the reaper |
| `redis` | Broker and result backend |
| `database` | PostgreSQL: submissions, contests, verdicts — the source of truth |
| `prometheus` / `grafana` | Production metrics |

The `judging` package holds the judge, split so each step can be tested on its
own:

| Module | Responsibility |
| --- | --- |
| `judging/sandbox.py` | Problem and submission folders |
| `judging/compilers.py` | Source file naming and compilation |
| `judging/checkers.py` | Compiling and invoking the answer checker |
| `judging/runner.py` | Building the safeexec command line, parsing its output |
| `judging/verdicts.py` | The safeexec-outcome → verdict table |
| `judging/state.py` | State transitions, claiming, stalled detection |
| `judging/service.py` | Grading one submission, retry policy |
| `judging/tasks.py` | The queue: enqueue, grade, reap |

`manage.py grader` is still there and shares the same work unit as the worker.
Because the claim is atomic, a worker and the command can run side by side
without grading anything twice — which also makes rolling back to it a matter
of changing the compose command.

## Stack

- Python 3.12 in the grader image, Django 5.2, PostgreSQL
- Celery 5.6 on Redis 7
- `safeexec` built from the [UPR-Rank/safeexec](https://github.com/UPR-Rank/safeexec)
  fork instead of upstream
- Toolchains: gcc 11.3.0, OpenJDK 17, Kotlin, PyPy, Mono, CPython 3.12

## Running it locally

```bash
./updev.sh
```

That generates `settings.ini` from the template, brings up PostgreSQL, Redis,
the API, a worker and beat. Then:

```bash
docker-compose -f docker/dev/docker-compose.yml exec api \
    /opt/environ/bin/python3 manage.py populate_local_dev
```

### Apple Silicon (arm64) Macs

The grader image is x86_64-only (it bundles a custom gcc 11.3.0 toolchain), so
it is built and run as `linux/amd64` under emulation. `updev.sh` registers
Homebrew's buildx automatically. **Use Rosetta, not QEMU** — QEMU crashes the
compiler:

```bash
colima start --vz-rosetta --cpu 4 --memory 6
```

Docker Desktop users should enable *Settings → General → Use Rosetta for
x86/amd64 emulation*.

## Tests

```bash
docker-compose -f docker/dev/docker-compose.yml exec api \
    /opt/environ/bin/python3 manage.py test tests
```

119 tests. Two of them (`tests/test_roles.py`) fail on a clean checkout and
are unrelated to the judge — they are left as found rather than papered over.
Lint is `flake8` and `black`; the CI workflows run both.

## Work in progress

The judge is being modernized in sprints, each on its own branch off `master`.

| Sprint | State |
| --- | --- |
| 0 — Independence | Done: build `safeexec` from the UPR-Rank fork, baseline tests |
| 1 — Judge modularization | Done: the 532-line command split into the `judging` package |
| 2 — Queue | Done: Celery on Redis, with the reaper |
| 3 — Shared cache | Next (not started) |
| 4 — Side effects | Planned |
| 5 — Web refactor | Planned |
| 6 — Observability and deployment | Planned |

### Known gaps

- **The build still depends on upstream for `gcc-11.3.0`.**
  `docker/common/dockerfile.grader` fetches the toolchain tarball from
  Matcom's release. A UPR-Rank fork of it has been verified to build, but
  publishing a prebuilt tarball it can be pointed at is still pending, so an
  upstream outage breaks the image build. The same is true, less urgently, of
  `testlib` and `runexe`.
- **`Pillow` is pinned to 9.5.0** in `requirements.txt`, which predates
  Django 5.2's requirement of Pillow >= 10.1. Unrelated to the judge, but it
  is wrong and the test image works around it rather than it being fixed here.
- **Two known judge defects are left in place** and pinned by tests: Kotlin
  receives `-J-Xmx` 1024× the memory limit in MB, and Java/Kotlin/C# runs
  without `--clock`, so they are not charged for time they do not use.

## Contributing

The judge is a sandbox that runs code from strangers. Changes to
`judging/runner.py`, `judging/state.py` or anything that touches the
firewall or `safeexec` deserve particular review.

## License

MIT, inherited from MOG. See [LICENSE](LICENSE).
