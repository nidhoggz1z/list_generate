# 材料清单 K8s 独立交付（SIT）

部署方式参考 shb-delivery-deploy：解压部署包 → 导入镜像 → 推送 Harbor → 应用服务 YAML → 等待就绪。命名空间是 `agent2`，所有操作只涉及材料清单项目的资源。

## 开发机打包

在项目根目录执行：

```bash
bash deploy/k8s/make-package.sh
```

默认版本取当前 Git 提交号。每次修改后使用新版本，示例：

```bash
VERSION=sit-20261112-01 bash deploy/k8s/make-package.sh
```

脚本使用现有 Dockerfile 构建 Linux amd64 镜像，自动打包根目录 `rules/` 的 Word 清单和话术库 Excel，无需指定规则目录。默认输出 `dist/k8s-<版本>/`；已存在时拒绝覆盖。开发机需要 Docker、Python 3.11+；现场只需 Bash、Docker、kubectl、tar、gzip、sha256sum，验证外部入口时需要 curl。

输出包括 `run-all.sh`、`material-checklist-deploy.tar.gz`、`material-checklist-images.tar.gz`（超过 1GiB 时自动分片）和 `SHA256SUMS`。不包含 HiAgent 工作流或平台。

## 现场一键部署

将输出目录下的全部文件上传到 `/haw/lwh-install/`。执行机器需要有 Docker 权限、Harbor 推送权限和 kubectl 权限；提前登录 `10.208.11.33`，确认 Harbor 的 `agents` 项目已存在且工作节点能够拉取镜像。Ingress 控制器需要监听 `agent2`，`/material-checklist` 路径不能被其他项目占用。

确认当前 kubeconfig 指向 SIT：

```bash
kubectl config current-context
kubectl cluster-info
```

首次执行前解压并填写服务 YAML 中的 Secret 配置（也可以交付前在待打包清单填写，真实凭据不要提交到 Git）：

```bash
cd /haw/lwh-install
sha256sum -c SHA256SUMS
tar -xzf material-checklist-deploy.tar.gz
vi material-checklist-deploy/k8s/material-checklist.yaml
```

需要核对 `DIFY_API_URL`（完整 HiAgent 同步 URL）、`DIFY_API_KEY`、`CALLBACK_URL`；相对影像路径还要填写 `IMAGE_PLATFORM_DOWNLOAD_BASE_URL`、`IMAGE_PLATFORM_AU`。配置集中在服务 YAML 的 Secret，容器通过环境变量读取，不需要额外 env 文件。仍为占位符时部署脚本会退出。

```bash
bash /haw/lwh-install/run-all.sh
```

`run-all.sh` 自动解压不存在的部署目录；已有目录则沿用，保留现场修改。然后调用 `install-images.sh` 导入、推送镜像，再调用 `deploy-all.sh` 启动服务。固定使用执行开始时的 context；也可用 `KUBE_CONTEXT=<实际context>` 指定，不修改当前 kubeconfig。不会自动登录 Harbor，不把账号密码写进脚本。

首次自动选择第一个 Ready、可调度的 amd64 非 master/control-plane 节点；写入 `.node-name`，渲染后的服务 YAML 保存在 `.runtime/`。以后同时核对已有 Deployment 和现场节点记录，不自动更换数据节点。节点目录为：

```text
/haw/shb/data/material-checklist/
├── rules/             # Word 清单和 Excel；主服务只读挂载
├── gateway_storage/   # SQLite 任务、结果、按日业务日志
└── backups/           # 升级时生成的完整数据备份
```

脚本通过同镜像的临时准备 Pod 初始化目录并传入规则，无需 SSH。保留现有非空规则目录。Secret、Deployment、Service 和 Ingress 都在 `material-checklist.yaml`；不接管其他项目的同名资源，不清理整个 `agent2`。

## 验证与接入

```bash
bash /haw/lwh-install/material-checklist-deploy/verify.sh
ENTRY_URL=http://<SIT入口IP>:30100/material-checklist bash /haw/lwh-install/material-checklist-deploy/verify.sh
```

第一条检查 Pod、配置、健康、规则目录、首份规则正文以及 OpenAPI 前缀；第二条增加外部入口检查。探针正常不代表规则和 HiAgent/CP 链路正常，之后仍需使用专用任务进行业务联调。

- CP：`POST http://<SIT入口IP>:30100/material-checklist/api/v1/tasks`。
- HiAgent 的 `checklist_gateway_url`：`http://<SIT入口IP>:30100/material-checklist`。
- 集群内：`http://material-checklist-gateway.agent2.svc:8000`，不加外部前缀。
- 根路径没有业务页面，访问根路径返回 404 正常。内网 Swagger 页面还受其 CDN 资源可达性影响，可直接使用 `/material-checklist/openapi.json`。

## 升级与故障恢复

1. 让调用方暂停新任务，等待 `/health` 的 `PENDING`、`PROCESSING` 为零。服务停止会取消正在执行的任务，重启不会自动重跑。
2. 备份移走旧部署目录、旧镜像整包/分片和校验文件，再上传完整的新交付物。不要删除节点数据。禁止把新包覆盖解压到旧目录。
3. 解压新部署包，将已有现场配置填入新服务 YAML，执行同一条 `run-all.sh`。

有未完成任务或服务无法检查时，脚本停止升级。变更前在解包目录 `backups/` 保存旧工作负载与 Secret，在固定节点 `backups/<时间>/state.tar.gz` 保存停止后的规则和完整任务数据。配置或镜像变更后使用 Recreate；同一清单重复执行不主动重启 Pod。业务日志主要在节点 `gateway_storage/log/`，`kubectl logs` 主要用于进程启动和异常排查。

规则升级需要先停收并排空任务，然后显式执行：

```bash
bash /haw/lwh-install/material-checklist-deploy/deploy-all.sh --update-rules
```

该操作使用新包规则替换节点规则，并先备份旧数据。失败时不清场、不自动回滚数据库；检查 Pod 事件和日志后可重跑。若中途已停止旧实例，故障期间服务会保持停止。需要恢复旧版本时，保持调用方停收，使用对应备份 Secret 与 Deployment 恢复（先将备份 Deployment 保持为 0 副本），必要时在固定节点上恢复完整 `state.tar.gz`，再将服务恢复为 1 副本并验证。不要让两个 Pod/进程同时使用同一任务目录。

固定节点故障需要人工恢复或搬迁数据；单副本升级有停机窗口。`agent2` 实现资源与发布独立，本交付不新增网络隔离策略。真实配置填写后的 YAML、备份和部署包均按含凭据文件保管。
