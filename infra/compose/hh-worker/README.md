# CareerOPS HH worker

This worker keeps `hh-applicant-tool` as the HH protocol/auth client and moves
application decisions into CareerOPS.

Pipeline:

1. Search HH vacancies through the upstream authenticated API client.
2. Fetch the full vacancy JSON.
3. Apply a strict ML/DS/AI title validator.
4. Persist vacancy + decision into SeaweedFS/S3.
5. For accepted normal HH vacancies, submit the application.
6. Re-fetch the vacancy and confirm `got_response`.
7. Persist application audit and batch outcome to S3.

The worker deliberately skips external `response_url` vacancies and HH tests.
Tests can continue to be handled by the upstream tool until its test flow is
adapted into the CareerOPS audit path.
