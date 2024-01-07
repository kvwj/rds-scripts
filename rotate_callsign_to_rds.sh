#!/bin/bash
while true; do
  sleep 5
    echo "DPS=KVWJ"|nc -w 2 192.168.0.108 7005
  sleep 5
    echo "DPS=J-95"|nc -w 2 192.168.0.108 7005
done
