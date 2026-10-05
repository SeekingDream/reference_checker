#!/usr/bin/env bash
# Starts the GROBID docker container used for reference extraction and waits
# until it reports healthy.
set -euo pipefail
cd "$(dirname "$0")/.."

echo "Starting GROBID (docker compose)..."
docker compose up -d

echo "Waiting for GROBID to become ready on http://localhost:8070 ..."
for i in $(seq 1 60); do
  if curl -sf http://localhost:8070/api/isalive >/dev/null 2>&1; then
    echo "GROBID is ready."
    exit 0
  fi
  sleep 2
done

echo "GROBID did not become ready in time. Check 'docker compose logs' for details." >&2
exit 1
