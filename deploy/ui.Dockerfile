# TrialMatch web UI (Streamlit) for the DGX Spark.
# The app code is NOT baked in: it is bind-mounted at /srv/app from ~/trialmatch/ui on the Spark,
# so a sync from GitHub updates the running app without a rebuild.
FROM python:3.12-slim

RUN pip install --no-cache-dir "streamlit>=1.40" pandas openai pydantic requests

WORKDIR /srv/app
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

EXPOSE 8501
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
