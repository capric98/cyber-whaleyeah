#!/bin/bash
mkdir -p database
docker-compose rm -s
MYUID="$(id -u)" MYGID="$(id -g)" docker-compose up -d