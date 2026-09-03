#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="${CAREEROPS_ROOT:-/srv/careerops/app}"
RUN_USER="${CAREEROPS_RUN_USER:-sukuna}"
RUN_GROUP="${CAREEROPS_RUN_GROUP:-sukuna}"
ACCOUNTS_SOURCE="${1:-$REPO_ROOT/config/hh_accounts.example.toml}"

if [[ ! -f "$ACCOUNTS_SOURCE" ]]; then
  echo "accounts config source not found: $ACCOUNTS_SOURCE" >&2
  exit 2
fi

install -d -o "$RUN_USER" -g "$RUN_GROUP" -m 0750 /var/lib/careerops/hh
install -d -o root -g "$RUN_GROUP" -m 0750 /etc/careerops/hh

if [[ ! -f /etc/careerops/hh/accounts.toml ]]; then
  install -o root -g "$RUN_GROUP" -m 0640 \
    "$ACCOUNTS_SOURCE" /etc/careerops/hh/accounts.toml
  echo "Installed account template. Replace every REPLACE_ME_* binding before use."
fi

install -o root -g "$RUN_GROUP" -m 0640 \
  "$REPO_ROOT/infra/systemd/scheduler.env.example" \
  /etc/careerops/hh/scheduler.env

install -m 0644 "$REPO_ROOT/infra/systemd/careerops-hh-planner.service" /etc/systemd/system/
install -m 0644 "$REPO_ROOT/infra/systemd/careerops-hh-planner.timer" /etc/systemd/system/
install -m 0644 "$REPO_ROOT/infra/systemd/careerops-hh-dispatcher.service" /etc/systemd/system/
install -m 0644 "$REPO_ROOT/infra/systemd/careerops-hh-dispatcher.timer" /etc/systemd/system/

systemctl daemon-reload
systemctl enable --now careerops-hh-planner.timer
systemctl enable careerops-hh-dispatcher.timer
systemctl start careerops-hh-planner.service

echo
echo "CareerOPS HH scheduler installed in OBSERVE mode."
echo "Plan: /var/lib/careerops/hh/plan-$(date +%F).json"
echo "Dispatcher is installed but NOT started yet."
echo "Review accounts.toml and the plan before starting the dispatcher timer."
