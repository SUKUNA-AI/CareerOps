#!/usr/bin/env bash
set -euo pipefail

RESUME_ID="${1:-}"
if [[ -z "$RESUME_ID" ]]; then
  echo "usage: sudo $0 <HH_RESUME_ID>" >&2
  exit 2
fi

REPO_ROOT="${CAREEROPS_ROOT:-/srv/careerops/app}"
RUN_USER="${CAREEROPS_RUN_USER:-sukuna}"
RUN_GROUP="${CAREEROPS_RUN_GROUP:-sukuna}"

install -d -o "$RUN_USER" -g "$RUN_GROUP" -m 0750 /var/lib/careerops/hh
install -d -o root -g "$RUN_GROUP" -m 0750 /etc/careerops/hh

cat > /etc/careerops/hh/scheduler.env <<ENV
CAREEROPS_HH_RESUME_ID=${RESUME_ID}
CAREEROPS_HH_PROFILE=careerops-ml
CAREEROPS_HH_TIMEZONE=Europe/Moscow
CAREEROPS_HH_DAILY_CAP=150
CAREEROPS_HH_MIN_RUNS=7
CAREEROPS_HH_MAX_RUNS=8
CAREEROPS_HH_MAX_PER_RUN=25
CAREEROPS_HH_MIN_PER_RUN=14
CAREEROPS_HH_WINDOW_START=08:30
CAREEROPS_HH_WINDOW_END=23:00
CAREEROPS_HH_MIN_GAP_MINUTES=80
CAREEROPS_HH_LATE_GRACE_MINUTES=75
CAREEROPS_HH_AREA=1
CAREEROPS_HH_PERIOD=14
CAREEROPS_HH_PAGES=3
CAREEROPS_HH_PER_PAGE=100
CAREEROPS_HH_STATE_DIR=/var/lib/careerops/hh
CAREEROPS_ROOT=${REPO_ROOT}
ENV
chown root:"$RUN_GROUP" /etc/careerops/hh/scheduler.env
chmod 0640 /etc/careerops/hh/scheduler.env

install -m 0644 "$REPO_ROOT/infra/systemd/careerops-hh-planner.service" /etc/systemd/system/
install -m 0644 "$REPO_ROOT/infra/systemd/careerops-hh-planner.timer" /etc/systemd/system/
install -m 0644 "$REPO_ROOT/infra/systemd/careerops-hh-dispatcher.service" /etc/systemd/system/
install -m 0644 "$REPO_ROOT/infra/systemd/careerops-hh-dispatcher.timer" /etc/systemd/system/

systemctl daemon-reload
systemctl enable --now careerops-hh-planner.timer
systemctl enable careerops-hh-dispatcher.timer
systemctl start careerops-hh-planner.service

echo
echo "CareerOPS HH scheduler installed."
echo "Plan: /var/lib/careerops/hh/plan-$(date +%F).json"
echo "Dispatcher is installed but NOT started yet."
echo "After reviewing the plan: systemctl start careerops-hh-dispatcher.timer"
echo "Timers: systemctl list-timers 'careerops-hh-*'"
