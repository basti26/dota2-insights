# 装备观测逐局交接 v1

2026-10-26。独立模块observation_handoff验证Foresight MatchRecorder写入线程发布的装备观测清单，无跨仓库导入。不等待整个助手退出；仅声明本装备通道的提交截止，不代替其他通道收尾。

## 清单与边界

元数据在matches/sessions/run_<会话>/observation-handoff.json，最后的observation-handoff.COMPLETE存该文件原字节SHA256。schema_version=1、kind=foresight_observation_handoff，固定比赛/game_session_id/record_session_id、源端POST_GAME的UTC、封存UTC、截止source epoch和accepted_observations_at_cutoff。

Foresight先由已有赛后机制退休source epoch，拦住在途旧识别提交；submission_lock固定数量并拒绝后续同会话装备submit；唯一写入线程等这些已接受作业完成后再发布。可以同时处理下一局队列，所以cutoff_jobs_drained=true只指本会话的截止作业，不要求整个进程pending=0。

payload只允许match_<该比赛>/run_<该会话>/observations.jsonl，声明精确字节、行数和SHA。它仍在原地，没有复制图片或正文。早期unbound行不回填比赛标签，可能使bound行数少于接受数，保留gaps。payload=null明确无可交接观测，不补0准确率。录音、BP、小地图、性能、buff和阵容不由此清单关闭。

health范围是writer_cumulative_at_publication_not_per_match，健康缺口不能直接归因为本场损失；但不能因缺口来自别场就抹掉。完整性字段只表示文件/队列边界的状态，不是英雄/物品识别真值。图片的文件名和SHA格式受检查，实际像素文件仍须独立验证，review固定images_verified=false、accuracy_verified=false。

## 读取

```powershell
python tools/review_observation_handoff.py --diagnostics-root <已配置历史的diagnostics根> --session run_<会话>
```

CLI通过history.checked_input限制来源根。verify先读取有界16KiB身份元数据，再按公开比赛锁拒绝封存比赛，随后才读正文。固定目录/正文名，拒绝符号链接、目录联接、越界、未排空截止、行尾/字节/行数/时间/身份/SHA矛盾；上限128MiB、10万行、每行1MiB。

open_rows进入与退出复验，并可固定expected_package_id，不能把旧验收当永久许可；读入仅限清单字节，退出后迭代器不可继续。来源变化则结果失败。review给出观测状态、英雄和已知/未知格的日志计数，不把accepted或known数量算成准确率。

独立单包读取继续支持；现已接入observation_index的自动发现、持久索引和日常联合报告。源码helper_history及免安装版helper_releases按固定目录查找，索引位于diagnostics/equipment-handoff/index.json，与GSI原生索引分开；使用时固定本装备包SHA，不拿GSI索引授权读装备资料。tools/sync_helper_records.py会同步它，tools/review_recordings.py一次完成新旧记录复盘。旧launcher包处理保持原流程，不将旧recorders.json伪改closed，不倒写旧资料。

报告将装备与本人GSI按比赛号/game_session_id/record_session_id明确关联，同一时间但会话不同不关联，没有GSI不补本人经济。重复manifest包去重，与关闭启动器的同一装备日志范围重合时标注also_in_closed_launcher_scope，不重复累计。路径、身份或正文在索引后变化即拒绝本次内容报告；空payload保留无观测，不当作识别全对。

原生装备现已接入与旧格式共用的大件挑样和SHA/几何裁片。统一报告提供正文/行SHA绑定的item_review_priorities，tools/prepare_record_item_review.py验证当前装备索引后输出原图格裁片与contact.png。候选名也必须与原槽位一致，缺图/缺几何不猜补，图片准备不视为身份正确。详见[CLOSED_RECORD_REVIEW.md](CLOSED_RECORD_REVIEW.md)。

## 双进程生成夹具

Foresight真实MatchRecorder线程写一张生成的24×48像素图和合成观测，封存12345后接收12346会话；producer-ready.json表明writer_alive=true、shutdown_complete=false。此时以Insights独立CLI读取第一局，consumer-live.json返回一行且images_verified/accuracy_verified=false；之后才释放并正常关闭生产进程。两边只传数据路径和会话，没有互相导入源码。

证据在Insights diagnostics/equipment-handoff-interop-20261026/，生成脚本保存在Foresight diagnostics/equipment-handoff-interop-20261026/produce.py。不是用户比赛，不是截图或物品识别测试；真实赛后GSI到达/切局仍待游戏验证。

最终Insights380项完整离线回归通过（run-34dc7ab885c7），助手1533项通过（run-d31ce383df43），无失败/错误/跳过/后台异常、回归期间源码未变。专项包括满队列截止、等待慢作业、不封存就绪前的文件、同会话退役/新会话继续、发布器异常不杀主写入、空通道、身份/帧时钟/SHA/尾行/健康缺口、锁定比赛正文前拒读及分析后变化拒收。

自动接入后，Insights393项完整离线回归通过（run-fe22c3a5fbfa），失败/错误/跳过/后台异常均0且源码未在回归中改变。另用上节真实记录线程输出的生成数据走完整一条命令流程：在隔离分析目录自动发现1个装备包、输出1份装备报告，没有原生GSI就保持缺失。证据diagnostics/equipment-report-interop-20261026/verification.json及isolated-project/diagnostics/record-content/20261026T082808861840Z/REPORT.md；正式本机历史实际执行报告在diagnostics/record-content/20261026T082835716251Z/，3个旧闭合包，新原生GSI/装备均未发现。不将夹具冒充实战。
