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

mkdir -p "$ROOT/ui" "$ROOT/deploy" "$ROOT/appdata"  # appdata: the web app's app.db, kept across deploys
rsync -a --delete "$HERE/" "$ROOT/deploy/"
if [[ -f "$SRC/app/app.py" || -f "$SRC/api/main.py" || -f "$SRC/Dockerfile" ]]; then
  rsync -a --delete --exclude .git --exclude __pycache__ --exclude 'data/*.db' "$SRC/" "$ROOT/ui/"
  (cd "$SRC" && git log -1 --format='%h %s' 2>/dev/null || true) > "$ROOT/ui/DEPLOYED_VERSION"
else
  echo "No app in $SRC; keeping the code already in $ROOT/ui"
fi

# Feature flags for the web app's extras (api/README.md): the committed deploy/features.env first,
# then an optional Spark-only override in ~/trialmatch/features.env (outside the folders rsync replaces).
for env_file in "$ROOT/deploy/features.env" "$ROOT/features.env"; do
  if [[ -f "$env_file" ]]; then set -a; . "$env_file"; set +a; echo "Loaded feature flags from $env_file"; fi
done

# Pick what to run from the deployed code.
profiles=()
[[ -f "$ROOT/ui/app/app.py" ]] && profiles+=(streamlit)
for f in Dockerfile api/Dockerfile web/Dockerfile; do
  if [[ -f "$ROOT/ui/$f" ]]; then export WEB_DOCKERFILE="$f"; profiles+=(web); break; fi
done
export COMPOSE_PROFILES="$(IFS=,; echo "${profiles[*]:-}")"
echo "Compose profiles: ${COMPOSE_PROFILES:-none (LLM server only)}"
# Stop app containers whose code is no longer there.
[[ " ${profiles[*]:-} " == *" streamlit "* ]] || docker rm -f trialmatch-ui >/dev/null 2>&1 || true
[[ " ${profiles[*]:-} " == *" web "* ]] || docker rm -f trialmatch-web >/dev/null 2>&1 || true

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
echo "LLM API: http://spark1.local:11434/v1"
check() {  # name port
  for i in $(seq 1 45); do curl -fs -o /dev/null "http://$LANIP:$2/" && break; sleep 2; done
  echo "$1: http://spark1.local:$2  (HTTP $(curl -s -o /dev/null -w '%{http_code}' "http://$LANIP:$2/"))"
}
[[ " ${profiles[*]:-} " == *" streamlit "* ]] && check "Streamlit UI" 8501
[[ " ${profiles[*]:-} " == *" web "* ]] && check "Web app (FastAPI)" 8000
true
