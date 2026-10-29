# 对局性能离线复核

新逐局采集包有显式入口tools/review_performance_match.py，也已接入tools/review_recordings.py的自动索引及日常联合报告，见PER_MATCH_PERFORMANCE_HANDOFF_V1.md；它允许助手其他观察继续运行，但本包的采集器必须先关闭并验证最终诊断。下述旧专用入口仍消费闭合启动器包。联合报告分开新旧格式并标记重合，不把同一CSV作为新增采样。

## 长帧原始区间样例（2026-10-29）

现有CSV同一解析过程中增加`long_interval_examples`，不启动PresentMon或额外采样。只在调用方已提供合格QPC范围时保留，继续沿用整个间隔须处于合格前台/新鲜/对局中窗口的门禁；没有QPC范围时明确unavailable_without_validated_qpc_scope，不从相对时间/UTC补齐。

每次按比赛范围解析最多5条CSV数据行，以该行最大有效提交/显示间隔超过1000/30毫秒为候选，降序保留；同值优先原数据行较早者。这个门槛仅用于挑样，不是体验验收。提交区间为QPCTime减MsBetweenPresents；显示区间由QPCTime、MsUntilDisplayed及MsBetweenDisplayChange计算，必须Dropped=0；显示未知/被丢弃不伪造显示间隔。两种区间分别保留，不能相加成两次卡顿。每条保留原CSV数据行号（不是物理文本行号）、PID/交换链/runtime/layer/frame_type、频率及上下文窗口索引。

多流只在样例排序时并列候选，不混合各自FPS分母或选择“主流”。只有5条候选的堆，额外内存不随CSV行数增长。旧FPS、分位数、边界排除等统计算法不变。日常/显式报告自动加中文长帧表；原报告和源CSV不回写。

实际重新准入最近三份原生性能包（两场明确比赛、一段公开比赛号为空的本地范围），每份5条，共15条。最长保留间隔分别43.0806、52.1062、58.3883毫秒；这些不是持续FPS，也不能排除选人阶段或记录范围外的长卡顿。逐条原CSV字段、间隔单位和QPC范围独立复查通过，原来源进入/退出SHA复验通过，三个包原有逐流统计与先前报告完全相等。证据`diagnostics/performance-slow-windows-20261029/REPORT.md`、`reports.json`及`raw-row-check.json`。

目前仍无把识别/录音任务与这些QPC区间精确关联的已认证时钟桥，不凭同一UTC秒、相近比赛时间归因；助手开关/CPU/GPU控制变量证据也缺。P5已完成异常位置提取，工作负载关联继续未知。5项新检查覆盖同一行双指标、5条上限/多流/相同值顺序、范围边界、显示未知/丢帧及无QPC拒绝关联；完整792项离线回归通过（run-d81365d8a8df），无失败/错误/跳过/后台异常、源码稳定。

入口：`C:\Python312\python.exe tools/review_performance_recordings.py --latest 3`。
在本仓库根目录执行，先同步现有闭合启动器记录，再生成
`diagnostics/performance-review/<UTC>/REPORT.md` 与 `report.json`。
不会启动游戏、PresentMon、ETW、采屏或联网；历史资料只读。

## 数据边界

- 使用已接收的闭合记录包，读取前后核对来源SHA、大小与身份。
- 当前只接受固定PresentMon 2.6.0二进制指纹、v1模式、Windows dota2.exe原生QPC记录。
- 必须正常收尾、退出码0且无ETW丢失警告；资源上限导致的正常部分记录单独标记。
- 比赛号、game_session_id、record_session_id必须明确一致，不按UTC猜比赛。
- 只取相邻、游戏前台、GAME_IN_PROGRESS且GSI来源年龄不超过5秒的上下文窗口；间隔上限2.5秒，QPC与单调时钟误差不超过0.25秒。
- 提交/显示间隔须整个落在合格窗口内；窗口边界长间隔会被排除。因此“无>100ms”只指保留的间隔，不能证明整局没有卡顿。
- 无合格窗口显示未知；不同PID/swapchain/runtime/layer/frame_type保留为独立流，不选择或混合主流。

## 指标

提交FPS和显示FPS分别计算；显示间隔仅使用明确Dropped=0且有显示时序的记录。
平均FPS为1000/平均帧间隔；1% low为最慢ceil(样本数×1%)帧间隔均值的倒数；P99为最近秩百分位。
“覆盖秒”是合格上下文窗口总长，并非显示帧间隔累计时长或整局时长。
GPU耗时不作GPU利用率，旧记录没有助手关闭对照，不能推断助手造成的帧率损失。

## 本次已有资料结果

证据：`diagnostics/performance-review/20261026T105935248544Z/`。
三个闭合启动记录包含四场比赛，来源进出复验通过：

| 比赛 | 合格窗口秒 | 平均显示FPS | 显示1% low | 显示P99 ms | 保留显示间隔>100ms |
|---|---:|---:|---:|---:|---:|
| 9061604287 | 2519.7 | 111.83 | 46.36 | 18.8666 | 0 |
| 9061085056 | 2691.7 | 101.41 | 42.16 | 20.8350 | 0 |
| 9060953322 | 3520.4 | 112.60 | 47.15 | 18.5406 | 0 |
| 9061031426 | 2111.0 | 90.90 | 38.24 | 23.0367 | 0 |

这四场不是控制变量实验，不凭场次差异判断CPU/GPU方案优劣。
完整离线回归507项通过，无失败、错误、跳过或后台异常；回归时源码未变。
证据：`diagnostics/offline-tests/run-98e1a95c49c1/result.json`。

来源迁移指纹见 `presentmon_review_origins.json`；仅迁移离线读取/统计，采集端仍归Foresight。
