# CareerOPS

**Status: FROZEN / PARKED.**

CareerOPS is a self-hosted job-search data and matching platform built around immutable source data, reproducible normalization, deterministic matching, a local reranker, and guarded application submission.

The project is intentionally parked. Nothing in the canonical runtime collects vacancies or submits applications unless an operator explicitly selects the corresponding Compose profile and command. Kubernetes, Airflow, Kafka, ClickHouse, CDC, streaming schedulers and background ingestion are not required to restore or demonstrate the system.

## Architecture

```text
                    explicit operator command
                             |
                             v
HH --------------------> HH Adapter
                             |
                             v
                    SeaweedFS / careerops-raw
                             |
                   manual PySpark batch
                             |
                             v
                        Normalizer
                   /         |         \
                  v          v          v
       normalized JSON   Parquet     PostgreSQL
       careerops-lake    silver      current refs
                  \          |          /
                   +---------+---------+
                             |
                             v
                         Processing
                    /        |        \
                   v         v         v
           matching-core    Jina   deterministic
              C++/gRPC    reranker    policy
                   \         |         /
                    +--------+--------+
                             |
               SKIP / REVIEW / APPLICATION_CANDIDATE
                             |
                             v
                    Application Owner
                             |
                 explicit external-write gate
                             |
                             v
                            HH
```

The decision unit is `vacancy × resume`. Processing never owns HH transport. The HH adapter never owns matching or scoring.

## Safety model

Freeze v1 is fail-closed:

- plain `docker compose up` starts no CareerOPS service because every service is profile-gated;
- every service in the frozen stack has `restart: "no"`;
- HH ingestion is a one-shot `seed` / `work` CLI, with no CareerOPS cron or systemd scheduler;
- the normalizer is a one-shot batch container;
- Application Owner is behind the `apply` profile **and** requires `CAREEROPS_APPLICATION_ALLOW_EXTERNAL_WRITES=true`;
- the default application setting is `false`;
- no Kubernetes component is required by the runtime.

If an old machine still has historical `careerops-hh-planner.timer`, `careerops-hh-dispatcher.timer` or `careerops-hh-materializer.timer` units installed from an earlier generation, disable them once before treating that host as parked:

```bash
sudo systemctl disable --now \
  careerops-hh-planner.timer \
  careerops-hh-dispatcher.timer \
  careerops-hh-materializer.timer 2>/dev/null || true
```

The vendored `hh-applicant-tool` is used as a Python transport/auth dependency only. Its old standalone Docker/cron deployment files are not part of Freeze v1.

## Frozen Compose stack

Canonical file:

```text
infra/compose/stack/compose.yml
```

Profiles:

| Profile | Services | Purpose |
|---|---|---|
| `state` | PostgreSQL, SeaweedFS master/volume/filer/S3 | persistent operational + object state |
| `admin` | `migrate` | Alembic schema administration |
| `collect` | `hh-adapter` | explicit one-shot HH ingestion |
| `normalize` | `normalizer` | explicit PySpark batch |
| `decision` | `matching-core`, `reranker`, `processing` | matching and candidate production |
| `apply` | `application-owner` | guarded external application submission |

For the examples below:

```bash
COMPOSE='docker compose -f infra/compose/stack/compose.yml'
```

## Prerequisites

Required for the complete stack:

- Docker Engine + Docker Compose;
- persistent directories under `/srv/careerops`;
- service configuration under `/etc/careerops`;
- NVIDIA Container Toolkit only when running the GPU reranker;
- a valid HH runtime profile only when running `collect` or `apply`.

Create service configuration from the checked-in examples and replace placeholder credentials:

```bash
sudo install -d \
  /etc/careerops/postgres \
  /etc/careerops/seaweedfs \
  /etc/careerops/hh-adapter \
  /etc/careerops/normalizer \
  /etc/careerops/processing \
  /etc/careerops/reranker \
  /etc/careerops/application-owner

sudo cp infra/compose/postgres/env.example /etc/careerops/postgres/env
sudo cp infra/compose/seaweedfs/s3.example.json /etc/careerops/seaweedfs/s3.json
sudo cp infra/compose/hh-adapter/env.example /etc/careerops/hh-adapter/env
sudo cp infra/compose/normalizer/env.example /etc/careerops/normalizer/env
sudo cp infra/compose/processing/env.example /etc/careerops/processing/env
sudo cp infra/compose/reranker/env.example /etc/careerops/reranker/env
sudo cp infra/compose/application-owner/env.example /etc/careerops/application-owner/env
```

HH account definitions live separately under `/etc/careerops/hh`, and the persistent upstream HH session/profile is mounted from `/etc/careerops/hh-applicant-tool`.

## Start storage

Create the persistent directories once:

```bash
sudo mkdir -p \
  /srv/careerops/postgres \
  /srv/careerops/seaweedfs/master \
  /srv/careerops/seaweedfs/volume \
  /srv/careerops/seaweedfs/filer
```

Start only state services:

```bash
$COMPOSE --profile state up -d
```

Apply database migrations explicitly:

```bash
$COMPOSE --profile admin run --rm migrate upgrade head
```

Nothing in these commands contacts HH.

## Run HH ingestion

There is no scheduler. Both operations are explicit one-shot commands.

Seed persistent source work:

```bash
$COMPOSE --profile collect run --rm hh-adapter \
  seed --account <account-key> --pages 1
```

Execute bounded work:

```bash
$COMPOSE --profile collect run --rm hh-adapter \
  work --account <account-key> --limit 25
```

The adapter publishes immutable source payloads to `careerops-raw`. It does not decide vacancy suitability and it does not own application policy.

## Run the normalizer

The normalizer is a manual PySpark batch:

```bash
$COMPOSE --profile normalize run --rm normalizer
```

Optional bounded run:

```bash
$COMPOSE --profile normalize run --rm normalizer --limit 100
```

A successful batch materializes three outputs:

```text
careerops-raw
    ↓
PySpark normalization / dedup / DQ
    ├── careerops-lake/normalized/.../*.json
    ├── careerops-lake/silver/<entity>/.../*.parquet
    └── PostgreSQL current references
```

Parquet batches are content-addressed. Replaying the exact same normalized input set reuses the existing Parquet manifest instead of creating a second logical snapshot.

To validate object output without touching PostgreSQL:

```bash
$COMPOSE --profile normalize run --rm normalizer --no-postgres
```

## Run Processing

Start the decision plane explicitly:

```bash
$COMPOSE --profile decision up -d matching-core reranker processing
```

Processing consumes normalized vacancy/resume versions, uses the deterministic core plus Jina evidence reranking, and publishes reproducible decisions:

```text
SKIP
REVIEW
APPLICATION_CANDIDATE
```

It does not submit applications.

Stop the decision plane without touching storage:

```bash
$COMPOSE --profile decision stop processing reranker matching-core
```

## Enable applications

Applications are deliberately harder to enable than to disable.

Default configuration:

```text
CAREEROPS_APPLICATION_ALLOW_EXTERNAL_WRITES=false
```

Starting Application Owner while the flag is false fails closed.

For an intentional live application session:

1. inspect the current application candidates and HH profile;
2. set the following in `/etc/careerops/application-owner/env`:

```text
CAREEROPS_APPLICATION_ALLOW_EXTERNAL_WRITES=true
```

3. start the owner explicitly:

```bash
$COMPOSE --profile apply up application-owner
```

Use foreground mode for deliberate live sessions so external writes remain visible. To park applications again:

```bash
$COMPOSE --profile apply stop application-owner
```

Then restore:

```text
CAREEROPS_APPLICATION_ALLOW_EXTERNAL_WRITES=false
```

## Stop everything

Because Freeze v1 uses no restart policies, stopped containers stay stopped after Docker or host restart.

```bash
$COMPOSE \
  --profile state \
  --profile admin \
  --profile collect \
  --profile normalize \
  --profile decision \
  --profile apply \
  down
```

This removes containers and the Compose network. Persistent PostgreSQL and SeaweedFS data under `/srv/careerops` are not deleted.

## Cold resurrection

With the repository, persistent data and `/etc/careerops` configuration restored, the system does not require a scheduler, Kubernetes cluster or external orchestration layer.

The minimal resurrection path is:

```bash
$COMPOSE --profile state up -d
$COMPOSE --profile admin run --rm migrate upgrade head
$COMPOSE --profile decision up -d matching-core reranker processing
```

Ingestion and application submission remain off until separately invoked.

## CI release gates

Freeze v1 has one CI workflow and only these release gates:

1. Ruff + mypy;
2. Python tests on supported Python versions;
3. PostgreSQL integration;
4. SeaweedFS integration;
5. C++ matching-core build;
6. Python ↔ C++ gRPC contract;
7. normalizer contract, image build and Spark Parquet smoke.

Calibration experiments, live Jina E2E, Kubernetes checks and historical architecture experiments are not release gates for the frozen artifact.

## Repository map

```text
src/careerops_adapter/                 HH source adapter
src/careerops_processing/              Processing v2 orchestration/contracts
src/careerops_application/             Application Owner
services/careerops-matching-core/       C++20 + gRPC deterministic core
services/careerops-normalizer-spark/    PySpark batch normalizer
infra/compose/stack/                    canonical parked Compose stack
infra/compose/*/env.example             runtime configuration examples
alembic/                                PostgreSQL schema migrations
proto/                                  gRPC contracts
tests/                                  unit/contract/integration tests
docs/                                   architecture history and reference material
hh-applicant-tool/                      vendored HH transport/auth dependency
```

## Explicit non-goals of Freeze v1

The frozen runtime does **not** require or automatically start:

- Airflow;
- Kafka;
- ClickHouse;
- Flink;
- CDC;
- Kubernetes;
- cron/systemd ingestion schedulers;
- continuous production calibration.

Historical documents and experiments can stay in the repository because they explain how the architecture evolved. They are not runtime dependencies and they are not a promise of future implementation.

## Final state

```text
architecture preserved
contracts preserved
containers reproducible
RAW preserved
normalized JSON + Parquet reproducible
PostgreSQL current state reproducible
matching reproducible
ingestion OFF by default
applications OFF by default
no mandatory running server
no mandatory Kubernetes
```

CareerOPS is meant to be resurrectable, not permanently hungry.
