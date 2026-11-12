# 材料清单高码

高码接收 CP 材料清单任务，调用 HiAgent 同步工作流，保存结果并向 CP 回调。服务同时提供 Word 规则目录和正文查询接口。

- gateway/ 包含任务 API、调度、同步客户端和回调。
- service/ 包含 Word 和 Excel 规则读取。
- nodes/ 包含可复用的 Python 业务逻辑。

仓库里的 gateway.env 保存了接口地址和凭据占位符。部署时在目标机的 gateway.env 填入已发布 HiAgent 应用的 API Key。相对影像路径需要对应的下载配置。修改配置后重启高码。

构建离线包时使用 bash huanban_tar.sh --without-workflow --no-split。包内包含 rules/ 的 Word 清单和话术库 Excel。首次安装使用包内模板初始化配置，升级保留现场配置。工作流在 HiAgent 单独部署，目标环境需要能访问同步接口和 CP 回调。部署步骤见 [部署说明](DEPLOYMENT.md)。

K8s 独立部署使用 `agent2`，开发机执行 `bash deploy/k8s/make-package.sh`，现场执行 `bash /haw/lwh-install/run-all.sh`。服务配置直接填写在服务 YAML 的 Secret 中，规则自动随包交付。详见 [K8s 部署说明](deploy/k8s/README.md)。
