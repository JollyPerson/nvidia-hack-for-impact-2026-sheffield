# Deploying TrialMatch on the DGX Spark

Two containers, managed by `docker-compose.yml`, run alongside the organiser's `spark-hack-ollama`
container, which is left untouched:

| Container | What | URL on the venue LAN |
|---|---|---|
| `trialmatch-ollama` | Ollama 0.40.16 (organiser's image, models mounted read-only). Keeps the screener `nemotron-3.5-lightning:30b` and the verifier `qwen3.6:35b-a3b` loaded. 8 parallel requests, 16k context. | `http://spark1.local:11434/v1` (OpenAI-compatible, any API key) |
| `trialmatch-ui` | The Streamlit app from `app/app.py`. Code is bind-mounted from `~/trialmatch/ui`, so a redeploy needs no rebuild. | `http://spark1.local:8501` |

Both restart automatically (`restart: unless-stopped`).

## Deploy

- **Automatically:** `.github/workflows/deploy-spark.yml` runs on the self-hosted runner `spark1` for each push to
  `master`. To deploy another branch, use "Run workflow" in the Actions tab and give it the branch name.
- **By hand, on the Spark:** `bash deploy/deploy.sh [path-to-checkout]`

`deploy.sh` copies the app into `~/trialmatch/ui` (only when the source has `app/app.py`), runs
`docker compose up -d --build`, unloads any model other than the two AGENTS.md roles, and preloads them.

## Runner

`install_runner.sh` installs the Actions runner as a systemd user service for `user1`, with no sudo needed. It reads a
registration token on stdin (see the header of the script).

## Useful commands (on the Spark)

```
docker compose -f ~/trialmatch/deploy/docker-compose.yml ps
docker logs -f trialmatch-ui
docker exec trialmatch-ollama ollama ps
```
