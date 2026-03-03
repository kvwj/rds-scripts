#!bin/bash
# Song announcements startup script
mkdir /mnt/zara
mount -t cifs //${SERVER}/${FOLDER} -o user=${USER},pass=${PASS} /mnt/zara
cd /opt/app
python3 server.py
