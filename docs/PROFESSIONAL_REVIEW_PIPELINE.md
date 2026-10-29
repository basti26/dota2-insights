# 单命令职业录像分析与恢复

2026-10-29实现。只串联已有收集/去重/分析器，不重解析DEM、不修改Pi服务、不打开游戏。默认完全使用本机完成资料；访问Pi必须显式加--collect-pi。

## 使用

```powershell
# 使用已有累计记录；同一批输出只允许创建一次。
python tools/run_professional_review.py --previous diagnostics/<累计>/registry.json --map <固定SHA地图.jpg> --reconcile-paused-metric-clocks --retain-missing-event-times --output diagnostics/<新运行>

# 加入一个新批次，可与--previous一起使用。
python tools/run_professional_review.py --batch diagnostics/<完成批次> --map <固定SHA地图.jpg> --output diagnostics/<新运行>

# 显式请求收集Pi最新一批完成分析后继续处理，单批仍按原有资源预算。
python tools/run_professional_review.py --collect-pi --previous diagnostics/<累计>/registry.json --map <固定SHA地图.jpg> --reconcile-paused-metric-clocks --retain-missing-event-times --output diagnostics/<新运行>

# 恢复原参数，不添加其他业务参数；源码/输入变化要创建新运行。
python tools/run_professional_review.py --resume --output diagnostics/<原运行>
```

默认时钟仍严格，示例中的两个时钟选项是显式选择既有规则：仅由明确暂停解释的指标时钟投影；原来为空的事件时间继续为空。原内容不改，不通过关掉检查绕过无法解释的偏差。

## 执行与结果

依次运行可选只读collect、merge、patterns、分时段map、context、events、revisits。每步调用固定本仓库工具，不从旧状态文件执行任意命令。最后生成index.html统一连接四种地图报告；pipeline.json保留参数、输入/源码指纹、原生进程PID/创建身份、各次尝试、原生退出码与产物SHA。

未完成/冲突/缺上下文和批次还有未取完的记录会明确列缺口。批次里记录的未完成状态只是冻结输入的历史观察，不是实时Pi状态；后来已完整纳入的同场旧“未完成”观察从缺口去掉。原批次剩余计数也不跨批相加。执行完成不代表整赛事全部数据齐全，更不代表地图或战术质量认证。

## 恢复边界

- 每次尝试写新的attempt-N目录，原失败半成品与日志保留，不递归删除、不覆盖旧报告。
- 完成阶段必须原生退出0、收据为完成状态并逐文件固定SHA；恢复时重新核对后跳过，不再次计数或重算。
- 输入、源码或已完成产物变化时拒绝复用；重新开始新运行，不将两个代码版本混作同一轮证据。
- 同一运行只有一个OS文件锁持有者。若旧子进程仍存在，必须匹配OS原生创建身份，返回waiting_for_stage而不重复启动；PID被复用不当成旧作业。
- 已确认原进程不存在且原生成功退出未落盘时，原尝试保留为中断，允许新尝试。若恰好在启动和PID落盘之间断电，无法确认是否启动过，则拒绝自动重启，明确报unconfirmed_launch。不会仅因观察超时重启。
- 每阶段有1800秒执行预算；超时只终止本命令持有的本机子进程并记失败，不对Pi解析进程操作。收集工具自身原有网络/响应预算仍保留。
- 所有候选都发生版本冲突时只保留注册表，停止后续分析，不生成伪空报告；用原--select明确选择并创建新运行。

当前是显式命令，不是Windows/Pi常驻定时任务，也未重新打包EXE。

## 最新增量结果（2026-10-29）

已实际执行带`--collect-pi`、旧累计注册表和既有时钟规则的新运行：`diagnostics/professional-review-20261029/run-v4/index.html`。只读收集第五场已完成解析，累计5场478眼；旧跨批重复7条未重复计数。七阶段（含collect）均原生退出0，再次resume不重算。逐个复验53份阶段产物SHA，并直接从冻结原始wards重新核对478行、24分层和36邻域。新增开局四分地图`phase-0-10-five-matches.png`已查看，保留示意底图/未认证提示。

本次快照另有一场等待录像重试，未混入统计；原四场快照的旧等待项已由第五场成功覆盖。执行为complete_with_gaps，快照不代表后续实时状态。开局天辉真眼一邻域从此前4/4场变为4/5场，10–20分钟天辉假眼一邻域从3/4变3/5；两者都是观察频率，不是眼位效果。五场只覆盖两组对阵组合（2场和3场），队伍/系列赛独立性仍未认证，不把5场视作5个独立队伍样本。

本轮只运行现有工具和更新资料，未改Pi服务、Foresight或分析源码；不重跑DEM、不新增眼位统计维度。既有776项完整离线回归仍为当前源码证据，不为资料增量重复全库测试。原始复算与指纹结果见同目录`independent-count-check.json`，其中independent_game_truth_verified=false明确表示内部复算不能替代游戏真值。

## 本轮实际验证

diagnostics/professional-review-20261029/run-v3/index.html：四场378眼、3条重复输入不重复计数。六阶段原生退出全部0；恢复后仍每阶段只有一次尝试，45个产物指纹复验通过，四个报告链接均存在。批次里的9066180509等待录像记录作为历史缺口保留，因此执行结果为complete_with_gaps；这不是本轮实时查询。

11项针对性恢复/故障检查覆盖非零退出、缺收据、产物/输入/源码变化、活动进程创建身份、PID复用、未确认启动、并发锁、真实生成子进程与全冲突停止。完整754项离线回归通过，证据diagnostics/offline-tests/run-00b7616ed748/result.json；源码未在回归期间变化，无失败/错误/跳过/后台异常。本轮未联网、开游戏或改Foresight/Pi。
