#!/usr/bin/env bash
# 演示环境（Docker，docs/design-v2.md 7.2；服务器部署见 docs/deploy.md）。同一个脚本两处用：
#   在仓库里（scripts/demo.sh，make demo-*）：本机构建镜像，数据在 var/demo/，地图包 var/map/，配置读仓库根目录的 .env；
#   在发布包里（rescue-demo.sh，make demo-pack 生成）：用包里的镜像，不构建；配置和数据在包旁边的 rescue-demo-data/（DEMO_HOME 可改）。
#
#   up       启动全部服务，等健康检查通过，打印网页端和控制台地址（仓库里会先构建镜像；发布包里第一次会先导入镜像）
#   down     停止（数据保留）
#   reset    停止、把上一轮的数据移到归档、再启动（与控制台“新一轮演示”相同，只是要登录服务器执行）
#   logs     跟踪日志
#   urls     再打印一次网页端和控制台地址
#   pack     （只在仓库里）打发布包：镜像 + compose + 地图包 + 本脚本，输出到 var/dist/
#
# 令牌第一次启动时随机生成，存在 <数据目录>/tokens.env（只有本用户可读）；控制台登录用其中的 RESCUE_SIM_CONTROL_TOKEN。
# DEEPSEEK_API_KEY、RAGFlow 的配置从 .env 读，只传给需要它的容器。
# 对外访问：.env 里设 DEMO_PUBLIC_URL（如 https://demo.example.com/）和 DEMO_AUTH_PASSWORD，就另起 proxy（Caddy）容器：HTTPS + 登录口令。
# .env 里设了 RESCUE_ROUTE_SERVICE_URL（外部路径规划服务，第六版）时另起 route-planner 容器；
# 设了 RESCUE_FIRE_SIM_URL（外部火灾推演服务，第七版）时另起 fire-sim 容器。演示数据不混进开发的 var/（CLAUDE.md 约定 9）。
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CADDY_VERSION=2.8.4

if [ -f "$HERE/../docker/compose.yml" ]; then
  MODE=repo
  ROOT="$(cd "$HERE/.." && pwd)"
  ENV_FILE="$ROOT/.env"
  DEMO="$ROOT/var/demo"
  MAP_DIR="$ROOT/var/map"
  COMPOSE_FILES=(-f "$ROOT/docker/compose.yml" -f "$ROOT/docker/compose.build.yml")
  TAG=latest
  . "$ROOT/scripts/lib/env.sh"
else
  MODE=release
  ROOT="$HERE"
  DEMO_HOME="${DEMO_HOME:-$(cd "$HERE/.." && pwd)/rescue-demo-data}"
  ENV_FILE="$DEMO_HOME/.env"
  DEMO="$DEMO_HOME/data"   # 归档服务挂载整个数据目录，所以 .env（含模型密钥）不放在里面
  MAP_DIR="$ROOT/map"
  COMPOSE_FILES=(-f "$ROOT/compose.yml")
  TAG="$(cat "$ROOT/VERSION")"
  . "$ROOT/lib/env.sh"
fi
cd "$ROOT"
load_env "$ENV_FILE"
# 综采工作面的名称：缺省 211016，与地图包的 mine_map.yaml 对上；环境变量或 .env 里的 DEMO_PANEL 优先（如 DEMO_PANEL=1201）
export DEMO_PANEL="${DEMO_PANEL:-211016}" DEMO_DATA="$DEMO" DEMO_MAP_DIR="$MAP_DIR" DEMO_TAG="$TAG" DEMO_CADDY_VERSION="$CADDY_VERSION"
COMPOSE=(docker compose "${COMPOSE_FILES[@]}" --env-file "$DEMO/tokens.env")
DATA_DIRS=(records incidents duty runs ledger audit dsh-home documents control archive)

die() { echo "✗ $*" >&2; exit 1; }

first_run_env() {
  [ "$MODE" = release ] || return 0
  if [ ! -f "$ENV_FILE" ]; then
    mkdir -p "$DEMO_HOME"
    install -m 600 "$ROOT/env.example" "$ENV_FILE"
    die "已生成配置文件 $ENV_FILE，填好 DEEPSEEK_API_KEY、DEMO_PUBLIC_URL、DEMO_AUTH_PASSWORD 后再运行 $0 up"
  fi
}

tokens() {
  mkdir -p "$DEMO"
  if [ ! -f "$DEMO/tokens.env" ]; then
    (
      umask 077
      {
        echo "RESCUE_MCP_MINE_DATA_TOKEN=$(openssl rand -hex 16)"
        echo "RESCUE_MCP_ACTIONS_TOKEN=$(openssl rand -hex 16)"
        echo "RESCUE_MCP_MINE_ACT_TOKEN=$(openssl rand -hex 16)"
        echo "RESCUE_SIM_CONTROL_TOKEN=$(openssl rand -hex 16)"
      } >"$DEMO/tokens.env"
    )
    echo "  已生成令牌 $DEMO/tokens.env"
  fi
  # 后来新增的令牌：旧的 tokens.env 补一行，其他令牌不变（第三版的写入令牌、第六版的 route_planner 令牌、第七版的 fire_sim 令牌）
  for name in RESCUE_MCP_MINE_ACT_TOKEN RESCUE_MCP_ROUTE_PLANNER_TOKEN RESCUE_MCP_FIRE_SIM_TOKEN; do
    if ! grep -q "^$name=" "$DEMO/tokens.env"; then
      echo "$name=$(openssl rand -hex 16)" >>"$DEMO/tokens.env"
      echo "  已向 $DEMO/tokens.env 补充 $name"
    fi
  done
  mkdir -p "$MAP_DIR"   # 地图包（真实矿井图，仓库里 git 忽略）：不放进数据目录，新一轮演示不清它
  for d in "${DATA_DIRS[@]}"; do mkdir -p "$DEMO/$d"; done
}

# 容器以 uid 1000 运行：数据目录要归它所有。服务器上常用 root 或别的用户运行本脚本，用一次性容器改属主（不需要 sudo）
fix_owner() {
  local d need=()
  for d in "${DATA_DIRS[@]}"; do
    [ "$(stat -c %u "$DEMO/$d")" = 1000 ] || need+=("/d/$d")
  done
  [ ${#need[@]} -eq 0 ] && return 0
  docker run --rm --user 0 --entrypoint chown -v "$DEMO:/d" "rescue-demo-python:$TAG" -R 1000:1000 "${need[@]}"
}

# 对外访问：按 DEMO_PUBLIC_URL 算 Caddy 的站点地址、DSH 的对外地址和来源白名单、要映射的端口，口令算成 bcrypt 散列
public_config() {
  PROFILES=()
  rm -f "$DEMO/proxy-ports.yml"
  if [ -z "${DEMO_PUBLIC_URL:-}" ]; then
    export DEMO_PUBLIC_URL= DEMO_TRUSTED_HOSTS= DEMO_SITE= DEMO_HOST=
    return 0
  fi
  : "${DEMO_AUTH_PASSWORD:?对外访问要在 $ENV_FILE 里设 DEMO_AUTH_PASSWORD（浏览器登录口令）}"
  local url="${DEMO_PUBLIC_URL%/}" scheme rest hostport host port
  scheme="${url%%://*}"; rest="${url#*://}"; hostport="${rest%%/*}"
  case "$scheme" in http | https) ;; *) die "DEMO_PUBLIC_URL 要以 http:// 或 https:// 开头：$DEMO_PUBLIC_URL" ;; esac
  [ "$rest" = "$hostport" ] || die "DEMO_PUBLIC_URL 不支持子路径（只能是 https://主机[:端口]/）：$DEMO_PUBLIC_URL"
  if [[ "$hostport" == *:* ]]; then host="${hostport%:*}"; port="${hostport##*:}"; else host="$hostport"; port=$([ "$scheme" = https ] && echo 443 || echo 80); fi
  export DEMO_SITE="$scheme://$hostport" DEMO_HOST="$host" DEMO_PUBLIC_URL="$url/" DEMO_TRUSTED_HOSTS="$host:$port $host"
  local bind="${DEMO_BIND:-0.0.0.0}"
  {
    echo "# demo.sh 按 DEMO_PUBLIC_URL 生成，不要手改"
    echo "services:"
    echo "  proxy:"
    if [ "$scheme" = https ] && [ "$port" = 443 ]; then
      echo "    ports: [\"$bind:443:443\", \"$bind:80:80\"]   # 80：证书申请和跳转到 https"
    else
      echo "    ports: [\"$bind:$port:$port\"]"
    fi
  } >"$DEMO/proxy-ports.yml"
  COMPOSE+=(-f "$DEMO/proxy-ports.yml")
  PROFILES+=(public)
  export DEMO_AUTH_USER="${DEMO_AUTH_USER:-demo}"
  DEMO_AUTH_HASH="$(docker run --rm -e PW="$DEMO_AUTH_PASSWORD" "caddy:$CADDY_VERSION-alpine" sh -c 'caddy hash-password --plaintext "$PW"')"
  export DEMO_AUTH_HASH
}

# 外部计算服务：设了提供方地址才启用对应的 compose 组（route-planner、fire-sim 容器）并把适配服务的地址给 DSH
route_profile() {
  if [ -n "${RESCUE_ROUTE_SERVICE_URL:-}" ]; then
    PROFILES+=(route)
    export DEMO_ROUTE_PLANNER_URL=http://route-planner:8107/mcp RESCUE_ROUTE_SERVICE_URL RESCUE_ROUTE_SERVICE_TOKEN="${RESCUE_ROUTE_SERVICE_TOKEN:-}"
  else
    export DEMO_ROUTE_PLANNER_URL=
  fi
  if [ -n "${RESCUE_FIRE_SIM_URL:-}" ]; then
    PROFILES+=(fire)
    export DEMO_FIRE_SIM_URL=http://fire-sim:8108/mcp RESCUE_FIRE_SIM_URL RESCUE_FIRE_SIM_TOKEN="${RESCUE_FIRE_SIM_TOKEN:-}"
  else
    export DEMO_FIRE_SIM_URL=
  fi
  export COMPOSE_PROFILES
  COMPOSE_PROFILES="$(IFS=,; echo "${PROFILES[*]}")"
}

load_images() {
  [ "$MODE" = release ] || return 0
  local img missing=0
  for img in "rescue-demo-python:$TAG" "rescue-demo-dsh:$TAG" "caddy:$CADDY_VERSION-alpine"; do
    docker image inspect "$img" >/dev/null 2>&1 || missing=1
  done
  if [ $missing = 1 ]; then
    echo "== 导入镜像（第一次约需几分钟）"
    docker load -i "$ROOT/images.tar.gz"
  fi
}

wait_healthy() {
  local deadline=$((SECONDS + 600)) id st
  local svcs=(mine-sim actions sentinel housekeeper dsh)
  [ -n "${DEMO_ROUTE_PLANNER_URL:-}" ] && svcs+=(route-planner)
  [ -n "${DEMO_FIRE_SIM_URL:-}" ] && svcs+=(fire-sim)
  for svc in "${svcs[@]}"; do
    while :; do
      id="$("${COMPOSE[@]}" ps -q "$svc")"
      st="$(docker inspect -f '{{.State.Health.Status}}' "$id" 2>/dev/null || echo starting)"
      [ "$st" = healthy ] && { echo "  ✓ $svc"; break; }
      [ $SECONDS -ge $deadline ] && die "$svc 未就绪（$st），查看：$0 logs"
      sleep 2
    done
  done
}

urls() {
  local url token
  # 登录链接 DSH 每次启动都换（已登录的浏览器靠 cookie，30 天有效）；轮次守护进程把当前的记在控制目录里
  url="$(sed -n 's/.*"url": *"\([^"]*\)".*/\1/p' "$DEMO/control/login-dsh.json" 2>/dev/null || true)"
  [ -n "$url" ] || url="$("${COMPOSE[@]}" logs dsh 2>/dev/null | grep -Eo 'https?://[^ ]+/\?token=[A-Za-z0-9_-]+' | tail -1 || true)"
  token="$(sed -n 's/^RESCUE_SIM_CONTROL_TOKEN=//p' "$DEMO/tokens.env")"
  echo
  echo "网页端：${url:-（带 token 的地址见 $0 logs）}"
  echo "        （首次登录用；智能体每次重启都会换链接，已登录的浏览器不受影响；控制台“演示轮次”里也能看到当前链接）"
  if [ -n "${DEMO_PUBLIC_URL:-}" ]; then
    echo "控制台：${DEMO_PUBLIC_URL%/}/console"
    echo "浏览器登录：用户 ${DEMO_AUTH_USER:-demo}，口令见 $ENV_FILE 的 DEMO_AUTH_PASSWORD"
    echo "本机（SSH 隧道）：网页端 127.0.0.1:${DEMO_WEB_PORT:-13080}，控制台 127.0.0.1:${DEMO_CONSOLE_PORT:-13090}/console"
  else
    echo "控制台：http://127.0.0.1:${DEMO_CONSOLE_PORT:-13090}/console"
  fi
  echo "控制台的控制令牌：$token"
  [ -n "${DEMO_ROUTE_PLANNER_URL:-}" ] && echo "避灾路线：外部路径规划服务 $RESCUE_ROUTE_SERVICE_URL（route-planner 容器）"
  [ -n "${DEMO_FIRE_SIM_URL:-}" ] && echo "火灾蔓延：外部火灾推演服务 $RESCUE_FIRE_SIM_URL（fire-sim 容器）"
  return 0
}

prepare() {
  first_run_env
  : "${DEEPSEEK_API_KEY:?在 $ENV_FILE 里填写 DEEPSEEK_API_KEY}"
  export DEEPSEEK_API_KEY RAGFLOW_BASE_URL="${RAGFLOW_BASE_URL:-}" RAGFLOW_API_KEY="${RAGFLOW_API_KEY:-}" \
    KB_DATASETS_REGULATIONS="${KB_DATASETS_REGULATIONS:-}" KB_DATASETS_PLANS="${KB_DATASETS_PLANS:-}" KB_DATASETS_CASES="${KB_DATASETS_CASES:-}"
  tokens
  load_images
  public_config
  route_profile
}

up() {
  prepare
  if [ "$MODE" = repo ]; then
    "${COMPOSE[@]}" build
  fi
  [ -f "$MAP_DIR/geometry.json" ] || echo "  ! 没有地图包（$MAP_DIR/geometry.json），网页端是 DSH 原界面"
  fix_owner
  "${COMPOSE[@]}" up -d --remove-orphans
  echo "== 等待健康检查"
  wait_healthy
  urls
}

down() {
  [ -f "$DEMO/tokens.env" ] || return 0
  [ -f "$DEMO/proxy-ports.yml" ] && COMPOSE+=(-f "$DEMO/proxy-ports.yml")
  COMPOSE_PROFILES=route,fire,public "${COMPOSE[@]}" down
}

reset() {
  down
  prepare
  fix_owner
  # 与控制台“新一轮演示”同一套归档逻辑（housekeeper.py），服务都停着，直接归档
  "${COMPOSE[@]}" run --rm --no-deps -T housekeeper python -m rescue_mine_data_sim.housekeeper archive
  up
}

# 发布包：镜像（python、dsh、caddy）+ compose.yml + Caddyfile + 本脚本 + 地图包 + 配置样例 + 部署说明
pack() {
  [ "$MODE" = repo ] || die "pack 只能在仓库里运行"
  [ -f "$MAP_DIR/geometry.json" ] || die "没有地图包 $MAP_DIR/geometry.json（发布包要带地图）"
  local ver dirty="" out
  git -C "$ROOT" diff --quiet HEAD -- . ':!var' 2>/dev/null || dirty="-dirty"
  ver="$(date +%Y%m%d-%H%M)-$(git -C "$ROOT" rev-parse --short HEAD)$dirty"
  out="$ROOT/var/dist/rescue-demo-$ver"
  [ -n "$dirty" ] && echo "  ! 工作区有未提交的改动，版本号带 -dirty"
  export DEMO_TAG="$ver" DEEPSEEK_API_KEY="${DEEPSEEK_API_KEY:-unused}"
  tokens   # compose 解析文件要用到令牌变量（只为构建，不进发布包）
  echo "== 构建镜像 $ver"
  COMPOSE_PROFILES=route,fire "${COMPOSE[@]}" build
  docker image inspect "caddy:$CADDY_VERSION-alpine" >/dev/null 2>&1 || docker pull "caddy:$CADDY_VERSION-alpine"
  rm -rf "$out"
  mkdir -p "$out/lib" "$out/map"
  cp "$ROOT/docker/compose.yml" "$ROOT/docker/Caddyfile" "$out/"
  cp "$ROOT/scripts/demo.sh" "$out/rescue-demo.sh"
  cp "$ROOT/scripts/lib/env.sh" "$out/lib/"
  cp "$ROOT/docker/env.example" "$out/env.example"
  cp "$ROOT/docs/deploy.md" "$out/部署说明.md"
  cp "$MAP_DIR/geometry.json" "$out/map/"
  [ -f "$MAP_DIR/mine_map.yaml" ] && cp "$MAP_DIR/mine_map.yaml" "$out/map/"
  echo "$ver" >"$out/VERSION"
  echo "== 导出镜像"
  docker save "rescue-demo-python:$ver" "rescue-demo-dsh:$ver" "caddy:$CADDY_VERSION-alpine" | gzip -1 >"$out/images.tar.gz"
  tar -C "$ROOT/var/dist" -cf "$out.tar" "rescue-demo-$ver"
  rm -rf "$out"
  echo
  echo "发布包：$out.tar（$(du -h "$out.tar" | cut -f1)）"
  echo "拷到服务器后：tar xf rescue-demo-$ver.tar && rescue-demo-$ver/rescue-demo.sh up（第一次会生成配置文件，填好再运行一次）"
}

case "${1:-}" in
  up) up ;;
  down) down ;;
  reset) reset ;;
  logs) [ -f "$DEMO/proxy-ports.yml" ] && COMPOSE+=(-f "$DEMO/proxy-ports.yml"); COMPOSE_PROFILES=route,fire,public "${COMPOSE[@]}" logs -f --tail 100 ;;
  urls) urls ;;
  pack) pack ;;
  *) sed -n '2,17p' "$0"; exit 1 ;;
esac
