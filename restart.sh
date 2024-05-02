#!/bin/bash
git pull

mkdir -p database
docker-compose rm -sf
MYUID="$(id -u)" MYGID="$(id -g)" docker-compose up -d

docker-compose logs -f