#!/bin/bash
cd /home/dev/jxemall-collector
docker compose down
docker compose build --no-cache
docker compose up -d --force-recreate
echo "Deploy done — http://192.168.180.210:5050"
