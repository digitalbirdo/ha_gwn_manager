#!/bin/sh
# Create and run a throw-away Home Assistant Core instance for developing this integration.
#
#   scripts/dev-instance.sh [setup|run] [DIR]
#
# setup  creates a Python venv with Home Assistant in DIR (default: ../ha-dev next to the repo),
#        a minimal configuration and a symlink to custom_components/gwn_manager.
# run    starts Home Assistant on http://localhost:8123 (log: DIR/config/home-assistant.log).
#
# Requires uv (https://docs.astral.sh/uv/). No Docker needed.
set -eu

REPO="$(cd "$(dirname "$0")/.." && pwd)"
CMD="${1:-setup}"
DIR="${2:-$REPO/../ha-dev}"
PYTHON_VERSION="${PYTHON_VERSION:-3.14}"

case "$CMD" in
  setup)
    mkdir -p "$DIR/config/custom_components"
    DIR="$(cd "$DIR" && pwd)"
    [ -d "$DIR/.venv" ] || uv venv --python "$PYTHON_VERSION" "$DIR/.venv"
    uv pip install --python "$DIR/.venv/bin/python" homeassistant segno
    ln -sfn "$REPO/custom_components/gwn_manager" "$DIR/config/custom_components/gwn_manager"
    if [ ! -f "$DIR/config/configuration.yaml" ]; then
      # No default_config: it needs ffmpeg/go2rtc, which a bare venv lacks.
      cat > "$DIR/config/configuration.yaml" <<'YAML'
frontend:
config:
history:
logbook:
system_log:
diagnostics:

logger:
  default: warning
  logs:
    custom_components.gwn_manager: debug
YAML
    fi
    echo "Done. Start with: $0 run $DIR"
    ;;
  run)
    DIR="$(cd "$DIR" && pwd)"
    exec "$DIR/.venv/bin/hass" -c "$DIR/config" --log-file "$DIR/config/home-assistant.log"
    ;;
  *)
    echo "usage: $0 [setup|run] [DIR]" >&2
    exit 2
    ;;
esac
