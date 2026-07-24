#!/bin/bash
mkdir /mnt/logs
mount -t cifs //${SERVER}/${FOLDER} -o user=${USER},pass=${PASS} /mnt/logs
cd /opt/app/kvwjlogs
python insert_playback_events.py
