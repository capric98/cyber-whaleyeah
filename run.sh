#!/bin/bash
mkdir -p database
chown -R 101:101 database
docker-compose rm -f
docker-compose up -d