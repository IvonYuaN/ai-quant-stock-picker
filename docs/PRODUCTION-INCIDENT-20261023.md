# 生产卡顿事故记录：ECS 内存耗尽

## 结论

2026-10-23 16:02（北京时间）ECS 发生全局 OOM。内核明确杀掉了 `node` 进程（PID 95427），其父进程为 `npm run build`；当时机器同时运行 FastAPI、Vite preview、esbuild、MySQL、宝塔面板和后台任务。1.6 GiB 实例进入内存回收压力后，表现为 SSH banner、API 和前端同时超时，随后在 16:42 重启。

2026-10-20 17:41 也发生过同类 Node OOM。两次证据都指向生产机执行前端构建，而不是行情接口连接失败或业务代码死循环。

## 已落地的防线

- 生产部署脚本默认拒绝在小 ECS 执行 `npm ci` / `vite build`；只继承已经验证的 `frontend/node_modules` 和 `frontend/dist`。
- 若在有足够内存的构建机显式构建，脚本仍会执行 `MemAvailable` 预检并限制 Node 堆。
- 实时股票池解析和盘中主锁已有硬超时、跨重启陈旧锁回收，避免外部行情请求无限占住调度链。
- 当前 release 的 systemd 服务只运行 API 和 Vite preview；不允许在 ECS 手工执行 `npm run build`。

## 当前核验

2026-10-24 重启后，API 和前端 target active，服务重启计数为 0；API 约 227 MiB、preview 约 108 MiB，整机 `MemAvailable` 约 802 MiB。公网首页、health、alerts、market overview 均返回 200，隔离 raw 页面检查通过。

## 操作边界

后续前端改动必须在本地或构建机执行 `npm run build`，把制品随 immutable release 发布；生产机只做制品继承、切换和健康检查。若再次出现整机不可达，先查 `journalctl -k` 的 OOM 记录和 `last -x`，不要直接重复重启或在 ECS 重跑 npm。
