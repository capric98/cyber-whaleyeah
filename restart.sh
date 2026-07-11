#!/bin/bash
set -euo pipefail

MYUID="$(id -u)" MYGID="$(id -g)" docker compose restart whaleyeah
docker compose logs --tail 100 -f whaleyeah
