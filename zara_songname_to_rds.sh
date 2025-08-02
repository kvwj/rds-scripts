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
DELIMITER=' - '
while true; do
  sleep 1
  NEW=`ls -l "$FILE"`
  if [[ $(date +%u) -eq 7 ]]; then
    #echo "Today is Sunday!"
    SONGNAME=$(</mnt/zara/ZaraLogs/CurrentSong.txt)
    NAMELENGTH=${#SONGNAME}
    if [ "$NAMELENGTH" -gt 64 ]; then
      SONGNAMECUT=$(echo $SONGNAME | cut -c 1-64)
      echo "TEXT=$(echo $SONGNAMECUT )"|nc -i 2 -w 2 192.168.0.108 7005
      echo "TEXT?"|nc -i 2 -w 2 192.168.0.108 7005
    fi
    if [ "$NAMELENGTH" -lt 65 ]; then
      echo "TEXT=$(echo $SONGNAME )"|nc -i 2 -w 2 192.168.0.108 7005
      echo "TEXT?"|nc -i 2 -w 2 192.168.0.108 7005
    fi
  fi
  if [[ $(date +%u) -ne 7 ]]; then
    #echo "Today is not Sunday."
    if [ "$NEW" != "$LAST" ]; then
      SONGNAME=$(</mnt/zara/ZaraLogs/CurrentSong.txt)
      DELCOUNT=$(echo "$SONGNAME" | grep -o "$DELIMITER" | wc -l)
      if [ "$DELCOUNT" -gt 1 ]; then
        NAMELENGTH=${#SONGNAME}
        if [ "$NAMELENGTH" -gt 64 ]; then
          SONGNAMECUT=$(echo $SONGNAME | cut -c 1-64)
          echo "TEXT=$(echo $SONGNAMECUT )"|nc -i 2 -w 2 192.168.0.108 7005
          echo "TEXT?"|nc -i 2 -w 2 192.168.0.108 7005
        fi
        if [ "$NAMELENGTH" -lt 65 ]; then
          echo "TEXT=$(echo $SONGNAME )"|nc -i 2 -w 2 192.168.0.108 7005
          echo "TEXT?"|nc -i 2 -w 2 192.168.0.108 7005
        fi
      fi
      if [ "$SONGNAME" = "Hyrum City Civics Night" ]; then
        echo "TEXT=$(echo $SONGNAME )"|nc -i 2 -w 2 192.168.0.108 7005
      fi
      if [ "$SONGNAME" = "The Magic of Christmas" ]; then
        echo "TEXT=The Magic of Christmas"|nc -i 2 -w 2 192.168.0.108 7005
      fi
      if [ "$SONGNAME" = "Hyrum City Patriotic Program" ]; then
        echo "TEXT=Hyrum City Patriotic Program"|nc -i 2 -w 2 192.168.0.108 7005
      fi
      if [[ "$SONGNAME" == *"FSN"* ]]; then
        echo "TEXT=FSN World News"|nc -i 2 -w 2 192.168.0.108 7005
      fi
      if [[ "$SONGNAME" == *"Big Time 80"* ]]; then
        echo "TEXT=Big Time 80s with Gary Teel"|nc -i 2 -w 2 192.168.0.108 7005
      fi
    fi
   fi
#    echo "DPS=$(</mnt/zara/ZaraLogs/CurrentSong.txt )"|nc -w 2 192.168.0.108 7005
    LAST="$NEW"
done
