#!/bin/sh
# DSH 容器的健康检查：网页端在监听，且能连到模拟矿井和 actions（证据插件、指令发布都靠它们）。
set -eu
IP="$(hostname -i | awk '{print $1}')"
curl -s -o /dev/null --max-time 3 "http://$IP:${DSH_PORT:-3080}/"
curl -fs --max-time 3 http://mine-sim:8101/healthz >/dev/null
curl -fs --max-time 3 http://actions:8106/healthz >/dev/null
