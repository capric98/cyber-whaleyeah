#!/bin/bash
# git pull

# mkdir -p database
# docker-compose rm -sf
# MYUID="$(id -u)" MYGID="$(id -g)" docker-compose up -d
MYUID="$(id -u)" MYGID="$(id -g)" docker-compose restart whaleyeah

docker-compose logs -f whaleyeah