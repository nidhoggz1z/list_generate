# 材料清单高码

高码接收 CP 材料清单任务，调用 HiAgent 同步工作流，保存结果并向 CP 回调。服务同时提供 Word 规则目录和正文查询接口。

- gateway/ 包含任务 API、调度、同步客户端和回调。
- service/ 包含 Word 和 Excel 规则读取。
- nodes/ 包含可复用的 Python 业务逻辑。

复制 gateway.env.example 为 gateway.env，填写 HiAgent 同步接口和应用 Key，并按部署环境核对 CP 的完整回调地址。相对影像路径需要对应的下载配置。修改配置后重启高码。

工作流在 HiAgent 单独部署。Word 清单和话术库 Excel 由部署环境提供，放在 rules/。运行需要规则文件的测试前，先补齐这些材料。构建离线包时使用 bash huanban_tar.sh --without-workflow --no-split。部署步骤见 [部署说明](DEPLOYMENT.md)。
