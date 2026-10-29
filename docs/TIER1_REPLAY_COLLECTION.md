# 最新Tier 1赛事录像

## 最近失败样本与诊断（2026-10-29）

本次只读Pi检查：6场下载verified，其中5场完成解析，另1场partial_or_failed；本机没有新闭合实战包。失败场在tick 80412发生BufferReadError，清单complete=false，解析比赛号与独立footer不一致，仅匹配完整编号低32位。低32位相等是线索，不能改编号放行，也不能据此认定底层错误已修复。临时导出的74眼未计入当前5场478眼；旧work和失败日志保留，不重排或自动重跑该录像。

本地代码已补失败留痕：`extract_replay_wards.terminal_status`将内部parse_error情况下的replay_eof改为parser_stream_error，成功EOF行为不变；不再让错误看似正常到尾。`pi_ward_worker.failure_summary`只读有界小清单，保留原生退出码、错误类型、最后tick、完整性/身份标志及低32位线索，写入catalog的analysis_failure与worker_status。它始终diagnostic_only且eligible_for_analysis=false；不会接收部分眼位或覆盖独立身份校验。进程未结束时不读取正在写的清单；缺失/超大/坏JSON/跨比赛清单独立标注，不把异常原文或路径塞进公开原因字段。

这些诊断修复尚未部署Pi，远端仍运行原版本。已有失败元数据的离线演示见`diagnostics/goal-input-check-20261029/9066273901-structured-failure.json`；其SHA只绑定规范化的本地元数据副本，不是原Pi清单字节；旧worker没有保存本次原生退出码，因此演示明确标为未知，不冒充原生退出收据。原始只读检查和日志尾部保存在同目录9066273901-work-inspection.json。

5项专项覆盖真实worker失败分支的生成子进程替身、未接收部分导出、状态落盘、有界/未结束/错误字段及EOF差异；完整797项离线回归通过（run-2e4166165161），0失败/错误/跳过/后台异常、源码稳定。没有实际新开解析进程、修改远端服务、下载DEM、操作游戏或提交。

进一步包级定位：只读完整遍历68181个外层帧头/范围，tick80412的5332字节内层包单独拆分即复现原错，不需重跑实体状态。两种Snappy输出一致；原快读、原逐字节读和独立标量位读，前36条全部消息的边界/载荷SHA聚合及第37个失败头相同。该头声明1205837字节而只剩4398个完整字节。当前证据只能定位为固定格式下内层长度异常，不能证明一定为服务器坏文件或排除协议差异；不跳包、重同步或把部分结果当完整。证据`diagnostics/replay-framing-20261029/REPORT.md`、`reader-comparison-v2.json`，不含原始载荷或实体状态。本轮无生产代码变化，797项对应的332源码/数据指纹逐项相等。

## 内层错误发生时自动留证

新增`replay_packet_diagnostics`，在本仓库眼位导出入口绑定固定gem私有内层分发回调。只有ReplayDataError抛出时才对当时字节做诊断，正常包不执行哈希/逐位探针；原异常继续抛出，由既有parse_error/完整性门禁处理。只保留第一个失败包的一份metadata，最多1MiB/4096条消息头；超限、半截头、长度越界、头部预算分别标注，不跳包或修改读取位置去“修复”结果。

manifest增加failure_packet_diagnostics和诊断模块指纹。只保存包SHA、长度、tick、消息头数量/失败位偏移及长度，不保存原载荷、消息内容或实体状态。worker只复制白名单/有类型约束的小摘要，任意嵌套文本或payload字段不透传；诊断自身失败也不得替代原解析错误。该摘要永远不作为完整分析准入，framing_complete也不认证协议正确或实体处理成功。

真实固定版本ReplayParser的内层分发已在Pi现有失败包上通过临时内存模块验证：自动捕获tick80412、36个头后第37条长度越界，包SHA与此前探针相同，原BufferReadError仍抛出。没有执行整场parse或写入远端程序。证据`diagnostics/replay-framing-20261029/automatic-callback-check.json`。worker摘要/生成位流/预算/异常保持等检查及完整804项离线回归通过（run-14ee1802d7e2），0失败/错误/跳过/后台异常、源码稳定。

当前部署清单已包含该标准库模块，共17文件，清单SHA核对通过；本轮未部署Pi，不能声称远端后台已经启用这项新留痕。也未修复该包底层内容/协议问题、未重试整场，完成统计仍保持5场478眼。

用户2026-10-29确认：只下载最新结束的一局及之后新结束的Tier 1比赛，不回补往期；职业赛事全部可获得录像都收，不占100盘D2PT样本额度、没有每日/终身盘数和原库GiB配额。磁盘仍需保留4GiB空闲，下载仍串行并限速。本人录像原规则保持。

赛事分类依据Liquipedia对应赛事页；不能把职业玩家路人局、Valve premium标签或某Tier1赛事的预选赛直接当Tier1正赛。配置中的tier1_leagues必须带tier=1、Liquipedia URL、明确league_id及精确名称；当前已登记PARI UNIVERSE by FISSURE（20395），来源https://liquipedia.net/dota2/PARI_Universe/1。新增赛事需确认对应编号/名称后加入名单，不凭模糊名称自动升级分类。

Pi配置tier1_enabled=true后，原服务检查最新OpenDota proMatches。仅接受登记赛事ID及精确名称的已结束记录，等待约10分钟且结束不超过24小时。首次以最新可用比赛的结束时间/编号建立持久基线，旧场次排除；之后收比基线新的场次并去重。每轮最多处理3场为工作批次，不是额度；积压可在后续轮次继续。当前来源是公开最近比赛列表，不声称覆盖未出现在该列表的比赛。

先GET /matches/{id}核对比赛和leagueid；无replay_url时GET /replays?match_id={id}构造Valve地址。仅允许既有Valve域名/该比赛路径，无重定向；复用压缩/解压资源上限和完整性校验，核对DEM footer比赛号和SHA后才标verified。地址暂缺时10分钟后继续查；临时网络错误和HTTP 403/404/408/425/429/5xx在10分钟至1小时间退避重试。已发现的新局保留到重试队列，即使退出最近列表也不丢弃；仍仅处理结束24小时内且不早于启用基线的比赛。身份、地址白名单和文件校验失败仍需人工处理，不冒充成功。每次循环有失败日志，不绕过HTTP封锁。

管理命令：`python tools/manage_pi_replays.py status`；立即一轮：`python tools/manage_pi_replays.py collect-tier1`。本机连接配置config/pi_replay_library.local.json不入Git。服务不依赖Windows开机；录像仍保存原录像目录。单个Pi服务内下载与分析各用独立线程，下载串行、解析也串行，互不等待；不增加第二个服务。空闲时约10分钟查询一次，最新批次积压时继续分批收集。

部署前核对无活动解析/待处理队列，备份程序、配置与服务文本，按静态闭包核对SHA及隔离导入后启动服务；失败恢复原程序配置。已有录像/报告不删除。职业赛事在目录来源标记tier1并单列显示，不混入高分路人样本统计。


2026-10-29后台修复：恢复旧版因临时HTTP错误进入manual_required的新局；只恢复明确可重试状态，不重试身份/来源校验失败。首次源码部署、既有基线和录像不重建，不回补启动前旧比赛。后台异常保留HTTP状态，解析占用不阻塞新局下载。


## 官方替代来源复查（2026-10-29）

已完成一次隔离来源获取尝试，结果为HTTP 404，未取得可供哈希对比的新副本。因此不能判定“重新下载相同”或“官方文件损坏”，也不据404认定录像永久过期。原录像、失败工作目录、原完成统计均未改动，未启动解析或替换canonical。结果见`diagnostics/replay-framing-20261029/official-source-comparison.json`。本次单次尝试已终止，不在没有新依据时反复下载/重跑；后续仍需独立有效来源或协议证据，保持解析失败、排除临时74眼。


## 成功恢复后的当前故障状态（2026-10-29续）

最新只读Pi清单登记7盘职业局：6盘下载verified，5盘完整解析合计478眼；9066273901仍partial_or_failed，9066349307为HTTP404后的waiting_retry。四盘complete行仍保留旧HTTPError/TimeoutError原因，说明旧版成功后未清除当前故障字段，不能据reason非空认定这些完成结果失效。清单证据diagnostics/professional-review-20261029/catalog-check-latest/catalog.json及summary.json；本次没有验证服务进程存活，不把配置enabled或清单状态当实时进程证据。

本地修复：Tier1/D2PT下载成功后pending更新清除reason/http_status/retry_after_epoch；完整分析经过原身份/完整性/摘要门禁接收后，complete更新清除这些当前字段及analysis_failure。累计network_retries、原下载收据、失败工作目录和原文件不删除或改写。验证失败时不清除已有故障，不把部分导出标成成功。生成数据先复现两处状态问题，修复后12项职业收集专项通过（run-87f3371f8128）。

17文件本地部署闭包已重新核对SHA，计划在上述目录local-deployment-plan.json；此次未部署Pi、未改远端清单/原数据、未重试下载或重跑解析，旧远端仍可能显示历史错误。本次无新完整分析，不重复跑地图或增加眼位统计维度。

最终完整822项离线回归通过（diagnostics/offline-tests/run-0268146f4fd9/result.json），0失败/错误/跳过/后台异常、335覆盖源码/数据SHA一致。
