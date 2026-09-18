#!/bin/bash
set -euo pipefail

echo "[$(date)] Running applicant session maintenance..."
/usr/local/bin/python -m hh_applicant_tool refresh-token
/usr/local/bin/python -m hh_applicant_tool update-resumes
echo "[$(date)] Applicant session maintenance finished."
