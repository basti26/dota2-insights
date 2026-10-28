# 独立小地图采样收尾交接 v1

2026-10-26。小地图采样器是独立进程，基础契约不要求主助手GSI。当前交接正常结束的单次run，不把它改称“某局比赛记录”，也不按启动器时间窗口关联比赛。2026-10-27新增可选[帧来源上下文扩展](MINIMAP_FRAME_CONTEXT_V1.md)，基础身份未知和旧兼容读取保持。

## 生产边界

Foresight的dota2_minimap/recording.py先退出samples写入上下文，关闭RecorderEvidence的四个辅助通道和采集源，发布最后session.json，然后调用closed_handoff.publish。源码会话必须closed=true、complete=true、limited=false、error=null，结束原因只能user_stop/source_exhausted/layout_changed；辅助文件句柄全关闭、pending_images为整数0。达到时长/容量上限、异常退出及未闭合进程不发布成功标记。

单包元数据位于minimap-learning/closed-runs/run_<32位ID>/record-handoff.json，最后发布record-handoff.COMPLETE，内容为元数据原字节SHA256及换行。独立目录不会改变原run内文件或session.json的物理字节统计。发布失败不重写已闭合源文件，closed事件单列handoff错误，缺标记不能被接收端当成功。不能覆盖已存在的收据。

schema_version=1、kind=foresight_closed_minimap_run。保留run_id、可空chain_id、开始/结束UTC、关闭原因、session.json的字节数及SHA，固定五条可选流的字节数/行数/SHA及missing_at_close：

| 流 | 最大字节 | 含义 |
| --- | ---: | --- |
| samples.jsonl | 128 MiB | 接受的样本及采集状态，必须存在，可为空 |
| capture_attempts.jsonl | 64 MiB | 每次尝试的结果/时钟 |
| context.jsonl | 8 MiB | 独立顶部旁证元数据 |
| rejected.jsonl | 8 MiB | 被拒绝ROI的诊断元数据 |
| markers.jsonl | 512 MiB | 未认证匿名候选观测 |

每流最多72000行、每行1MiB；session最多64KiB、交接清单16KiB。不存在的流明确列出，不补作成功的0条。已关闭的辅助通道可以有缺口；evidence_complete仍保留原值，文件完整性与采集覆盖分别表述。

match_id固定null、match_association固定unknown_no_time_inference、scope固定closed_standalone_run_not_whole_match。图像只保留正文声明的引用，不代表PNG、ROI或英雄身份已经核验。顶部旁证与小地图分别采集，不能合成“同一帧”。

## 字节预算

交接元数据在写入前检查剩余总预算。原session的bytes_saved/total_bytes_saved仍描述原run内容；额外收据在独立closed-runs目录。worker最后closed事件和UI恢复后的合计包含额外实际文件字节，连写入失败的部分收据也计入；自动布局重启的链累计预算沿用这个合计，不丢失新增容量。没有在持续截图循环中增加哈希或识别工作，收尾哈希在原采样停止后进行；真实收尾耗时尚未测量。

## 独立接收

```powershell
python tools/review_minimap_handoff.py --diagnostics-root <已配置历史diagnostics根> --run <32位ID>
```

接收器dota2_insights/minimap_handoff.py不导入Foresight代码。先检查有界交接元数据/完成标记及公共封存锁，校验源session的SHA和关闭状态，再读固定日志路径；路径越界、符号链接/目录联接、错run、身份注入、乱序样本、帧数不符、正文/元数据变化、缺失流后来出现均拒收。

open_rows进入和退出重验，可绑定expected_package_id；退出时关闭尚未遍历完的文件游标，旧迭代器不能续读。报告只给实际样本/尝试行数、原采集UTC及结果计数，保留来源evidence_complete；不认证图像、身份、地图标定、整局覆盖或消失预警。

## 生成数据证据与后续

diagnostics/minimap-handoff-interop-20261026/包含生产脚本和独立消费证据。生产端真实learning_worker使用模拟采集源生成两帧相同像素，生成一张复用PNG及两次采集尝试，正常关闭并发布清单；独立Insights CLI和核心读取器成功核验，session原SHA不变。没有运行游戏、采屏、录音、联网或使用真实比赛身份；这是管线证据，不是实战检测质量证据。

自动目录索引和联合报告已接通；后续可选帧扩展按明确三身份关联原生GSI，不晋升整个run身份，详见MINIMAP_FRAME_CONTEXT_V1.md。旧历史没有本清单时不得补写伪造收据或升级为新格式；继续走旧的闭合启动器门禁。未修改旧EXE，真实采集/布局重启/性能仍待游戏验收。

最终432项Insights完整离线回归通过（diagnostics/offline-tests/run-b0cc0d098cfa/result.json），助手1547项通过（run-d8ec018f93d8）；无失败/错误/跳过/后台异常、源码在回归中未变。新增专项覆盖关闭门禁、绑定未知、部分写入计费、收据不覆盖、源文件/流变化、计数和序列矛盾、封存身份元数据先拒读、退出后迭代器不续读等。两仓库git diff --check通过。

## 自动索引与联合报告

tools/review_recordings.py会先同步旧包、本人GSI、装备、BP及小地图run，再生成同一份REPORT.md/report.json。分别调用tools/sync_helper_records.py和tools/review_closed_record_content.py也可。新索引位于diagnostics/minimap-run-handoff/index.json，kind=closed_minimap_run_index；与其他格式互不代替。

只扫描已配置helper_history根下minimap-learning/closed-runs/run_*/record-handoff.json，以及helper_releases根下已知helper-*/economy-*目录的Dota2Helper/Dota2Economy/diagnostics相同路径。固定布局之外不递归扫描；最多100个release、10000候选，原生latest为1–128，联合入口受旧格式1–25上限约束。文件mtime只用于排序，不冒充采样UTC。

索引只保存根标识、相对路径、包SHA和run/chain/关闭时钟等元数据，不复制日志或PNG。相同run和相同包SHA去重；相同run出现不同有效收据时整组blocked，不挑一份继续。索引原子替换，失败保留上次文件、清理本次临时文件及锁；遗留未知锁不擅自删除。

报告读取时重新校验期望包SHA、源session/五流及索引身份；索引后源文件改变或重新封存，拒绝本次报告，不提前生成成功目录。此前已存在的报告不代表本次成功。缺索引为missing_not_treated_as_zero，已扫描但没有包才是0项，未就绪包单列skipped；合法空samples文件与缺失辅助流区分。

与已闭合启动器包引用同一根/日志路径的流逐项标记streams_also_in_closed_launcher_scope，不能把重复行累计成更多样本。即使同一旧包还列了比赛号，新run依然match_id=null；不按启动器范围、相近时钟或相同chain_id绑定比赛。

最终443项Insights完整离线回归通过（run-9298be40a060），无失败/错误/跳过/后台异常、源码未在回归中变化；本批助手运行代码未改，沿用上批1547项结果。11项新增索引专项覆盖源码/便携版、重复/冲突、路径/类型/身份篡改、索引后变化、缺标记、空采样、原子失败及旧流重合。

diagnostics/minimap-report-interop-20261026/verification.json记录既有真实worker生成资料的自动发现→索引→联合报告：1个run、2条样本、2次尝试，源文件SHA/mtime均不变，使用隔离分析目录和显式生成资料根，未纳入正式历史配置。正式--latest 3的复核报告为diagnostics/record-content/20261026T094919589265Z/REPORT.md：3个旧闭合包ready_with_gaps，正式新格式小地图/GSI/装备/BP均未发现。没有把生成数据当实战记录。
