#!/bin/bash
mkdir -p database
docker-compose rm -f
UID="$(id -u)" GID="$(id -g)" docker-compose up -d