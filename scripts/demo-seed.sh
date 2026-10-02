#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$ROOT_DIR/.env.demo"
DEMO_DATABASE="finsight_demo"
SKIP_DATABASE=0
SKIP_MODEL_PULL=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --env-file) ENV_FILE="$2"; shift 2 ;;
    --database) DEMO_DATABASE="$2"; shift 2 ;;
    --skip-database) SKIP_DATABASE=1; shift ;;
    --skip-model-pull) SKIP_MODEL_PULL=1; shift ;;
    *) echo "Unknown argument: $1" >&2; exit 2 ;;
  esac
done

if [[ ! -f "$ENV_FILE" ]]; then
  echo "Missing $ENV_FILE. Copy .env.demo.example to .env.demo and set MySQL credentials first." >&2
  exit 2
fi
if [[ ! "$DEMO_DATABASE" =~ ^finsight_demo(_[a-zA-Z0-9_]+)?$ ]]; then
  echo "Public demo bootstrap requires isolated finsight_demo database" >&2
  exit 2
fi

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

if [[ -x "$ROOT_DIR/.venv/bin/python" ]]; then
  PYTHON="$ROOT_DIR/.venv/bin/python"
else
  PYTHON="${PYTHON:-python3}"
fi

export LLM_PROVIDER=ollama
export EMBEDDING_PROVIDER=bge_local
export DB_NAME="$DEMO_DATABASE"
export MYSQL_DATABASE="$DEMO_DATABASE"
export CHROMA_DB_PATH=data/demo_chroma_db

if [[ "$SKIP_MODEL_PULL" -eq 0 ]]; then
  MODEL="${OLLAMA_MODEL:-qwen3.5:9b-q4_K_M}"
  "$PYTHON" "$ROOT_DIR/scripts/ensure_ollama_model.py" --model "$MODEL"
fi

if [[ "$SKIP_DATABASE" -eq 0 ]]; then
  echo "Seeding isolated MySQL database $DEMO_DATABASE ..."
  "$PYTHON" "$ROOT_DIR/scripts/bootstrap_demo_v3.py" --env-file "$ENV_FILE"
fi

"$PYTHON" "$ROOT_DIR/scripts/ensure_demo_reader.py" --env-file "$ENV_FILE"
"$PYTHON" "$ROOT_DIR/scripts/runtime_env.py" --env-file "$ENV_FILE"

echo "Demo seed completed. Load .env.demo in each service terminal before starting the stack."
