#!/usr/bin/env bash
set -euo pipefail

usage() {
    cat <<'EOF'
Usage: ./control.sh [restart|build|logs|status] [--no-logs]

Commands:
  restart  Restart the existing bot container (default).
  build    Rebuild the image and apply it to the bot container.
  logs     Follow the last 100 lines of bot logs.
  status   Show all service containers, including stopped ones.

Options:
  --no-logs  Return after restart/build without following logs.
  -h, --help Show this help.

Restart and build affect only the bot. MongoDB must already be running and
healthy before build. MYUID/MYGID default to the current account's UID/GID;
export them before running this script to use different IDs.
EOF
}

action=restart
if [[ $# -gt 0 && "$1" != -* ]]; then
    action="$1"
    shift
fi

follow_logs=true
for option in "$@"; do
    case "$option" in
        --no-logs) follow_logs=false ;;
        -h|--help) usage; exit 0 ;;
        *) printf 'Unknown option: %s\n' "$option" >&2; usage >&2; exit 2 ;;
    esac
done

case "$action" in
    help) usage; exit 0 ;;
    restart|build|logs|status) ;;
    *) printf 'Unknown command: %s\n' "$action" >&2; usage >&2; exit 2 ;;
esac

if [[ "$follow_logs" == false && "$action" != restart && "$action" != build ]]; then
    printf '%s\n' '--no-logs is only supported with restart or build.' >&2
    exit 2
fi

if ! command -v docker >/dev/null 2>&1; then
    printf '%s\n' 'Docker is required. Install Docker with the Compose plugin.' >&2
    exit 1
fi

# Resolve Compose files and bind mounts relative to this script, from any cwd.
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
MYUID="${MYUID:-$(id -u)}"
MYGID="${MYGID:-$(id -g)}"
export MYUID MYGID

case "$action" in
    restart) docker compose restart --no-deps whaleyeah ;;
    build) docker compose up -d --build --no-deps whaleyeah ;;
    logs) exec docker compose logs --tail 100 --follow whaleyeah ;;
    status) exec docker compose ps --all ;;
esac

if [[ "$follow_logs" == true ]]; then
    exec docker compose logs --tail 100 --follow whaleyeah
fi
