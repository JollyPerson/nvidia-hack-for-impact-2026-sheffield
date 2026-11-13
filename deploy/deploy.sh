#!/usr/bin/env bash
# Runs ON the Spark (by the self-hosted GitHub runner, or by hand).
#   bash deploy/deploy.sh [SOURCE_DIR]
# Copies the app code in SOURCE_DIR (default: this repo checkout) to ~/trialmatch/ui and this
# script's deploy/ folder to ~/trialmatch/deploy, then brings the stack up.
# Leaves the organiser's spark-hack-ollama container and the vLLM image alone.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC="$(cd "${1:-$HERE/..}" && pwd)"
ROOT="$HOME/trialmatch"
export LANIP="${LANIP:-$(ip -4 -o addr show | awk '{print $4}' | cut -d/ -f1 | grep '^10\.41\.30\.' | head -1)}"
export APP_DIR="$ROOT/ui"
export MODELS_DIR="$HOME/spark-hackathon/ollama-models"
SCREENER=nemotron-3.5-lightning:30b
VERIFIER=qwen3.6:35b-a3b

mkdir -p "$ROOT/ui" "$ROOT/deploy"
rsync -a --delete "$HERE/" "$ROOT/deploy/"
if [[ -f "$SRC/app/app.py" ]]; then
  rsync -a --delete --exclude .git --exclude __pycache__ --exclude 'data/*.db' "$SRC/" "$ROOT/ui/"
  (cd "$SRC" && git log -1 --format='%h %s' 2>/dev/null || true) > "$ROOT/ui/DEPLOYED_VERSION"
else
  echo "No app/app.py in $SRC; keeping the UI code already in $ROOT/ui"
fi

# One-time move from the hand-started containers to compose-managed ones.
for c in trialmatch-ollama trialmatch-ui; do
  if docker container inspect "$c" >/dev/null 2>&1 && \
     [[ -z "$(docker inspect --format '{{index .Config.Labels "com.docker.compose.project"}}' "$c")" ]]; then
    docker rm -f "$c"
  fi
done

cd "$ROOT/deploy"
timeout 600 docker compose up -d --build --remove-orphans

# Keep only the two AGENTS.md models resident. Loads run with a timeout so nothing hangs.
for i in $(seq 1 60); do curl -fsS "http://$LANIP:11434/api/tags" >/dev/null 2>&1 && break; sleep 1; done
for m in $(docker exec trialmatch-ollama ollama ps | awk 'NR>1{print $1}'); do
  [[ "$m" == "$SCREENER" || "$m" == "$VERIFIER" ]] || docker exec trialmatch-ollama ollama stop "$m"
done
for m in "$SCREENER" "$VERIFIER"; do
  curl -s -m 300 "http://$LANIP:11434/api/generate" -d "{\"model\":\"$m\",\"keep_alive\":-1}" >/dev/null || echo "preload of $m timed out"
done

docker exec trialmatch-ollama ollama ps
docker compose ps
for i in $(seq 1 30); do curl -fsS -o /dev/null "http://$LANIP:8501/" && break; sleep 2; done
curl -s -o /dev/null -w "UI HTTP %{http_code}\n" "http://$LANIP:8501/"
echo "LLM API: http://spark1.local:11434/v1    Web UI: http://spark1.local:8501"
