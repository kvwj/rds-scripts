#!/bin/bash
mkdir /mnt/logs
mount -t cifs //${SERVER}/${FOLDER} -o user=${USER},pass=${PASS} /mnt/logs
cd /opt/app/rds
python zara_songname_to_rds.py
