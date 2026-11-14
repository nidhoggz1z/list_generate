# 材料清单 K8s 独立交付（SIT / PI / UAT / 生产）

部署方式参考 shb-delivery-deploy：解压部署包 → 导入镜像 → 推送 Harbor → 应用服务 YAML → 等待就绪。命名空间是 `agent2`，所有操作只涉及材料清单项目的资源。不生成或要求额外的校验文件。

## 选择环境和镜像地址

四套配置分别位于 `k8s/sit/`、`k8s/pi/`、`k8s/uat/`、`k8s/prod/`，命名空间均为 `agent2`。通过 `DEPLOY_ENV=sit|pi|uat|prod` 选择打包的 YAML，默认 `sit`。四套配置用于各自的集群；执行前确认目标 context，不能在同一集群的 `agent2` 同时部署多套同名服务。

每个环境使用独立的镜像清单：

| 环境 | 镜像清单 |
| --- | --- |
| SIT | `deploy/k8s/images-sit.txt` |
| PI | `deploy/k8s/images-pi.txt` |
| UAT | `deploy/k8s/images-uat.txt` |
| 生产 | `deploy/k8s/images-prod.txt` |

每个文件只放一行，包含实际 Harbor 地址、项目路径、镜像名和 tag。构建、导出、现场推送及生成的部署 YAML 均使用所选清单的完整值，不额外拼接 Harbor，也不覆盖 tag。部署包只携带所选环境的镜像清单，并通过包内 `ENVIRONMENT` 记录让现场脚本自动选择；现场仍直接运行 `run-all.sh`。

PI 镜像仓库为 `10.240.24.11`，UAT 镜像仓◊库为 `10.208.11.33`，路径均为 `agents/shb/material-checklist-dify:v1`。生产镜像清单仍用 `FIXME_` 占位；准备交付前必须填入实际完整镜像名，否则脚本会退出。同时填写所选环境 `material-checklist.yaml` 中的 HiAgent URL、API Key、CP 回调及影像参数。源 YAML 的 `image` 是模板值，打包时自动用对应镜像清单的完整值替换，无需手动保持两处一致。真实凭据不要提交到 Git。

例如准备 PI 交付：

```bash
vi deploy/k8s/images-pi.txt
vi k8s/pi/material-checklist.yaml
DEPLOY_ENV=pi bash deploy/k8s/prepare-bank.sh
```

生产使用 `DEPLOY_ENV=prod`、`deploy/k8s/images-prod.txt` 和 `k8s/prod/material-checklist.yaml`。假设镜像 tag 为 `v1`，PI 准备包是 `dist/material-checklist-bank-pi-v1.tar.gz`，生产是 `dist/material-checklist-bank-prod-v1.tar.gz`。解压后构建命令仍是 `bash deploy/k8s/make-package.sh`，脚本自动读取准备包中记录的环境，分别输出到 `dist/k8s-pi-v1/`、`dist/k8s-prod-v1/`。每个环境使用独立的解包目录。

UAT 的服务配置与 PI 相同，独立保存在 `k8s/uat/`，镜像使用 `deploy/k8s/images-uat.txt`。准备 UAT 交付：

```bash
DEPLOY_ENV=uat bash deploy/k8s/prepare-bank.sh
```

生成 `dist/material-checklist-bank-uat-v1.tar.gz`，上传到 11.33 的独立目录并解压：

```bash
tar -xzf material-checklist-bank-uat-v1.tar.gz
cd material-checklist-bank
bash deploy/k8s/make-package.sh
```

脚本自动读取包内 `BANK_ENV=uat`，输出到 `dist/k8s-uat-v1/`。将该目录全部交付物复制到 UAT 安装目录，再执行 `KUBE_CONTEXT=<UAT实际context> bash /haw/lwh-install/run-all.sh`。部署包通过 `ENVIRONMENT=uat` 自动选择镜像清单，无需现场再传 `DEPLOY_ENV`。切换环境或更新交付物时，先备份移走旧解包目录和旧镜像文件，再使用完整新包；`run-all.sh` 会沿用已有解包目录，不会用新压缩包覆盖它。


在 material-checklist-bank/dist/k8s-uat-v1 下面会生成一个run-all.sh和两个tar.gz包，
运行
```bash
bash run-all.sh
```
