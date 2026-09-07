# careerops-processing container

This is the standalone Processing v2 orchestration service deployed on `core`.
It is intentionally separate from Spark, the Jina reranker and the deterministic
C++ matching core.

Ownership:

- PostgreSQL job/current-state orchestration;
- S3 normalized/artifact reads and writes;
- requirement/evidence workflow in later P2 stages;
- calls the reranker on `edge`;
- calls `careerops-matching-core` on core loopback;
- publishes match results and application candidates.

It does not own Spark normalization, HH transport, Jina model execution or native
matching mathematics.

The container uses host networking so it can reach both the direct edge/core LAN and
the matching core bound to `127.0.0.1:50051`. Its own liveness endpoint defaults to
`127.0.0.1:18081/healthz`, so it is not exposed to the LAN.

Runtime configuration lives in `/etc/careerops/processing/env`; the repository contains
only `env.example`. S3 credentials use standard AWS environment variables and are never
part of Processing manifests or logs.

P2-01 exposes a permanent service shell and liveness surface. `/readyz` remains 503 until
P2-02 attaches the real queue worker lifecycle; this prevents deployment tooling from
mistaking a contract-only process for a functioning Processing worker.

Build/check:

```bash
docker compose -f infra/compose/processing/compose.yml build
python -m careerops_processing check-config
```
