#!/bin/bash
mkdir -p database
docker-compose rm -f
MYUID="$(id -u)" MYGID="$(id -g)" docker-compose up -d