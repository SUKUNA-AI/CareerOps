# careerops-matching-core

`careerops-matching-core` is the standalone C++20 deterministic compute service for
Processing v2. It is deployed on `core` next to `careerops-processing`, not on `edge`.

The service is intentionally stateless. It never owns PostgreSQL, S3, Spark, Jina,
job leases, retries, artifact publication or HH/application side effects. Python
Processing owns orchestration and sends typed batches after semantic reranking.

## Transport

The production service boundary is gRPC + Protobuf. The permanent control protocol lives at:

`proto/careerops/matching_core/v1/control.proto`

P2-01 implements only `Health` and `GetCapabilities`. The final evaluation protocol is
added only after Requirement/ResumeEvidence and post-Jina qualification inputs are frozen;
this avoids publishing a fake first-generation wire contract that would immediately become
legacy.

Expected future compute ownership includes:

- deterministic role/technology feature processing;
- experience interval compatibility;
- requirement-group evaluation;
- evidence aggregation and conflict handling;
- support bounds and mandatory coverage;
- deterministic scoring/policy fan-out;
- batched vacancy × resume evaluation.

The service must remain pure compute: request in, deterministic response out.

## Network boundary

The default listen address is `127.0.0.1:50051`. Both Processing and matching-core use host
networking on `core`, so the Python container reaches gRPC over host loopback. Do not bind the
service to the LAN without separately designing transport authentication/TLS.

Override only when necessary:

```bash
CAREEROPS_MATCHING_CORE_LISTEN_ADDR=127.0.0.1:50051
```

## Build

```bash
docker compose -f infra/compose/matching-core/compose.yml build
```

The Docker build uses a Debian 13 build stage and copies only the binary plus its resolved
runtime shared libraries into the final Debian 13 image.
