# 演示环境的服务器部署

把 Docker 演示环境（模拟矿井 + 智能体 + 大屏）部署到一台服务器上，供多场演示反复使用。
做法是在本机打一个**发布包**（镜像、配置模板、地图包、启动脚本都在里面），拷到服务器上解压启动。服务器不需要联网构建，也不需要仓库代码。公网和内网用同一个包。

## 1. 服务器要求

| 项 | 要求 |
| --- | --- |
| 系统 | Linux x86_64（镜像在本机按 amd64 构建；ARM 服务器要另外构建） |
| 软件 | Docker 24+，带 compose 插件（`docker compose version` 能运行）；`openssl`、`tar` |
| 资源 | 4 核、8 GB 内存、20 GB 磁盘起步（镜像解压后约 3 GB；每轮演示的归档几 MB 到几十 MB） |
| 网络 | 要能访问大模型接口（DeepSeek：`api.deepseek.com:443`）；对外访问要开放端口，见第 3 节 |
| 用户 | 能运行 docker 的用户（root 或在 docker 组里）；容器以 uid 1000 运行，启动脚本会自动把数据目录改给 uid 1000 |

内网服务器访问不了 DeepSeek 时，模型要换成内网部署的（`profiles/local-llm.patch.yml`），这个还没有做进发布包。

## 2. 打包（在开发机上）

```bash
make demo-pack          # 等同 scripts/demo.sh pack
```

- 要求 `var/map/geometry.json`（地图包）存在，地图包随发布包一起发布（`map/` 目录）；换矿井图（如红柳煤矿）要重新打包。
- 输出 `var/dist/rescue-demo-<日期时间>-<提交号>.tar`，大小约 400 MB。工作区有未提交的改动时，版本号末尾带 `-dirty`。
- 包里有：`images.tar.gz`（镜像）、`compose.yml`、`Caddyfile`、`rescue-demo.sh`（就是 `scripts/demo.sh`）、`map/`、`env.example`、本说明（`部署说明.md`）、`VERSION`。**不含**任何密钥或令牌。

## 3. 部署（在服务器上）

```bash
scp var/dist/rescue-demo-<版本>.tar 服务器:/opt/rescue/
ssh 服务器
cd /opt/rescue && tar xf rescue-demo-<版本>.tar
rescue-demo-<版本>/rescue-demo.sh up       # 第一次：生成配置文件 /opt/rescue/rescue-demo-data/.env 后退出
vi rescue-demo-data/.env                   # 填 DEEPSEEK_API_KEY、DEMO_PUBLIC_URL、DEMO_AUTH_PASSWORD
rescue-demo-<版本>/rescue-demo.sh up       # 导入镜像（第一次几分钟）、启动、等健康检查，最后打印地址和令牌
```

配置和数据放在发布包**旁边**的 `rescue-demo-data/`（可用环境变量 `DEMO_HOME` 改位置），升级换包时不受影响：

```
/opt/rescue/
├── rescue-demo-<版本>/        发布包（只读，不用改）
└── rescue-demo-data/
    ├── .env                    配置（模型密钥、对外地址、登录口令）
    └── data/                   运行数据：tokens.env（服务间令牌）、本轮的记录/事件/台账/会话、archive/（历次演示的归档）、caddy/（证书）
```

### 对外地址（`.env` 的 `DEMO_PUBLIC_URL`）

| 写法 | 适用 | 端口 | 说明 |
| --- | --- | --- | --- |
| `https://demo.example.com/` | 公网，有域名 | 80、443 | 域名解析到服务器后自动申请证书（Let's Encrypt），公网要能访问 80 和 443 |
| `https://1.2.3.4/` 或 `https://1.2.3.4:8443/` | 公网或内网，只有 IP | 443（或写的端口） | 自签证书，浏览器第一次提示“不安全”，点继续访问 |
| `http://10.0.0.5:8080/` | 可信内网 | 写的端口 | 不加密 |
| 留空 | 只在服务器本机 | — | 经 SSH 隧道访问 `127.0.0.1:13080`、`127.0.0.1:13090/console` |

云服务器还要在安全组里放行对应端口。只能写“协议://主机[:端口]/”，不支持子路径。

### 三道门

1. **登录口令**（`DEMO_AUTH_USER` / `DEMO_AUTH_PASSWORD`）：网页端（智能体、大屏）先过浏览器的登录弹窗，挡住外人，也防止模型被乱用（模型按 token 计费）。
2. **网页端的登录链接**：`up` 最后打印的“网页端”地址带 `?token=…`。这是一次性的登录链接：浏览器打开后 DSH 写入登录 cookie（30 天有效，签名密钥存在 `data/dsh-home/.credentials.yaml`），之后直接打开对外地址即可。**DSH 每次启动（包括每次新一轮演示）都会换登录链接**，已经登录过的浏览器不受影响；新电脑要用当前的链接，控制台“演示轮次”里显示当前链接，也可以 `rescue-demo.sh urls` 查看。
3. **控制台的控制令牌**：控制台（`<对外地址>/console`）不弹登录框，页面能打开，但右上角要输入 `up` 打印的控制令牌（也在 `data/tokens.env` 的 `RESCUE_SIM_CONTROL_TOKEN`）才能看到和操作任何东西。控制台能推进演示、看真值，令牌只给演示员。（控制台不套登录口令，是因为口令和控制令牌都用 `Authorization` 请求头，套上后控制台的请求会被挡掉。）

## 4. 一场演示的流程

1. 大屏电脑打开网页端登录链接（第一次）或对外地址（已登录过）；演示员电脑打开控制台并输入控制令牌。
2. 控制台“演示导演”点 **开始演示**，导演按预警等级自己推进（第五版，`docs/design-v5-demo-flow.md`）；橙色时在大屏的卡片上选处置。
3. 演示结束（大屏显示“演示已结束”）后，控制台“演示轮次”点 **新一轮演示**：
   - 停止智能体（DSH）、guard、监听器；
   - 把上一轮的记录、事件、值班会话、台账、审计、工作区和 DSH 会话**移到** `data/archive/round-<轮次>-<时间>/`（不删除，复盘用）；
   - 模拟矿井回到常态，导演复位；
   - 重新启动各服务（约半分钟），已打开的大屏自动刷新，不用重新登录。
4. 回到第 2 步。

控制台打不开时，也可以登录服务器执行 `rescue-demo.sh reset`，效果相同。

## 5. 日常运维

| 要做什么 | 命令 |
| --- | --- |
| 再看一次地址和令牌 | `rescue-demo.sh urls` |
| 看日志 | `rescue-demo.sh logs` |
| 停止 / 启动 | `rescue-demo.sh down` / `rescue-demo.sh up`（数据保留；服务器重启后容器会自动起来） |
| 升级到新版本 | 解压新包，`rescue-demo-<旧版本>/rescue-demo.sh down`，再 `rescue-demo-<新版本>/rescue-demo.sh up`；数据和配置沿用 |
| 清理旧镜像 | `docker image ls 'rescue-demo-*'`，删掉不用的版本 |
| 清理旧归档 | 手工删 `data/archive/` 下不要的轮次（脚本不会自动删） |

## 6. 已知限制

- 同一时间只能进行一场演示：模拟矿井只有一个时钟，所有打开网页端的人看到的是同一场。
- 新一轮演示会中断正在进行的演示，确认框会提示。
- 自签证书（只有 IP 时）浏览器每台电脑第一次都要点“继续访问”。
- 模型用的是 DeepSeek 官方接口；内网部署要换本地模型（见第 1 节）。

## 7. 实现说明（开发者看）

- `docker/compose.yml` 只引用镜像，原样随包发布；本机构建另加 `docker/compose.build.yml`。路径由脚本给：`DEMO_DATA`（数据目录）、`DEMO_MAP_DIR`（地图包）、`DEMO_TAG`（镜像版本）。技能随镜像发布，不再挂载。
- 对外入口是 compose `public` 组的 `proxy`（Caddy，`docker/Caddyfile`）：`/console` 和 `/sim/` 转模拟矿井（只靠控制令牌），其余转 DSH（登录口令）；IP 访问时浏览器不发 SNI，所以设了 `default_sni`；端口由脚本按 `DEMO_PUBLIC_URL` 生成 `data/proxy-ports.yml`。DSH 的 `--public-url`、`--trusted-host` 由 `DSH_PUBLIC_URL`、`DSH_TRUSTED_HOSTS` 给（`docker/dsh-entrypoint.sh`）。
- 新一轮演示**不用 Docker 套接字**（那等于把宿主机 root 权限交给容器）：有状态的容器（dsh、actions、sentinel）由守护进程 `rescue_contract.rounds` 起服务，看控制目录 `data/control/round.json`；归档服务 `rescue_mine_data_sim.housekeeper`（housekeeper 容器，挂载整个数据目录）把轮次加一、等各服务报告已停、移走上一轮数据、让模拟矿井回到常态、再放行。服务没有按时停下时不归档，直接放行（控制台显示失败原因）。控制台经模拟矿井的 `/sim/demo/round`（控制令牌）转给归档服务。
- 归档只移动各数据目录**里面**的内容：目录本身是其他容器的挂载点，挪走目录会让容器继续写进归档。`dsh-home` 只移走 `sessions`、`storages`、`logs`，配置档和网页端令牌（`.credentials.yaml`）保留。
- 大屏轮询的状态里带 DSH 进程的启动编号（`boot`），变化时刷新页面（DSH 重启后旧会话已不存在）。DSH 重连时会重新挂载客户端插件，所以上次见到的编号存在 `sessionStorage`，不能放在闭包里。
- DSH 的 `?token=` 每个进程随机生成、只在 `GET /` 换 cookie（`dsh-client-connection` 的说明）。轮次守护进程转发服务输出时识别登录链接，记在 `data/control/login-dsh.json`，服务停下时删掉；归档服务的状态带 `login_url`，控制台显示。
