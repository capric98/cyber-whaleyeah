#!/bin/bash
mkdir -p database
docker-compose rm -f
docker-compose up -d