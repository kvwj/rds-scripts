#!/bin/bash
#FILE="$1"
FILE=/mnt/zara/CurrentSong.txt
#CMD="$2"
CMD1=$(echo "TEXT=$(</mnt/zara/CurrentSong.txt )"|nc -w 2 192.168.0.108 7005)
CMD2=$(echo "DPS=$(</mnt/zara/CurrentSong.txt )"|nc -w 2 192.168.0.108 7005)
LAST=`ls -l "$FILE"`
while true; do
  sleep 1
  NEW=`ls -l "$FILE"`
  if [ "$NEW" != "$LAST" ]; then
    echo "TEXT=$(</mnt/zara/CurrentSong.txt )"|nc -w 2 192.168.0.108 7005
#    echo "DPS=$(</mnt/zara/CurrentSong.txt )"|nc -w 2 192.168.0.108 7005
    LAST="$NEW"
  fi
done
