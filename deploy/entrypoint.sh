#!/bin/sh
# Install any extra packages the app lists (best effort, so it still starts offline), then run Streamlit.
cd /srv/app
if [ -f app/requirements.txt ]; then
  pip install --no-cache-dir -q -r app/requirements.txt || echo "pip install failed; starting with baked-in packages"
fi
exec streamlit run "${APP_ENTRY:-app/app.py}" \
  --server.address=0.0.0.0 \
  --server.port=8501 \
  --server.headless=true \
  --server.runOnSave=true \
  --server.fileWatcherType=poll \
  --browser.gatherUsageStats=false
