#!/usr/bin/env bash
set -euo pipefail

export FG_HOST="${FG_HOST:-127.0.0.1}"
export FG_PORT="${FG_PORT:-8000}"

# Local state directory (sqlite + artifacts)
export FG_STATE_DIR="${FG_STATE_DIR:-$PWD/state}"
mkdir -p "$FG_STATE_DIR"

# Explicit sqlite path
export FG_SQLITE_PATH="${FG_SQLITE_PATH:-$FG_STATE_DIR/frostgate.db}"

# Demo auth defaults (tweak if you want auth off during local dev)
export FG_AUTH_ENABLED="${FG_AUTH_ENABLED:-true}"
export FG_API_KEY="${FG_API_KEY:-supersecret}"

echo "[dev_env_local] FG_HOST=$FG_HOST"
echo "[dev_env_local] FG_PORT=$FG_PORT"
echo "[dev_env_local] FG_STATE_DIR=$FG_STATE_DIR"
echo "[dev_env_local] FG_SQLITE_PATH=$FG_SQLITE_PATH"
echo "[dev_env_local] FG_AUTH_ENABLED=$FG_AUTH_ENABLED"
