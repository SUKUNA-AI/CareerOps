# CareerOPS Application Owner

P2-08 runtime. The service consumes only current `application_candidates.status = eligible`,
rechecks operational safety, owns durable application state and is the only CareerOPS runtime
allowed to call `HHApplicantTransport`.

The vendored `hh-applicant-tool` is installed inside the image only to provide applicant session,
authentication, resume, questionnaire and submission mechanics. It is not allowed to search,
filter, score or schedule applications.

HH profiles live under `/etc/careerops/hh-applicant-tool/<account_key>/config.json` on the host.
The directory is mounted read-write because the vendored client persists refreshed tokens and
cookies. The rest of the container filesystem is read-only.
