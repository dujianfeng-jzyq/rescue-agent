#!/bin/bash
# 把通风系统图 DWG 转成 DXF（在 Docker 里编译 LibreDWG 0.13.3，宿主机不装任何东西）。
# 用法：scripts/map/convert_dwg.sh [输入.dwg] [输出目录]
# 缺省：var/ventnet.dwg → var/map-work/out.dxf。输出 DXF 的 MTEXT 里有未转义换行，
# ezdxf 读不了，用 scripts/map/ 下自带的扫描器。
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
IN="${1:-$ROOT/var/ventnet.dwg}"
OUT="${2:-$ROOT/var/map-work}"
mkdir -p "$OUT"
cp -f "$IN" "$OUT/in.dwg"
docker run --rm -v "$OUT":/w -w /w ubuntu:22.04 bash -c '
set -e
apt-get update -qq >/dev/null 2>&1
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq build-essential curl ca-certificates xz-utils python3 perl >/dev/null 2>&1
[ -s lr.tar.xz ] || curl -fsSL --retry 5 --retry-delay 5 --retry-all-errors -o lr.tar.xz https://github.com/LibreDWG/libredwg/releases/download/0.13.3/libredwg-0.13.3.tar.xz
[ -x libredwg-0.13.3/programs/dwg2dxf ] || { rm -rf libredwg-0.13.3; tar xf lr.tar.xz; cd libredwg-0.13.3
  ./configure --disable-bindings --disable-werror --disable-docs -q 2>&1 | tail -5; make -j"$(nproc)" 2>&1 | tail -5; cd /w; }
libredwg-0.13.3/programs/dwg2dxf -y -o out.dxf in.dwg 2>&1 | tail -3
chown -R '"$(id -u):$(id -g)"' /w
'
ls -la "$OUT/out.dxf"
