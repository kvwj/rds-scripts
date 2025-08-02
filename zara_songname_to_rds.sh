#!/bin/bash
mkdir /mnt/zara
mount -t cifs //${SERVER}/${FOLDER} -o user=${USER},pass=${PASS} /mnt/zara
cd /opt/app
#FILE="$1"
FILE=/mnt/zara/ZaraLogs/CurrentSong.txt
#CMD="$2"
CMD1=$(echo "TEXT=$(</mnt/zara/ZaraLogs/CurrentSong.txt )"|nc -i 2 -w 2 192.168.0.108 7005)
CMD2=$(echo "DPS=$(</mnt/zara/ZaraLogs/CurrentSong.txt )"|nc -i 2 -w 2 192.168.0.108 7005)
LAST=`ls -l "$FILE"`
while true; do
  sleep 1
  NEW=`ls -l "$FILE"`
  if [ "$NEW" != "$LAST" ]; then
    SONGNAME=$(</mnt/zara/ZaraLogs/CurrentSong.txt)
    NAMELENGTH=${#SONGNAME}
    if [ "$NAMELENGTH" -gt 64 ]; then
      SONGNAMECUT=$(echo $SONGNAME | cut -c 1-64)
      echo "TEXT=$(echo $SONGNAMECUT )"|nc -i 2 -w 2 192.168.0.108 7005
      echo "TEXT?"|nc -i 2 -w 2 192.168.0.108 7005
      curl -X POST $SLACK_WEBHOOK_URL -d "{\"text\": \"KVWJ Song name too long -- '${SONGNAME}'\"}"
    fi
    if [ "$NAMELENGTH" -lt 65 ]; then
      echo "TEXT=$(echo $SONGNAME )"|nc -i 2 -w 2 192.168.0.108 7005
      echo "TEXT?"|nc -i 2 -w 2 192.168.0.108 7005
    fi
#    echo "DPS=$(</mnt/zara/ZaraLogs/CurrentSong.txt )"|nc -w 2 192.168.0.108 7005
    LAST="$NEW"
  fi
done
