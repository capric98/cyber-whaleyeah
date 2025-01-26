#!/bin/sh
pip install --upgrade pip
pip install --upgrade --no-cache-dir -r /requirements.txt
cp /data/db/recover.py /recover.py 2>/dev/null || :

exec "$@"