# 材料清单高码离线部署

本包使用现有 Dify／HiAgent 平台，只部署 material-checklist-dify:1.0 高码镜像、规则资源和运维脚本。不包含工作流、模型供应商配置或真实凭据。不要使用原“文件检索”的 file-retrieval 镜像代替。

## 交付与安装

构建机执行 `bash huanban_tar.sh --without-workflow --no-split`，生成一个 huanban.tar。镜像为 Linux amd64、Python 3.12 slim-bullseye；运行机只需已有 Docker、Bash、tar、gzip、sha256sum，无需联网安装依赖。

在独立解包目录执行：

```sh
tar -xf huanban.tar
bash release.sh
```

release.sh 校验文件、加载镜像并安装到 /data/deploy_material-checklist-dify。仅在缺失时初始化 gateway.env；rules 缺失或完全为空时从包内初始化，已有非空规则目录保持原样。保留现场配置、数据库和日志，不自动启动服务。

## 高码配置

首次安装使用包内模板初始化 gateway.env，其中已预填 HiAgent 接口和 CP 回调地址。升级保留部署机现有配置。部署时在目标机填写已发布 HiAgent 应用的 API Key，并按需填写影像下载参数。配置项如下。

```text
DIFY_API_URL=<Postman中调用成功的完整HiAgent同步接口URL>
DIFY_API_KEY=<HiAgent已发布应用的APIKey>
IMAGE_PLATFORM_DOWNLOAD_BASE_URL=影像下载地址头
IMAGE_PLATFORM_AU=现场AU
CALLBACK_URL=<CP回调接口的完整URL>
```

高码直接向 DIFY_API_URL 发起 POST。填入 Postman 直连成功的完整 URL，包含端口、网关前缀和 /sync_run_app_workflow 路径。DIFY_API_KEY 用作 Apikey 请求头。

请求体的 UserID 固定为 CP。高码把 CP 业务对象序列化成 businessData 字符串，再把包含 businessData 的开始节点入参对象序列化成 InputData 字符串。同步响应的 status 字段如有返回须为 success。高码解析 output 字符串并提取 result 和 callbackResult，响应包含 runId 时记录该编号。output 为 null 或缺少业务结果时，任务标记为失败，可按 runId 核对 HiAgent 的执行记录。CP 提交任务后立即收到受理响应，后台执行器等待同步接口返回。

修改 gateway.env 后重启高码。CP 传入相对路径时，高码把已填写的 IMAGE_PLATFORM_DOWNLOAD_BASE_URL 和 IMAGE_PLATFORM_AU 映射为开始节点的 image_platform_download_base_url 和 image_platform_au，和 businessData 同层放入 InputData。留空的配置交由 HiAgent 工作流默认值或绑定变量提供。工作流按相对路径编码、拼接并附加 au 参数。CP 传入完整 HTTP(S) PDF 地址时，工作流直接下载。


在 gateway.env 中用 CALLBACK_URL 设置 CP 回调接口的完整 URL。缺少该项或留空时，沿用现有地址。
`http://10.240.184.200:9090/NLSC/LdsAiTaskESBFunctionApi/0.1.0/ldaAiTaskResultCallback`。
沿用原回调请求头和 C/F 流水号、北京时间格式。回调正文使用格式 B 的五个字段；失败返回 FAILED、错误信息和空清单。

## 一个端口提供全部高码接口

```sh
cd /data/deploy_material-checklist-dify
bash start.sh
bash show.sh
curl --fail http://127.0.0.1:18008/health
curl --fail http://127.0.0.1:18008/v1/checklist-files
```

仅启动 material-checklist-dify-gateway，宿主机 18008 映射容器 8000。默认部署机入口 http://10.240.99.210:18008：

| 调用方 | 方法与路径 | 用途 |
| --- | --- | --- |
| CP | POST /api/v1/tasks | 接收业务 JSON，立即受理并排队 |
| 工作流 | GET /v1/checklist-files | 列出 Word 材料清单 |
| 工作流 | POST /v1/checklist-files/content | 按 fileName 读取清单正文 |
| 运维 | GET /health | 确认网关服务及队列状态 |

读取正文的请求头为 Content-Type: application/json，正文示例 `{"fileName":"目录接口返回的完整文件名"}`。在 HiAgent 工作流给 checklist_gateway_url 绑定 http://10.240.99.210:18008。高码的文件接口可直接调用。根路径 / 未定义，访问 / 返回 404 属正常行为。

18018 的独立规则容器已移除。启动脚本仅清理带本项目所有权标签的旧规则容器，不停止其他项目。旧 gateway.env 中已废弃的端口、回调超时和文件服务密钥字段可读取但不再生效。端口默认固定为 18008；仅运维隔离测试时可通过 shell 环境变量 API_PORT 临时覆盖，不在 gateway.env 配置。

## 18008 返回 404 的排查

GET /v1/checklist-files 是新版网关的有效路径。若 /health 返回 `{"detail":"数据目录内没有可用材料清单"}`，该响应来自原“文件检索”项目；不是本包网关。补充旧项目材料无法使它拥有新版路由。

在部署机检查：

```sh
docker ps --format '{{.Names}} {{.Image}} {{.Ports}}'
ss -lntp 'sport = :18008'
curl -s http://127.0.0.1:18008/health
curl -s http://10.240.99.210:18008/health
```

docker ps 的 Image 是单数。publish 过滤无结果不能排除宿主机进程、host 网络容器或反向代理。先确认占用者，再由现场运维停用或迁移旧服务，释放 18008 后启动本包。不要盲目停止其他项目。新版健康响应包含 `service: material-checklist-gateway`、`status: healthy` 和并发数 2。

## 存储与工作流

### 目录接口返回“材料清单目录不可用”

该错误表示网关已运行，但枚举规则文件失败；与模型 Key、影像 AU 和 PDF 内容无关。接口不会返回内部路径。本次修正版会在 gateway_storage/log/YYYY-MM-DD.txt 记录 rules_dir、error_type 和 reason；上一版单端口包没有这条详细日志，可直接在原容器中运行以下只读命令，无需更换镜像：

```sh
docker inspect material-checklist-dify-gateway --format '{{range .Mounts}}{{println .Source "->" .Destination}}{{end}}'
docker exec material-checklist-dify-gateway python -B -c "from gateway.config import Settings; from service.app import RuleStore; p=Settings.from_env().rules_dir; print('rules_dir=',p,'exists=',p.exists(),'is_dir=',p.is_dir()); print('entries=',[x.name for x in p.iterdir()][:12]); print('checklists=',len(RuleStore(p).files()))"
```

FileNotFoundError 表示目录不存在或挂载错误；PermissionError 表示路径访问权限问题。ValueError 的“规则目录没有有效清单”表示顶层没有名称以“材料清单.docx”结尾的有效文件，检查是否目录为空、文件名变化或多套了一层 rules 子目录。“产品存在重复清单”应核对报错中的产品，避免同一产品同时存在带下划线和不带下划线的两份清单。路径不合法需检查符号链接是否指向挂载目录外。

上一版 release.sh 为保留现场维护内容，已存在 rules 时不会覆盖，即使该目录为空；本次修正版会向原空目录内补入包内规则，不替换目录本身，已有非空目录仍不覆盖。已部署旧版时，确认现场规则为空后，可在已校验的新包解压目录执行 `cp -a ./rules/. /data/deploy_material-checklist-dify/rules/` 恢复；不要删除 gateway_storage 或改动 API Key。若修改了绑定挂载目录本身，应重新运行 start.sh；仅在原目录补入文件后，目录接口会实时读取，通常无需重启。

rules 和 gateway.env 只读挂载；gateway_storage 持久保存输入、结果、队列数据库和按日日志。只允许一个容器、一个 uvicorn worker 使用同一存储；进程内最多两个工作流并发，其余持久排队。同业务只回调最新任务。停服后完整备份 gateway_storage，重启时未完成任务标记失败，不自动重跑。

`bash stop.sh` 停止本项目。升级前排空队列并备份配置、规则、存储与工作流；回退时配对恢复镜像和工作流。目标 Dify 继续使用现有模型供应商和 MinerU 凭据。健康检查和合成离线测试不能代替现场工作流、真实影像和 CP 联调。
