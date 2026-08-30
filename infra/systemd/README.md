# CareerOPS HH scheduler

The scheduler deliberately separates local timing from HH network activity.
The dispatcher timer wakes locally every five minutes, but it calls HH only when
one generated plan slot is due.

Default invariants:

- 150 submitted applications maximum per local day;
- 7-8 planned runs;
- never more than 25 submissions per run;
- runs spread across 08:30-23:00 Europe/Moscow with at least 80 minutes between slots;
- unused quota may be carried forward, but a later run is still capped at 25;
- a captcha pauses the remaining day; no automatic catch-up;
- if there are not enough relevant vacancies, the day finishes below 150 rather than sending noise.

`careerops_scheduler.planner` writes the daily plan locally and mirrors it to S3.
`careerops_scheduler.dispatcher` executes at most one due slot and mirrors dispatch
results to S3.

The HH worker generates a short vacancy-specific cover letter by default. Pass
`--letter-file` manually only when a fixed letter is desired.
