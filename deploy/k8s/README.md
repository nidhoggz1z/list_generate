# 材料清单 K8s 独立交付（SIT）

部署方式参考 shb-delivery-deploy：解压部署包 → 导入镜像 → 推送 Harbor → 应用服务 YAML → 等待就绪。命名空间是 `agent2`，所有操作只涉及材料清单项目的资源。不生成或要求额外的校验文件。

## 本地准备源码和离线依赖（不构建镜像）

在项目根目录执行：

```bash
bash deploy/k8s/prepare-bank.sh
```

首版默认版本为 `v1`，生成 `material-checklist-bank-v1.tar.gz`，镜像为 `10.208.11.33/agents/shb/material-checklist-dify:v1`。后续发布可显式指定新版本，例如：

```bash
VERSION=v2 bash deploy/k8s/prepare-bank.sh
```

本地需要 Python 3.11+ 和 pip，不需要 Docker。脚本为 Linux x86_64、CPython 3.12、glibc 2.28 采集 wheel（兼容 manylinux2014），并用采集结果离线解析一次依赖闭包，缺少二进制 wheel 时停止。自动带上源码、`Dockerfile.bank`、根目录 `rules/` 的 Word/Excel、构建脚本和 K8s 清单，不携带 Git、实际 gateway.env 或本地虚拟环境。

输出为 `dist/material-checklist-bank-<版本>.tar.gz`。将这个准备包上传到 `10.208.11.33` 的可写构建目录；不要覆盖解压到旧源码目录。

## 在 11.33 使用麒麟基座构建

在 11.33 存放首版准备包的目录执行：

```bash
tar -xzf material-checklist-bank-v1.tar.gz
cd material-checklist-bank
bash deploy/k8s/make-package.sh
```

脚本读取包内版本和离线 wheel，使用专用 `Dockerfile.bank` 构建镜像。默认麒麟基础镜像与 loan-assistant 相同：

```text
10.208.11.33/k8s/kylinv10sp3-python312-ssh:latest
```

如现场基座地址有变化，通过 `BANK_BASE=<实际麒麟镜像> bash deploy/k8s/make-package.sh` 指定。Dockerfile 直接继承基座层，使用基座 Python 3.12 创建 venv，从 wheelhouse 离线安装；不使用 Debian 镜像、公网 pip 或 yum/apt。构建后以 appuser 并补充组 0 做 import 冒烟，再导出镜像、生成部署包。

11.33 构建只需 Bash、Docker、tar/gzip；不要求宿主机安装 Python 3.12 或 Git，打包代码由产物容器内的 Python 执行。版本优先取 `VERSION` 环境变量，其次取准备包的 `BANK_VERSION`，没有记录时默认为 `v1`。首版输出 `material-checklist-bank/dist/k8s-v1/`，存在时拒绝覆盖。后续发布使用新版本。需要核对镜像血缘时执行 `docker image inspect --format '{{json .RootFS.Layers}}' <镜像>`，产物层应保留麒麟基座层作为前缀。

输出只包括 `run-all.sh`、`material-checklist-deploy.tar.gz` 和 `material-checklist-images.tar.gz`（超过 1GiB 时自动分片）。不包含 HiAgent 工作流或平台。构建脚本只负责生成交付物，不执行 kubectl；推送 Harbor 和启动服务仍由下文 `run-all.sh` 执行。

## 现场一键部署

将 11.33 生成的输出目录下全部文件复制或上传到 `/haw/lwh-install/`。执行机器需要有 Docker 权限、Harbor 推送权限和 kubectl 权限，以及 Bash、tar/gzip、sha256sum（仅计算配置与规则版本摘要），验证外部入口时需要 curl。提前登录 `10.208.11.33`，确认 Harbor 的 `agents` 项目已存在且工作节点能够拉取镜像。Ingress 控制器需要监听 `agent2`，`/material-checklist` 路径不能被其他项目占用。

确认当前 kubeconfig 指向 SIT：

```bash
kubectl config current-context
kubectl cluster-info
```

首次执行前解压并填写服务 YAML 中的 Secret 配置（也可以交付前在待打包清单填写，真实凭据不要提交到 Git）：

```bash
cd /haw/lwh-install
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

脚本通过同镜像的临时准备 Pod 初始化目录并传入规则，无需 SSH。准备 Pod 以 root 运行，按基座 appuser 的实际 UID/GID 授予任务目录权限；主服务以 appuser 运行。两个 Pod 都带 `supplementalGroups: [0]`，确保能读取麒麟基座 Python。保留现有非空规则目录。Secret、Deployment、Service 和 Ingress 都在 `material-checklist.yaml`；不接管其他项目的同名资源，不清理整个 `agent2`。

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
2. 备份移走旧部署目录和旧镜像整包/分片，再上传完整的新交付物。不要删除节点数据。禁止把新包覆盖解压到旧目录。
3. 解压新部署包，将已有现场配置填入新服务 YAML，执行同一条 `run-all.sh`。

有未完成任务或服务无法检查时，脚本停止升级。变更前在解包目录 `backups/` 保存旧工作负载与 Secret，在固定节点 `backups/<时间>/state.tar.gz` 保存停止后的规则和完整任务数据。配置或镜像变更后使用 Recreate；同一清单重复执行不主动重启 Pod。业务日志主要在节点 `gateway_storage/log/`，`kubectl logs` 主要用于进程启动和异常排查。

规则升级需要先停收并排空任务，然后显式执行：

```bash
bash /haw/lwh-install/material-checklist-deploy/deploy-all.sh --update-rules
```

该操作使用新包规则替换节点规则，并先备份旧数据。失败时不清场、不自动回滚数据库；检查 Pod 事件和日志后可重跑。若中途已停止旧实例，故障期间服务会保持停止。需要恢复旧版本时，保持调用方停收，使用对应备份 Secret 与 Deployment 恢复（先将备份 Deployment 保持为 0 副本），必要时在固定节点上恢复完整 `state.tar.gz`，再将服务恢复为 1 副本并验证。不要让两个 Pod/进程同时使用同一任务目录。

固定节点故障需要人工恢复或搬迁数据；单副本升级有停机窗口。`agent2` 实现资源与发布独立，本交付不新增网络隔离策略。真实配置填写后的 YAML、备份和部署包均按含凭据文件保管。
