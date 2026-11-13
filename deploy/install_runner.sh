#!/usr/bin/env bash
# Runs ON the Spark as user1. Installs a GitHub Actions self-hosted runner as a systemd *user*
# service (no sudo needed), so the deploy workflow can run here.
# The registration token is read from stdin so it never lands in a file or the shell history:
#   gh api -X POST repos/OWNER/REPO/actions/runners/registration-token --jq .token \
#     | ssh user1@spark1.local 'read -r REG_TOKEN; export REG_TOKEN; bash trialmatch/deploy/install_runner.sh'
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/JollyPerson/nvidia-hack-for-impact-2026-sheffield}"
VERSION="${RUNNER_VERSION:-2.338.2}"
DIR="$HOME/actions-runner"

mkdir -p "$DIR" && cd "$DIR"
if [[ ! -x ./config.sh ]]; then
  curl -fsSL -o runner.tgz \
    "https://github.com/actions/runner/releases/download/v${VERSION}/actions-runner-linux-arm64-${VERSION}.tar.gz"
  tar xzf runner.tgz && rm runner.tgz
fi
if [[ ! -f .runner ]]; then
  TOKEN="${REG_TOKEN:?REG_TOKEN not set (needed only for first registration)}"
  ./config.sh --unattended --replace --url "$REPO_URL" --token "$TOKEN" \
    --name spark1 --labels spark,trialmatch --work _work
fi

mkdir -p "$HOME/.config/systemd/user"
cat > "$HOME/.config/systemd/user/actions-runner.service" <<EOF
[Unit]
Description=GitHub Actions runner (TrialMatch deploys)
After=network-online.target

[Service]
WorkingDirectory=$DIR
# sg: the user systemd manager may predate user1 joining the docker group, so ask for it explicitly.
ExecStart=/usr/bin/sg docker -c $DIR/run.sh
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
EOF
systemctl --user daemon-reload
systemctl --user enable actions-runner.service
systemctl --user restart actions-runner.service
# Keep user services running without an open login, and start them at boot.
loginctl enable-linger "$USER" || echo "WARN: could not enable linger; runner stops when user1 logs out"
loginctl show-user "$USER" -p Linger
sleep 5
systemctl --user --no-pager status actions-runner.service | head -8
