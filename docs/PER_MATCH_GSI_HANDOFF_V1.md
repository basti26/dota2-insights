# 不停观察的逐局本人GSI交接 v1

2026-10-26。该协议只交接本人GSI，不能代替装备、BP、小地图、声音和性能通道的闭合。原launcher级交接继续保留，助手未停止时原recorders.json仍不能改为closed。

## 生产与边界

Foresight原SelfStatsRecorder写入线程在主日志成功追加之后，将同一份已经白名单化的记录另存为小型逐局JSONL。GSI回调不增加文件操作、线程、截图或模型计算。目录为：

```
self-economy/self-economy-<UUID>/closed-matches/match_<比赛号>/game_<会话>/
  samples.jsonl
  manifest.json
  COMPLETE
```

必须有比赛号、game_session_id与record_session_id；缺失时不根据UTC补身份。每份记录内run_id/比赛/两种会话均固定，sequence递增。收到第一次明确POST_GAME并写入该行后，对逐局文件fsync，再以独占新建方式写manifest.json，最后写COMPLETE。后续同局消息和下一局均不改已封存目录。只封存已接收样本至首次POST_GAME的区间，不证明从比赛开头开始记录，也不证明游戏提供了所有数据。没有POST_GAME时保留未封存，不以停助手或换局替代赛后证据。

清单schema_version=1、kind=foresight_match_journal、channel=self_economy；携带显式身份、首尾接收UTC、封存UTC、payload固定文件名/字节/行数/SHA256、来源累计丢弃和限额状态，以及other_channels=not_closed_by_this_manifest。COMPLETE为清单原字节SHA256及换行。recording_integrity=complete仅表示该发布边界未报告累计丢弃/限额，不是全局覆盖或金额准确认证。上游累计异常保守保留为gaps。

独立交付副本上限 64 MiB、最多 128 个身份，单行最多 8192 字节。失败的写入按尝试量保守计入容量，累计错误和已发布数分别记录。单局失败只关闭该局的发布路径，主经济记录和仍在预算内的后续比赛可以继续。发布前重新核对实际日志的字节与 SHA，最后原子发布 `COMPLETE`。封存比赛锁在新目录创建前检查，Insights 在读取清单及正文前再次核对路径中的比赛号。

## Insights读取

已有命令tools/sync_helper_records.py额外扫描本机helper_history及helper_releases的原生逐局目录，输出native_match_journals；原启动器索引状态仍独立报告。原生持久索引位于diagnostics/native-match-handoff/index.json，不写入旧helper-handoff索引，不可把两种package_id混用。已接入同一份内容报告；tools/review_recordings.py可一次完成两种同步与复盘，见[RECORD_REVIEW_PIPELINE.md](RECORD_REVIEW_PIPELINE.md)。

```
python tools/sync_helper_records.py --latest 5
python tools/review_match_journals.py --folder <已封存game会话目录>
```

match_journals.verify验证路径与身份、UTC、终态、SHA、长度、行数、行边界、逐行身份/sequence及健康状态。open_rows进出时复验，最多读取清单允许的字节；追加的新行不被读入，退出后迭代器不可继续。数据变化则本次分析失败，不沿用之前的成功检查。review_match_journals只输出本人金额状态、英雄计数和最后一条reported GSI原值及原时间，下降不平滑；不把partial/estimated冒充reported，不以这些记录评估图像准确率。

有界发现优先挑选带普通非符号链接 `COMPLETE` 文件的目录，再按目录修改时间排序。标记仅用于排列候选，仍须按原协议检查显式身份、封存锁及封存摘要。索引优先收录通过核验的独立包，失败或重复时最多额外核验两个候选，每轮总上限 128，达到目标数量即停止。剩余位置可显示阻塞项，`blocked_candidates_omitted` 记录核验失败后因索引容量未逐项列出的候选数。超出本轮预算的候选保持未核验，目录时间仅用于排序。

所有可接收包仍标ready_with_gaps，因为其他通道未由本清单封存。后续需要逐项推进共享通道的闭合边界和来源绑定，不能仅扩大COMPLETE的声称范围。

原生简报复用 SelfEconomyAudit 检查估值快照、分项与德鲁伊合计来源，保留 reported、estimated、partial 状态和不可比原因。新记录用 `valuation_receive_status` 与 `display_economy.valuation_binding` 标明是否取得同次接收的估值快照。旧记录只可按提供方时间戳核对，同一秒可能包含多次接收。画面可见性需要独立证据。详见 [SELF_ECONOMY_AUDIT.md](SELF_ECONOMY_AUDIT.md)。

## 独立进程证据

本机用合成比赛12345/12346验证，完全不连接游戏或网络。真实SelfStatsRecorder线程发布第一局后继续接收第二局，producer_status显示accepting=true、writer_alive=true、shutdown_complete=false。随后以Insights独立CLI读取第一局成功，再释放生产进程并正常关闭。两个仓库没有互相导入源码。

夹具及生产收尾状态保存在Foresight diagnostics/match-journal-interop-20261026/；仅是生成数据，不是实战采样或准确率证据。原始截图、录音、录像没有复制或改写；未重建EXE，下一次游戏记录需使用含这些更改的新源码。

最终Insights345项完整离线回归通过（run-7ddb3712ba0f），助手1528项完整离线回归通过（run-f52b1e34f216）；失败/错误/跳过/后台异常均0，回归期间源码未变。覆盖原生包身份/终态/SHA/配额/封存锁、变化后拒收、退出后停止读取、只能读取清单边界内行，以及主记录线程不停机交付；不代表真实赛后GSI到达已验证。
