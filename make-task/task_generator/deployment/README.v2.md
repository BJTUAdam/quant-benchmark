# v2 镜像、网络与部署

Dockerfile.v2构建engine和worker两个target，基础Python3.12镜像按摘要固定，requirements.v2.lock锁定AKQuant0.3.76、Tushare1.4.29及传递依赖。宿主finlab控制器负责每次创建容器，因此不使用常驻策略容器替代每次启动。

```powershell
python -m pip install -e .
finlab setup
finlab doctor
finlab backtest build-images
```

build-images记录deployment/images.lock.json，正式运行按镜像ID启动；标签仅方便维护。修改引擎、SDK或依赖后重新构建，并运行finlab test及finlab verify。不要在正式七段中途换镜像或规则来改变已冻结实验口径。

上游采集运行在运营进程。TUSHARE_UPSTREAM_URL默认官方https://api.tushare.pro，也接受本机http://127.0.0.1:9000或中转地址；TUSHARE_TOKEN只通过环境变量配置。采集完成后冻结，运行容器不需要上游凭证。

正式worker只连接内部网络中的engine:8766和RPC。网络设置internal=true，容器不发布宿主端口，不挂Docker socket。每个job结束后删除网络和容器，持久状态留在宿主episode目录。

引擎从控制器校验后的disclosure.private.json取得本次范围，worker不能指定或扩大。越界整次403、审计失败503，均不返回行情。桥接日志写入挂载的/out/bridge_audit.private.sqlite，随job/artifacts保留；成功提交前先停止全部桥接请求并完成WAL检查点，再计算哈希。不能把该日志或披露策略挂给worker或放入公开归档。缺少披露策略的旧episode停止，运营方须重新核验绑定；不要混用旧镜像。

手动在容器中调用宿主服务时，127.0.0.1指该容器自身；Docker Desktop通常使用host.docker.internal。Compose服务之间使用服务名。这些说明用于运营采集环境，不能给正式策略开放上游网络。

```powershell
finlab backtest save-images dist/finlab_images_v2.tar
finlab backtest load-images dist/finlab_images_v2.tar
```

导出使用锁文件中的镜像ID；导入后逐项检查相同ID存在。镜像文件单独保存，不嵌入源码工具包。Linux/amd64是本次实际验证平台；其他架构应重新构建和验收，不能直接沿用当前验收结论。

新工具包只提供v2任务和运行入口。使用者本人通过finlab setup选择预算后才可启动；没有默认运行时限，AI不得代填配置。
