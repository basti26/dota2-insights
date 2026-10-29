2026-10-29提交检查：清理四个新增测试文件的末尾空行；最终848项完整离线回归通过，0失败/错误/跳过/后台异常，337份源码/数据SHA复验一致（run-fac4ff6bc1e4）。暂存差异检查通过，不包含个人记录、本机配置或构建产物。

2026-10-29：修复眼位闭合清单缺损失字段仍可能标完整的问题：dropped必须非负整数，limited/shutdown_timed_out必须显式布尔，images_saved必须0–80整数（拒绝bool）；正文准入前拒收，未发布元数据诊断仍独立。新增反例先复现9项失败，修复后26项专项与848项完整离线回归通过，337份源码/数据SHA一致；旧v1 PNG与v2 JPEG两份真实生产的生成像素包继续准入。未开游戏/设备/外网/改Foresight/提交，不认证地图真值。证据diagnostics/ward-camera-storage-20261029/strict-loss-compatibility.json与diagnostics/offline-tests/run-ae4c6472af66/result.json。

2026-10-29：眼位自动留证升级v2，原尺寸JPEG90/每60秒留图，定位仍用原始帧；旧v1 PNG兼容。三闭合UI段129张2K PNG共628.20MiB，固定18张测算JPEG90中位约0.84MiB；旧PNG预算模型约6分钟触顶，新默认80张在样本最大值模型下约90MiB，不保证整局或FPS。成功定位、无参考、初始化失败三条生成数据跨进程链均通过；Foresight1654项、Insights846项完整离线回归通过，530/337份源码指纹复验一致。未开游戏/真实采集/联网/重建EXE/提交推送；真实地图与性能仍待自然记录。见docs/WARD_CAMERA_EVIDENCE_V2.md。

2026-10-29：补齐眼位留证未发布状态诊断：没有CLOSED时只读最多latest份/单份128KiB公共session元数据，声明失败/未收尾/缺收据/元数据未知分开；不读事件/图片、不认证进程存活或用未核验数量填零。封存前置/安全错误类型/省略数量/收据发布竞争已测，报告以中文显示，多段摘要表与详情分离。20专项及842项最终完整离线回归通过、337源码/数据SHA一致；Foresight1650项对应530文件不变。真实worker初始化失败分支以模拟OSError生成闭合失败状态，在独立日常报告识别，正文哈希哨兵确认未读取；有定位与无参考生成链也以当前reader复验。未开游戏/真实采屏/音频/联网或提交，见docs/WARD_CAMERA_EVIDENCE_V1.md及diagnostics/ward-camera-interop-20261029/unpublished-analysis-verification.json。

2026-10-29：眼位自动留证深入到内部一致性：投影尺寸/有限点/唯一ID/红绿标志/省略数核查；定位→surface按原帧+比赛+双会话+GSI来源ID唯一先行事件配对，不按UTC猜、重复/未来/缺失保持未知。shown无可靠定位/投影等标记录矛盾，不认证OS/地图；图像配准残差缺失为null，不冒充真实落点误差或显示时延。生成20/10平移纹理用真实Foresight ORB/RANSAC/投影/poll/写入，4事件2PNG在独立日常报告成功关联1回执，控制点到219.903/310.007（非实战误差）。14项专项与836项最终完整离线回归通过、337源码/数据SHA一致；Foresight1650项对应530文件未变，当前reader也复验旧无参考包。未使用游戏/真实设备/外网或提交，见docs/WARD_CAMERA_EVIDENCE_V1.md及diagnostics/ward-camera-interop-20261029/projection-analysis-verification.json。

2026-10-29：用户明确扩展到Foresight后完成眼位自动留证闭环：生产端后台采样、闭合清单/来源身份与预算/失败留痕；Insights新增ward_camera_review，日常generate自动发现源码/便携闭合段，封存前置、冲突整组拒收/副本去重、事件/图SHA进出复验，未闭合不读正文，partial明确有缺口。真实生产循环/写入器用生成像素在独立进程输出2个范围/2条未定位/1PNG，独立日常报告接收通过且不导入另一仓源码。最终830项完整回归通过、337覆盖SHA一致；Foresight1650项通过。未操作游戏/真实设备/外网/Pi部署/EXE或提交，不认证地图真值或整局采样/成本；其余经济/BP/BKB显示证据仍待补，见docs/WARD_CAMERA_EVIDENCE_V1.md与diagnostics/ward-camera-interop-20261029/verification.json。

2026-10-29：Pi职业清单只读更新：7登记/6下载/5完整合计478眼，9066273901失败、9066349307等待HTTP404重试，无新完整分析，不重跑地图。四个成功行仍有旧错误原因，已本地修复成功下载pending及通过验证的分析complete后清除当前reason/http/retry/analysis_failure，累计次数与原失败文件保持；拒收时不清故障。生成专项12项和最终822项完整离线回归通过（run-0268146f4fd9），0失败/错误/跳过/后台异常、335覆盖SHA一致；17文件静态部署闭包核对。未部署Pi/改远端数据/下载DEM/重跑解析/开游戏或提交；未验证远端服务进程存活，见docs/TIER1_REPLAY_COLLECTION.md。

2026-10-29：继续修正装备—录像判定：生成7例复现缺ID相等、重复格覆盖、occupied无ID、empty带ID矛盾和item_前缀差异。统一有效ID/格状态检查，保留冲突/未知；整体物品多重集只在双方九格恰好唯一且已知时可比，最近可见的子集判断在参考不完整时为null。16项专项与820项最终完整离线回归通过（run-81e0c00303f9），0失败/错误/跳过/后台异常、335源码/数据SHA一致。未修改原资料、实战识别/金额、Foresight或设备/网络/提交；不据生成回归声称识别准确率提升，见docs/CLOSED_RECORD_REVIEW.md。

2026-10-29：装备—录像自动复核的真实缺口修复：三旧入口先以有界小清单对声明/解析身份各做封存锁检查，再读正文；经济对照拒收缺失/不一致解析比赛号。最近可见库存九格一致只在双方九格齐全、唯一且已知时给布尔，缺格/重复/未知返回null，保留部分已知格比较。9项生成数据专项及813项最终完整离线回归通过（run-d50c2c00124d），0失败/错误/跳过/后台异常，335覆盖文件SHA一致；初次临时目录沙箱权限失败已按既有离线授权重跑解决。未读真实封存正文、未改识别金额/阈值或旧资料、未改Foresight/开游戏设备/联网/提交；见docs/CLOSED_RECORD_REVIEW.md。

2026-10-29：自然取样工作方式复查：源码生产端仍缺眼位镜头/投影及部分实际surface证据，不把Insights消费能力当已采集。本机七通道重新有界同步（latest=3）均无新增包；不重算相同资料冒充质量进展。此前隔离官方来源尝试已结束为HTTP404，未取得可对比副本、未替换canonical或重启解析，仍5场478眼，失败局排除。见diagnostics/automatic-evidence-check-20261029/input-check.json及diagnostics/replay-framing-20261029/official-source-comparison.json。本轮仅证据/当前文档收口，无生产源码变化、游戏设备操作、Foresight修改或提交。

2026-10-29：失败包诊断程序化：本地replay_packet_diagnostics接入眼位导出固定内层回调，仅ReplayDataError时记录第一份≤1MiB/4096头元数据，正常路径不探针、原异常保持；worker白名单摘要，不存载荷/实体。真实Pi固定ReplayParser对已定位小包的临时模块回调验证成功（同SHA/第37条越界），无整场重跑或远端部署。17文件静态闭包已核验；完整804项离线回归run-14ee1802d7e2通过，0失败/错误/跳过/后台异常、334覆盖文件SHA一致。见diagnostics/replay-framing-20261029/automatic-callback-check.json及docs/TIER1_REPLAY_COLLECTION.md；底层错误未修复、完成统计5场478眼，未改Foresight/游戏设备或提交。

2026-10-29：第六场失败包进一步只读定位：源109533921字节SHA一致，68181外层帧边界完整；tick80412第40282外帧内5332字节数据，前三十六条内消息可拆，第37头声明1205837字节但仅余4398完整字节。两种Snappy输出相等，快/慢/独立标量的36条边界/载荷SHA聚合及失败头相等；未解实体、导出原载荷、重跑17分钟解析或改远端程序/原数据。低32位编号可由提前中断后uint32 getter回退路径解释，不改号放行；是否源内容异常或协议兼容仍未定，不跳包冒充完整。见diagnostics/replay-framing-20261029/REPORT.md；无生产源码改动，797项源码/数据指纹仍逐项一致，不重跑未变回归，未提交。

2026-10-29：新输入只读核查：本机七类无新包；Pi第六场9066273901已下载但解析在tick80412报BufferReadError，完整比赛号不符、低32位相等仅线索，临时74眼不计入5场478眼。已本地修复内部错误仍标replay_eof的诊断语义，并使worker保留有界错误/位置/身份/原生退出码，生成失败分支验证拒绝部分导出；未修复底层解码、未部署Pi或重跑录像。旧worker未存原生退出码，元数据演示明确未知且SHA只属本地规范副本。797项完整离线回归run-2e4166165161通过，0失败/错误/跳过/后台异常、332源码/数据指纹复验；职业run-v5依赖仍一致。见diagnostics/goal-input-check-20261029/REPORT.md及docs/TIER1_REPLAY_COLLECTION.md；未动远端/原数据/游戏设备/下载DEM或提交。

2026-10-29：本轮范围收口：逐项审计经济/装备/BP/小地图/声音/性能/职业冻结产物，792项回归覆盖的331份源码/数据与现文件SHA全相等。旧职业run-v4有7项全局代码指纹差异，以同5场冻结资料在现代码完成run-v5六阶段复跑、46产物SHA复验、profile语义相等，resume不重算。TASKS顶部收敛当前事实/缺口/顺序，原历史逐字保留并记录前后SHA；不把旧缺资料/Pi未部署当现状，也未勾掉质量未知。证据diagnostics/goal-scope-audit-20261029/REPORT.md；整体目标仍未证明完成。本轮无源码改动，未联网/开游戏设备/改Foresight/重解析DEM/发布或提交。

2026-10-29：收拢待办后补齐Terra P5长帧位置：原性能CSV同一解析过程有界保留5条、QPC/提交显示区间/流身份分开、无QPC不给关联。真实三包各5条，原CSV15行独立核对、原包进出复验和旧逐流统计相等；最慢合格单次间隔43.0806/52.1062/58.3883ms，不代表持续FPS或排除BP阶段卡顿。识别/声音QPC桥和助手因果仍未知。792项完整离线回归run-d81365d8a8df通过、0失败/错误/跳过/后台异常、源码稳定。证据diagnostics/performance-slow-windows-20261029/REPORT.md及docs/PERFORMANCE_REVIEW.md；未启动ETW/游戏设备/联网、未改Foresight或提交。两份原始专项Downloads路径仍缺原文。

2026-10-29：装备稳定性报告新增证据单位/角色别名/同裁片跨源行关系与有效行号/SHA门禁；保留38角色、25源行、25裁片字节、26参考目标字节对，12角色别名组、1同裁片跨2源行组，不合并不同时间观测或当独立试验。实际probe-v3与v2输入SHA及逐条分数完全相同，不改识别门槛/金额。787项完整离线回归run-ca3f4538c746通过、0失败/错误/跳过/后台异常、源码稳定。见diagnostics/item-stability-20261029/probe-v3/REPORT.md及docs/ITEM_CROP_STABILITY.md；未读原采集/改Foresight/联网/操作游戏或提交。

2026-10-29：小地图来源范围诊断进入详细/日常报告，区分本次索引未选中、未就绪、已核验匹配范围，不将零配对解释为没采集。相同三run12010帧以3份/有界25份GSI扫描对照，旧两段恢复1527/767精确来源配对及1278/698可用位置帧；本地未知比赛号一段不补归属。不是新采集或像素/地图真值，缺来源ID不按时间猜、不提生产频率。785项完整离线回归run-3d538dfbc1e0通过、0失败/错误/跳过/后台异常、源码稳定。证据diagnostics/minimap-scope-review-20261029/REPORT.md与docs/MINIMAP_GSI_SOURCE_ALIGNMENT.md；未改Foresight/操作游戏设备/联网或提交。

2026-10-29：补齐Insights此前遗漏的panel-visibility前缀消费：v1字段/UTC/原因与布尔一致性核查，缺流/空/无效及旧模式未知分开，明确BP结束阶段shown行仅待查，不认证OS/应用或按revision猜关联。实际有界同步12、准入9前缀均缺显隐流；生成前缀尾部边界已测。782项完整离线回归run-ccd1db4dcd89通过，0失败/错误/跳过/后台异常、源码稳定。证据diagnostics/bp-visibility-20261029/REPORT.md与docs/BP_DIAGNOSTICS.md；未改Foresight或游戏/设备、未联网或提交。

2026-10-29：Pi只读确认第五场Tier1完成，统一入口collect+六分析阶段实际更新累计5场478眼，7重复输入不计；resume各阶段仍一次尝试。53产物SHA和原始478行/24分层/36邻域独立复算通过，开局地图已查看。4/4真眼邻域降为4/5，五场仅两组对阵，不能当效果或独立队伍证据；另1场快照等待重试排除。见diagnostics/professional-review-20261029/run-v4/REPORT-current.md与docs/PROFESSIONAL_REVIEW_PIPELINE.md。仅资料增量、沿用当前776项源码回归；未改Pi服务/分析源码/Foresight、未操作游戏或提交。

2026-10-29：BP各轮记录时序进入自动日常复盘：公开无冲突阵容资格、首条生成、完整视图唯一先行控件回执、记录结束参照分开；同事件不伪造0毫秒，缺钟/倒退/缺回执未知，不认证OS显示或隐藏。真实三前缀六轮均生成；两份无UI流，另一份仅首轮88ms回执，三份均缺显隐流。自然对局补证需求已列，不要求现场重演。776项完整离线回归run-d6cbf478fff8通过、无失败/错误/跳过/后台异常、源码稳定。证据diagnostics/bp-stage-timing-20261029/REPORT-v2.md，见docs/BP_DIAGNOSTICS.md；未改Foresight/联网/操作游戏/提交。

2026-10-29：最新三份自然对局录音完成固定v7/原0.30波形策略盲测，7617.4秒产生17候选，分层准备9段复听且均未审核。新增事后PCM覆盖核查，509个CD采样边沿前后各2秒连续录音，97库内边沿、9邻近候选；不据此认定可闻性或漏识别。空比赛号保持本地身份，旧源/预测/收据/active不改。完整772项离线回归run-eee94c01d6f6通过，无失败/错误/跳过/后台异常且源码稳定。证据diagnostics/audio-latest-20261029/REPORT.md，规则见docs/CLOSED_AUDIO_INFERENCE.md；未开游戏/采录/联网/改Foresight或提交。

2026-10-29：补齐历史图标参考的对照挖掘：新item_transition_review按同包/英雄/格相邻known记录挑同物品/变物品/变空，未知/缺格/时间断链，首次去重抽样后才看分数。既有三包得到47组（45同标签、2升级标签变化），原图核验并查看两组变化；小雷锤→大雷锤NCC0.652高于先前一组跳刀遮挡0.604，不能统一降阈值修复。无变空样本；原文/原图不改，来源/原几何/失败与0观测独立标注。768项完整回归run-5e07c6379014通过；证据docs/ITEM_TRANSITION_CONTROLS.md与diagnostics/item-transition-20261029/review-v2/；未提交。

2026-10-29：转回实战经济质量：重新同步/准入既有三局，6170条原生本人同包金额可比无不符（不与重合旧launcher相加）；两包19个大件中断重备68裁片并查看。视觉观察16数字/亮罩、2聊天覆盖、1近似不变；跳刀归一化纵向3像素配准使同内区NCC0.754→0.995，圣洁吊坠0.970仍unknown。新增固定离线crop稳定性/同包其他标签对照探针，不作准确率、不改金额/阈值或Foresight。761项完整回归run-d98adb289187通过，详见docs/ITEM_CROP_STABILITY.md与diagnostics/item-stability-20261029/probe-v2/；未提交。

2026-10-29：统一职业分析命令run_professional_review.py已贯通可选只读收集→累计→模式/分时段地图→上下文→事件→重复区域，生成index.html。原生退出/收据/产物SHA复用、原始输入/全分析源码指纹、OS并发锁及进程创建身份、失败新attempt保留与不确定启动拒绝重启已实现。真实四局离线六阶段全0，resume不重算，45产物SHA及4链接验证；冻结批次未完成比赛单列，结果complete_with_gaps，不当实时状态。754项完整回归run-00b7616ed748通过。见docs/PROFESSIONAL_REVIEW_PIPELINE.md；未改原数据/远端/Foresight、未提交。

2026-10-29：职业分析跨批累计已实际贯通：新analysis_batch_registry按来源/manifest/wards SHA去重，同场不同版本默认排除且需显式选择；原收据/上下文/读中变化重验。Pi第四局已完成，三场旧批+四场新批累计4场378眼、重复输入3条；再次加入同批仍4场。地图CLI直接读累计快照，v3的24分层/24邻域独立复算及DOM分支检查通过；0–10分钟天辉一真眼邻域4/4场，原10–20假眼热点变3/4，仍不作效果认证。743项完整回归run-3874ee160317通过；原历史/远端服务/Foresight未改，未提交。见docs/ANALYSIS_BATCH_REGISTRY.md。

2026-10-29：职业来源已接通旧赛后事实库与复用分析：tier1独立分组/赛事依据与元数据、封存前置、来源显示修正；新增有界Pi只读快照CLI（不搬DEM、不启动解析/改服务）。真实三场255眼接入，179事件/280指标包/14000值入上下文；严格时钟先拒收，再显式按暂停校正规则投影7包，原字节保留。原重复区域/事件窗口工具均完成；第四局收集时仍解析而未混入。729项完整回归run-2d3b5efe32f0通过，详见docs/TIER1_FACTS_INTEGRATION.md；未提交。

2026-10-29：眼位时间/阵营地图报告已完成：按提取器/已知版本分组，开局前独立参考，天辉夜魇/真眼假眼/每10分钟筛选，完整区间分母与尾段观察分离。三局255眼（开局前17）已生成离线自包含report.html与四分图；10–20分钟天辉假眼一邻域3/3盘、4次，不称效果认证。原始JSON独立复算20分层/16邻域、20个DOM筛选分支通过，完整719项回归run-15bbcd3f5736通过；地图仍旧示意投影、未开游戏/改Foresight/Pi或提交。见docs/WARD_PHASE_PROFILE.md。

# 当前状态

2026-10-29：按用户要求修复Pi持续收集新Tier1比赛：下载与串行分析改为同服务内独立线程，解析不阻塞发现/下载；临时HTTP/网络失败10分钟到1小时退避，已发现的新局退出最新列表仍可重试（保持基线和结束24小时窗口）。恢复旧HTTP失败9065883621，实际重新下载并校验verified，现正analyzing；9065794389已完成74眼解析。备份部署16文件与当前SHA一致、服务active/enabled，未中断活动解析/下载；原100盘与历史资料保留。703项最终完整离线回归通过run-5085d7dfcd30，证据diagnostics/tier1-replays-20261029/background-status-final.json、deployment-background.json。未提交。

2026-10-29：Pi首盘Tier1 9065794389已完整解析（74眼：48真/26假）。新增ward_reference_export与prepare_ward_overlay.py，显式单场完成/比赛身份/wards SHA与数量门禁、全放置不筛选导出、绑定树木人工证据；全部74点树木需求未知，不凭附近有树推断必须砍树。已只读收集并将版本化包交给Foresight被忽略配置，源/目标SHA一致；记录diagnostics/ward-overlay-prototype-20261029/9065794389/handoff-check.json。700项完整离线回归通过run-8c609f0b2f3c。Foresight实验性显示另维护，主画面/地图/树木真值仍未认证，未提交。

2026-10-29：最新Tier1职业赛事收集已部署Pi（16文件SHA与当前源码一致、备份/隔离导入/服务active）。登记Liquipedia确认的PARI Universe，首轮只最新一局、之后新局，持久基线不回补；职业不占原100盘或原库GiB配额，磁盘4GiB余量仍保留。实际首盘9065794389下载70,188,171字节、footer/SHA校验verified，tier1_count=1，D2PT仍100/100；入分析队列未宣称解析完成。696项完整离线回归通过run-ea5ff714774f，证据diagnostics/tier1-replays-20261029/REPORT.md。新赛事需核实ID/名称后加入名单，不声称已自动发现所有未来Tier1赛事；未改Foresight或提交。

2026-10-28：新增小地图本人像素标注导入/独立留出评估模块及evaluate_minimap_positions.py。按导出样本序号/比赛/图SHA绑定手工点，拒绝同局/共享图片混用、重复点号/越界、缺钟/大延迟及退化共线拟合；仅探索局拟合归一化二维仿射并分别给训练/留出误差。没有标签时不造模型/零误差；原始来源、人工身份/地图版本/精确同时性仍未认证。真实两局各12张材料现0标签，输出insufficient_labels，见diagnostics/minimap-position-evaluation/20261028T154708302254Z/REPORT.md。7项新增与689项最终完整离线回归通过run-292bc9907c08，源码稳定。

2026-10-28：新增draft_advice_audit并接入每条BP事件及日常联合Markdown；按推荐当时公开无冲突阵容核对前三候选的关系方向/唯一性/正样本数/有限delta，缺阵营或重复英雄分母未知，缺记录不补0。保存当时缓存/规则/版本/来源SHA，逐阶段首条案例，不用最终阵容或当前缓存回填。实际三份可用BP前缀产生六例，前三关系覆盖均齐，但全为stale/version_unverified；不是推荐质量认证。本机diagnostics/bp-advice-audit-20261028/REPORT.md。最终682项完整离线回归通过run-1878ef9182cc，源码稳定。未联网、改Foresight或提交。

2026-10-28：新增大件中断四阶段裁片模块与现有prepare_record_item_review.py --continuity入口；从已核验原生包重算事件、取known/首末unknown/下一known原行图片，并排HTML显示原状态/候选/时间及日志经济选择。候选为空可取样、图片SHA错标不可用、正文分析中变化不发成功页；不采信可编辑报告内事件标签。实际最近三局生成68张角色裁片（含同帧多角色），已查看一局回音战刃及跳刀冷却覆盖中断图；未认证真值或改实时识别。入口diagnostics/item-priority-review/20261028T153122371790Z/index.html；最终676项完整离线回归通过run-7916f7aa07b7，源码稳定。

2026-10-28：新增生产模块 equipment_continuity 并接入原生装备复核/日常联合报告；自动保留同英雄同格大件 known→unknown→后续known 的原行SHA、首尾未知、候选与原经济选择。缺格/空格/重复格/乱序或大间隔断链，限定180秒与60事件输出，不平滑金额。实际日常最近三局中一局装备0行保持无数据，另两局分别5/14段中断，其中2/9段后续相同物品ID，非准确率或独立真值。报告 diagnostics/record-content/20261028T152618301638Z/REPORT.md。完整离线回归672项通过 run-bc15caa556f0；最初沙箱临时目录权限失败已用既有授权的沙箱外离线运行解决。未改Foresight、联网或提交。

2026-10-28：再次查看已复验25张装备裁片，完成14组视觉观察与单局简报关联：军团同格外观更接近刃甲而候选未计价，待独立真值；BKB与大雷锤若干图像支持，但大雷锤缺无冷却成对图，不能计算识别准确率。详见diagnostics/terra-review-20261028/equipment-visual-review.md；未改源码、Foresight或联网。

2026-10-28：BP六个选人期案例前三名共18候选、108条已公开阵容关系，本机旧缓存均存在并与记录一致，缓存源SHA前后不变；仅针对定向案例，不认证补丁/推荐质量。核对原JSON中文正常，先前终端乱码结论已纠正。见diagnostics/terra-review-20261028/bp-health.md；未联网、改Foresight或提交。

2026-10-28：小鹿局698条可用位置来源配对中抽取12张、独立源包/行/图片SHA进出复验，生成留出局人工标注材料；剧毒探索局与小鹿留出局导出文件名和会话身份分离。尚无人工像素真值，不拟合或报误差。修复大件清单25个路径并验证目标存在；见diagnostics/terra-review-20261028/progress.md。未改生产源码/Foresight，未联网或提交。

2026-10-28：将三局经济旧值保留样本、BP当时推荐与声音盲测入口补入单局简报；新增source-inventory及equipment-review，25裁片按比赛/英雄/槽/候选归并14组并保留原行链接，不作为准确率。小地图12张人工标注仍仅单局探索，独立标注/留出待补。见diagnostics/terra-review-20261028/progress.md。未改生产代码/Foresight、未联网/提交。

2026-10-28：按原生交接复验三局装备选择记录；孽主局虚空大雷锤遮挡后原观测14717，经济选择记录保留20216，屏幕实际显示仍未认证。剧毒局1189条有可用位置的精确来源配对中抽取12张、原行和图SHA复验，生成独立人工像素标注页；未自动标本人或拟合坐标。见diagnostics/terra-review-20261028/progress.md；未改生产源码、Foresight或联网。

2026-10-28：继续固定v7波形盲测，三局预测冻结均完成；小鹿7候选导出4段、孽主1候选导出1段，均待听审。三局BP公开前缀提取6个阶段案例，均使用stale缓存和version_unverified规则；装备25图重新进出复验，小地图12张SHA核验图已查看但本人像素未认证。证据diagnostics/terra-review-20261028/progress.md与bp-health.md。未修改Foresight、联网、提交或启用提醒。

2026-10-28离线推进：复用已完成报告生成三局中文简报；剧毒小地图扩大扫描后1499条精确来源配对，图像身份/标定未认证。剧毒逐局录音固定v7/原波形策略完成盲测、1条mars_spear候选及复听页，未人工听审；BP统计127英雄32004关系，覆盖至10月8日，能力规则补丁未认证。逐图成对、其他两局音频及BP具体案例等仍未完成，见diagnostics/terra-review-20261028/progress.md。662项完整离线回归通过run-857485e9141d；未改Foresight、联网或提交。

2026-10-28提交前完整离线回归：662项全部通过，无失败/错误/跳过/后台异常，源码在回归期间未变。证据diagnostics/offline-tests/run-bd337a895c84/result.json。本批为累计源码、改名及数据交接修复；不包含原始比赛资料或本机配置，旧EXE未重建。

2026-10-28：适配Foresight改名后的发布目录：七处交接/原生索引允许foresight-*下Foresight及ForesightEconomy，同时保留旧Helper/Economy布局；历史配置键、记录协议、SHA/身份/关闭状态门禁不改。六类原生解析路径及共用发现/旧注册发现均有生成目录验证，包含陌生应用/目录穿越拒收；109项相关离线回归通过run-237fbf22294e。未迁移原资料、未联网、未提交推送。

2026-10-28：修复helper_handoff.Inputs.read局部导入遮蔽：首次带game_context_binding的JSONL行不再UnboundLocalError，保留封存拒收门禁；交接/内容报告54项离线回归通过run-797eb11057d0。日常review_recordings.py --latest 5已完成，新增三局本人8163条同包可比无不符，独立装备原图15组25裁片已抽查，冷却遮挡仍漏识别，错误manta候选样本保持unknown未计价；BP各轮已生成，但部分头像识别约4秒。新音频1607段完整性通过，小地图来源ID已有配对但像素/标定未认证；不称准确率或性能因果通过。具体私人证据见diagnostics/project-review/20261028-review.md，未改原始资料、未开游戏/联网/解析全录像或提交推送。

2026-10-27收尾核查：实际同步最近3个闭合启动包；原生GSI/装备/BP/小地图包身份与20261026T202331724926Z报告一致，新格式逐局音频和性能均0包，没有新的实战验收输入，见diagnostics/goal-readiness-audit/20261026T221652302625Z/comparison.json。另只读清点助手341模块，20份初迁后保留源码均有静态引用，保存位置/函数范围/SHA且不删除兼容代码，见diagnostics/retained-source-compatibility-audit/20261026T221947470148Z/REPORT.md。助手最新1614项回归通过run-81545554ff12；本仓库源码未改，658项回归保持对应。真实质量、人工听审及新格式实战时序仍缺，目标未判完成；未改HOME/历史/EXE、未联网或提交推送。

2026-10-27：逐局性能接入自动发现/独立performance-match-handoff索引/日常联合报告。包SHA与来源成本双门禁、失败预留预算、副本合并/同采集冲突整组阻止，新旧记录按明确采集/比赛/双会话及已核验CSV/context SHA标记跨路径重合、不相加；空编号不猜GSI。生成生产进程仍活跃时，独立日常入口自动报告1→2包、首包不变，10份生产/消费源码SHA前后一致；证据diagnostics/performance-match-auto-interop/20261026T215825010546Z/verification.json。最终658项完整离线回归通过run-30049df45361，无失败/错误/跳过/后台异常，289份源码指纹一致。助手本批生产源码未改，原1609项回归仍对应。真实ETW轮换开销/切局及游戏覆盖仍待，未改HOME/历史/EXE，未开设备/外网或提交推送。

2026-10-27：逐局性能原采集器关闭后封存及独立显式CLI接通，按小清单先查封存锁、固定四源/最终退出码/ETW诊断和进出复验，复用原QPC/帧率算法；没有新鲜窗口保持未知。真实监督/最终发布代码使用生成采集器，两次独立CLI读取时生产进程均存活，第一包在第二局后SHA不变，8份源码前后相同；证据diagnostics/performance-match-interop/20261026T214922968785Z/verification.json。Insights645项完整回归通过run-245fb3ab3737，助手1609项通过run-7679bcff4318，均无失败/错误/跳过/后台异常、源码未变。见docs/PER_MATCH_PERFORMANCE_HANDOFF_V1.md。自动发现/持久索引/日常报告尚未接入，真实ETW/游戏性能未验证，未改HOME/历史/EXE，未联网或提交推送。

2026-10-27收尾核查：刷新Pi本机部署准备清单，旧14文件清单仅offline_io.py因先验身份门禁更新而过期；当前14文件SHA与实际payload一致，标准库隔离子进程导入及公共锁检查通过，未加载Gem或读取比赛正文。证据diagnostics/pi-migration-plan/20261026T212932733554Z/verification.json，远端未连接/部署，ARM与EOF仍未验证。Insights635项回归的284份源码指纹仍一致。本批只刷新忽略目录准备资料及任务口径，不改生产源码、历史或EXE，不联网/提交推送。

2026-10-27：新逐局音频包已接入原候选分层复听入口，固定完成分析及包SHA、原PCM裁片、参考流只验字节不解码，源与预测退出复验后才发布成功清单。实际CLI生成资料裁片28,800帧与原WAV逐样本一致，原源/预测未改；未审核页面不能提交为空听审结果。635项完整离线回归通过（run-4f5d3d4b1278），失败/错误/跳过/后台异常均0，284份源码指纹复查一致。见docs/AUDIO_CANDIDATE_REVIEW.md。真实新格式比赛、人工听审及采样时序/性能仍未验证；未改HOME/旧资料/active/EXE，未开游戏/设备/外网或提交推送。

2026-10-27：逐局录音旁证与原生GSI四身份精确来源核查接入日常报告，唯一ID后复核provider/比赛钟/阶段/已报告英雄及共享技能字段；同秒不同ID、重复ID、缺记录/字段均不猜，0/false有效。正常音频及原生GSI读者进入退出复验，日常只放覆盖摘要；延后语义参考包不能进入此核查，未改变盲测顺序。真实双写入器生成GSI/PCM的独立进程证明每局6条旁证/6条唯一原记录和30共享字段相容，第二局后首局SHA不变；夹具用stun状态变化触发原有关键记录，非实战100%覆盖。证据diagnostics/audio-gsi-source-interop/20261026T205530234937Z/，628项完整回归通过run-c99a8f073a46，无失败/错误/跳过/后台异常、源码未变。见docs/PER_MATCH_AUDIO_HANDOFF_V1.md。未改Helper/Home/采样设置/旧资料/active/EXE，未开游戏/设备/外网或提交推送；真实新数据、分层复听和实际时序/性能仍缺。

2026-10-27：新逐局音频包盲测接入既有CLI/算法，延后参考模式在预测前仅验证旁证原字节/行数，不解码技能；输入投影去掉GSI/英雄字段，冻结阶段及来源/预测/扫描SHA核对后才放行旁证。错误或修改来源/预测不发完成收据，新收据保留本包范围且不要求整run退出；原算法/门槛/库不变。同一2秒PCM新旧预测、扫描和事后对照逐字节一致并产出真实分类器生成正候选；实际CLI在生成生产进程仍活跃、无全局summary时完成首局，第二局后首局SHA不变。证据diagnostics/audio-match-analysis-interop/20261026T203841779217Z/，618项完整回归通过run-345ae1ec2824，无失败/错误/跳过/后台异常、源码未变。见docs/CLOSED_AUDIO_INFERENCE.md。真实新格式资料、分层复听适配、逐来源GSI join及实战时序/性能仍缺，未改Helper/Home/旧资料/active/EXE，未开设备/游戏/外网或提交推送。

2026-10-27：逐局录音自动发现/持久索引/日常联合报告接通。固定源码/免安装版布局、同包去重、同capture范围不同有效清单整组阻止，按索引SHA重验正文与WAV；按明确capture ID/文件名/已核验SHA标记旧流及跨路径便携副本重合，新旧计数不相加。仅公开比赛号和双会话完全一致时列出GSI关联包，不冒充逐来源join。生产_record_take生成回调的独立进程日常入口验证首局可在生产仍活跃时自动出报告，第二局后2包且首局SHA不变，证据diagnostics/audio-match-auto-interop/20261026T202207527157Z/。最终610项完整离线回归通过（run-dd4155a3d2ac），无失败/错误/跳过/后台异常、源码未变；助手生产源码未再改，对应1584项指纹不变。本机旧资料兼容报告diagnostics/record-content/20261026T202331724926Z/仍有961段旧录音，新格式0包，未回填。见docs/PER_MATCH_AUDIO_HANDOFF_V1.md；新包盲测适配/逐来源join/实战时序及性能仍缺，未开游戏/设备/外网/更新EXE或提交推送。

2026-10-27：逐局音频独立协议读取/CLI接通，先查清单身份/封存锁再读元数据和WAV，严格目录/结束来源/健康/行数/偏移/PCM/SHA，进入退出复验；原录音run可继续写其他范围。实际生产_record_take以生成回调驱动，生产进程仍存活时独立消费者读首局1片段/0.6秒/6旁证，继续封存第二局后首局元数据/WAV SHA不变。598项完整离线回归通过（run-eec8d7c515c3），助手1584项通过run-a0e8b5397b73，无失败/错误/跳过/后台异常、源码未变；见docs/PER_MATCH_AUDIO_HANDOFF_V1.md及diagnostics/audio-match-interop/20261026T200920190556Z/verification.json。自动索引/日常报告、新包盲测适配、原生GSI join与实战时序/性能仍缺。未改旧记录/active/Home/EXE，未开游戏/设备/外网或提交推送。

2026-10-27：未来录音可选明确来源上下文独立接收：原音频私有会话与运行双会话分离，白名单/原provider秒/公开比赛及封存锁严格校验，片段范围先于WAV读取。日常技术报告保留范围覆盖，冻结后旁证读取校验来源，仍不声称原生GSI join或精确音频时序。实际启动器回调→独立写入器→Insights验证800帧生成PCM、源SHA前后不变，证据diagnostics/audio-source-context-interop/20261026T194959427589Z/verification.json。完整589项回归通过（run-1da32382b267），助手1574项通过，无失败/错误/跳过/后台异常、源码未变；见docs/AUDIO_RECORDED_SOURCE_CONTEXT_V1.md。音频逐局交接仍待做，未改旧资料/active/Home/EXE，未开游戏/联网/提交推送。

2026-10-27：修正旧小地图录像入口的身份检查顺序：路径及manifest先查封存锁，再读采样正文/未来目标候选；8项生成数据检查及584项完整回归通过（run-87b7ba0a6c4b），无失败/错误/跳过/后台异常、源码未变。两份已知开发归档经实际CLI复跑，640个事件整份报告除耗时外与旧报告完全一致，八个输入前后SHA不变。证据diagnostics/minimap-replay-admission/20261026T193711797396Z/verification.json，见docs/MINIMAP_REPLAY_ADMISSION.md。未读真实封存答案、重解DEM、操作游戏/联网/修改Helper运行代码或提交推送；玩家可见性、地图标定及预警仍未认证。

2026-10-27：两项未映射技能的当前官方来源已核查并前后复验。Smash无直接AbilitySound，FireShield参数只作调查线索；魅惑/蹦蹦跳跳/小鹿乱撞均声明EnchantCreep，不当成单技能唯一声。当前11条魅惑候选用另一EnchantCast资源，5条嗜血候选用Cast而静态字段声明Target，说明AbilitySound不是完整发声/阶段清单。证据diagnostics/official-audio-ability-audit/20261026T191417272382Z/verification.json及docs/OFFICIAL_AUDIO_BANK_EXTENSION.md。本轮仅补来源事实，不改模板/预测/生产源码/active，不开游戏、录音、联网或提交推送；576项对应源码回归仍有效。

2026-10-27：官方音效构建与有界VPK读取独立迁入Insights，迁移来源SHA明确；移除激活API、输出仅本仓库新diagnostics。游戏目录SHA已变化，但旧库273项依赖的103432198字节逐项SHA与当前安装一致，构建前后复验；保留402旧模板/原特征字节前缀，新增火焰爆轰/引燃/嗜血术各4模板，隔离v8共414模板，active仍v5。固定原0.30波形门槛在同一闭合录音得到19候选，原14窗口全保留，新增5嗜血术候选均邻近本人采样CD；模板覆盖196/207不作准确率，火焰爆轰/引燃仍无通过门槛候选，Smash和蹦蹦跳跳不猜映射。576项完整离线回归通过（run-94eeaa9435b5），无失败/错误/跳过/后台异常、源码未变。实际构建/比较证据diagnostics/official-audio-extension/20261026T185235704815Z/，见docs/OFFICIAL_AUDIO_BANK_EXTENSION.md。未改Helper/Home/旧素材/active，未开游戏、采集、联网、更新EXE或提交推送。

2026-10-27：声音候选已按原WAV声明的比赛/音频本地会话及采样偏移拆分，未按UTC猜归属：9062710567录到2084.3秒，v7粗频谱97→波形0；9062765692录到2703.9秒，94→14。第一局本人技能无模板覆盖，0不能算通过。最近5次启动准入核查中，其他三份音频均handoff_audio_not_closed，未读被阻止WAV正文；没有新增独立验证集，保留待补。证据diagnostics/audio-cross-corpus/20261026T184228735226Z/，见docs/AUDIO_EXISTING_BANK_COMPARISON.md。未改生产源码/阈值/原记录/active，未开游戏、采集、联网或提交推送。

2026-10-27：同一961段/4788.2秒录音完成已有v7库及固定波形开关对照。v5粗频谱181候选，v7粗频谱191（新增12条魅惑魔女技能），原0.30差分PCM核验后14条，保留11条靠近本人CD采样边沿的魅惑候选；重生模板的304次波形核验最高0.1277。不是听审准确率，不能从171→0推断真实误报清零。402模板WAV/库/active和录音原SHA前后不变，active仍v5。6段分层复听中2段与旧页PCM重复，另备4段新片段9.6秒，均unreviewed；旧26/9段不改。证据diagnostics/closed-audio-comparison/20261026T183050687657Z/，见docs/AUDIO_EXISTING_BANK_COMPARISON.md。本轮无生产源码变化，沿用569项完整离线回归，不重跑相同检查；未开游戏、采集、联网、改阈值、启用预警、更新EXE或提交推送。

2026-10-27：日常联合复盘自动执行小地图/GSI精确来源配对，报告来源ID覆盖、唯一原记录配对、可用位置证据和逐类缺口；仅保留紧凑摘要，缺原生索引不冒充0，旧上下文不按时间回填。真实写入器的生成资料→独立日常入口得到2条配对/2条可用位置，源SHA前后不变，证据diagnostics/minimap-source-id-auto-report/20261026T182457387212Z/。569项完整离线回归通过（run-b15133ce4022），无失败/错误/跳过/后台异常、源码未变；未改助手/历史/正式配置/EXE，未开游戏/联网/提交推送。见docs/MINIMAP_GSI_SOURCE_ALIGNMENT.md。

2026-10-27：发现最新两局GSI的provider时间各有117/127个重复键，其中107/101个键下坐标或比赛钟不同，不能把同秒当同一来源。Foresight新增回调来源ID与锁内上下文快照，原采样频率不变；Insights新增明确比赛+双会话+来源ID配对及坐标审阅工具，缺ID/重复ID/内容冲突保留未知，旧资料不回填。真实生成worker和本人记录器→独立Insights进程得到2条精确来源配对，坐标(100,-100)/(200,-200)，源SHA不变；像素/标定仍未认证。证据diagnostics/minimap-source-id-interop/20261026T181327883301Z/及docs/MINIMAP_GSI_SOURCE_ALIGNMENT.md。567项完整离线回归通过（run-d1902a6d0cc2），助手1567项通过（run-c58124833c3a），无失败/错误/跳过/后台异常且源码未变。本批未改Home、未操作游戏/联网/旧资料/EXE或提交推送。

2026-10-27：小地图帧来源上下文独立消费已接通。摘要先检查封存锁，原session与清单一致、逐行采集/发送/provider时钟、同序重定义/倒序、视频输入及额外载荷均校验；联合报告只按公开比赛号和双会话完全一致列出原生GSI包，不按UTC猜。空公开比赛号可保留本地会话但不回填，基础run及像素身份仍未知。真实worker生成像素→独立CLI→隔离索引报告证据在diagnostics/minimap-context-interop/20261026T174858403060Z/，原文件SHA前后不变。557项完整离线回归通过（run-f82a90ae486e），助手1560项通过（run-44e46d1a1657），无失败/错误/跳过/后台异常、回归源码未变。见docs/MINIMAP_FRAME_CONTEXT_V1.md。未读封存答案，未开游戏、联网、改旧资料、更新EXE或提交推送。

2026-10-27：GSI具名证据普查及原坐标CSV导出接通，两局原生4946条中4764条本人坐标可用，4297条为存活且来源年龄≤5秒。中文报告分开true/false/未报告；沉默/眩晕/缴械/妖术/禁用/破坏/开雾等有true样本，channeling/teleporting/debuffimmune等未报告，不推成false或全量buff列表。发现roshan_killed事件30/33次重复提及，各1个带时间事件键；事件原game_time为1940/2160，首次收到时比赛钟为1732/1908，分栏保留不混用。肉山状态节点/魔方节点仍缺失，不启用提醒。证据diagnostics/gsi-evidence/20261026T172404830121Z/及联合报告diagnostics/record-content/20261026T172240104227Z/，见docs/GSI_RECEIVED_EVIDENCE.md。550项完整回归通过（run-6800653a012f），中文表补齐后34项相关检查通过（run-dcf7a4e7b361），均无失败/错误/跳过/后台异常。地图像素标定仍未认证，原坐标不截断、不按UTC关联小地图。本轮不改助手/Home、不启用新提示、不开游戏或联网，未提交推送。

2026-10-27：冻结音频候选的分层复听工具与候选专用政策已接通，避免前50个名额被重生模板占满；按技能轮转、时间首尾/中间抽样，精确PCM截取、原包/WAV与冻结预测进出复验，最终review.json原子发布。实际181候选选10窗，9段约21.6秒覆盖6技能；幽灵船1段跨暂停边界明确不可用，原预测不改。中文复听页在diagnostics/audio-candidate-review/20261026T170409238182Z/listening-page-zh/index.html，9段均未听审，旧26段及旧页面不改。538项完整离线回归通过（run-e65a46d15a0c），随后中文/导入闭环专项30项通过（run-796274ddab37），均无失败/错误/跳过/后台异常。见docs/AUDIO_CANDIDATE_REVIEW.md；不开游戏、联网或实战预警，Home继续留待用户测试，未提交推送。

闭合录音盲测已完成一轮：入口强制准入包与WAV复验、固定参考库、预测冻结后才读已准入GSI、整包/声音/库进出复验及最终原子完成回执。最新961段4788.2秒固定库基线耗时106.524秒，输出181候选，其中171集中在骷髅王重生template_086，不能当真实释放或准确率。本人207次采样CD边沿均不在此库覆盖内，命中率仍不可验证。证据diagnostics/closed-audio-analysis/20261026T165125847998Z/，见docs/CLOSED_AUDIO_INFERENCE.md。未改阈值、参考库、原记录或启用音效预警；Home继续留待用户验收。

2026-10-27：日常入口已合入性能、录音文件技术核验和BP链路诊断。最新两局4892条本人经济同包可比记录无金额/分项不符，本人装备图像尝试0；961段4788.2秒WAV逐文件SHA/PCM/长度/摘要总量及前后来源复验通过，无全零/低于-60dBFS片段或int16极值样本，不证明技能可闻。BP第一局在selection记录里无可用阵容，第二局四头像含冲突而非确认2+2；首轮完整视图写入→控件应用124ms，不是揭示/显示延迟。最新7组大件疑点9图已看，鱼叉→血棘及蓝色刃甲→分身斧候选均保持unknown，没有错误候选计价；只留建议、不改模板。实际联合报告diagnostics/record-content/20261026T163951741342Z/；裁片意见diagnostics/item-priority-review/20261026T163104574176Z/LATEST_VISUAL_REVIEW.md。524项完整离线回归通过（run-92d902c1a788），无失败/错误/跳过/后台异常，回归源码未变。见docs/AUDIO_INTEGRITY_REVIEW.md及docs/BP_DIAGNOSTICS.md。Home按用户要求留待测试，本轮不改助手，不开游戏、不联网、不重建EXE或提交推送。

对局性能离线复核已接通：固定PresentMon版本/二进制与正常收尾门禁，原生QPC按明确比赛和双会话筛选游戏前台窗口，进出复验原CSV/上下文；不同流不混合，提交FPS与显示FPS分开。最近三个闭合启动记录含四场比赛，合格窗口内显示均值90.90–112.60 FPS、1% low 38.24–47.15，无保留显示间隔超过100ms；窗口边界/未记录区间不代表无卡顿，没有助手开关因果结论。报告diagnostics/performance-review/20261026T105935248544Z/，见docs/PERFORMANCE_REVIEW.md。507项完整离线回归通过（run-98e1a95c49c1），无失败/错误/跳过/后台异常且回归源码未变。旧小地图审查也未提供可靠方向英雄标记正例，保留缺口，不继续在同一文字遮挡数据上调阈值。未开游戏、联网、改历史、重建EXE或提交推送。

2026-10-26。insights独立维护录像与离线数据分析；实战助手由dota2-foresight（原helper）维护，两边不跨仓库导入源码。

资源匹配已扩展到完整颜色候选，不再限于孔洞：已有准备图像8帧226条候选，218未知、8个ward_obs形状建议；8个均已看图，外观与眼形资源相容，仍未认证实体、数量、位置或英雄识别。没有hero_marker建议，缺少已确认英雄正例，不能从拒收数或0建议算准确率。报告diagnostics/minimap-frame-template-match/20261026T104840063465Z/，见docs/MINIMAP_FULL_CANDIDATE_MATCH.md。499项完整离线回归通过（run-58cb5d35f2aa），无失败/错误/跳过/后台异常且回归源码未变；入口重验准备副本PNG/参照，不宣称重验原比赛日志。线上算法/配置/旧资料不改，未开游戏、联网、重建EXE或提交推送。

资源族离线匹配已接通：9族25个原始资源构建603个尺寸/方向变体，跨族保留最佳/次佳，分数≥0.82且差≥0.08才作未认证形状候选，缺参照/弱匹配/歧义保持未知。48张孔洞裁片全部未知；眼形样本ward_invis分数0.816327、hero_marker次佳0.773333，不足以确认。已查看裁片对照和像素差异，未放宽阈值或把拒收当准确率。报告diagnostics/minimap-template-crops/20261026T103955817522Z/，见docs/MINIMAP_TEMPLATE_MATCH.md；此入口只重验准备好的裁片/资源副本，不重验原始比赛日志，后续需完整候选和独立正负证据。493项完整离线回归通过（run-dd69fe9c9752），无失败/错误/跳过/后台异常，回归源码未变。线上算法/配置/原记录未改，未开游戏、联网、重建EXE或提交推送。

小地图原始资源参照已整理：已有游戏图集122个定义按原矩形/RGBA导出并绑定SHA；当前VPK目录SHA与旧来源记录一致，三份编译缓存SHA一致，但未重提取或认证运行时。已查看英雄/眼/兵线/信使图标族及并列对照：未命名空心菱形外观更接近ward_invis眼形，英雄资源有不同方向/填充模式，不能把空洞当英雄证据。未回写身份标签，后续按多图标族、方向、尺寸及歧义比较推进。图册/对照在diagnostics/minimap-icon-catalog-20261026/，见docs/MINIMAP_ICON_CATALOG.md。486项完整离线回归通过（run-6720b9c04122），无失败/错误/跳过/后台异常、回归源码未变。未开游戏、联网、改线上算法、重建EXE或提交推送。

小地图孔洞形状复核已推进：原图封闭颜色区域用实际空白像素度量，避免细边框轮廓面积比偏差；48张原图得到300个形状，分层48张裁片已查看。70个文字带重合、0个方块重合不代表其余是英雄；文字笔画、蓝紫图标和空心菱形均会命中，部分明显文字未被文字带规则标到。报告diagnostics/minimap-hole-probe/20261026T102240067058Z/，见docs/MINIMAP_HOLE_PROBE.md，所有身份/visual_label仍未知。479项完整离线回归通过（run-0119fa1d3d10），无失败/错误/跳过/后台异常、回归源码未变。未按开发裁片自动删框或启用预警，助手运行代码/配置/旧记录不改，未开游戏、联网、重建EXE或提交推送。

小地图连续样本探索已推进：按原行/PNG/BGR像素校验抽6窗口48样本；41对满足序位/布局/尺寸/时间门禁，1对2.033072秒缺口拒绝比较。得到1065条重复图像对应（947小位移、118较大位移），不是目标数或英雄轨迹。实际查看两组连续拼图与对应样例，兵线小点同样移动，名字紧邻英雄标记，不能由运动推身份或消失。报告diagnostics/minimap-sequence-probe/20261026T101413182462Z/，见docs/MINIMAP_SEQUENCE_PROBE.md。471项完整离线回归通过（run-42239f13e07e），无失败/错误/跳过/后台异常、回归源码未变。线上算法/配置/历史记录不改，未开游戏、联网、重建EXE或提交推送。

小地图干扰复核已推进：旧颜色基线独立适配到Insights，只添加疑似文字带/实色方块重合标记，不删框、不推身份或英雄数。24张已核验且SHA不同的图像得到663个颜色候选，40个文字带重合、197个方块重合，无候选触顶；实际查看两张标注图，发现塔/基地与名字附近均可能产生候选，不能用候选数当人数。独立Foresight进程与Insights对照24图基础字段完全相同，证据diagnostics/minimap-component-parity-20261026/verification.json；报告diagnostics/minimap-component-probe/20261026T100354372321Z/。462项完整离线回归通过（run-7742f61d9b52），无失败/错误/跳过/后台异常、回归源码未变。详见docs/MINIMAP_COMPONENT_PROBE.md；线上算法、配置和旧记录不改，未开游戏、联网、重建EXE或提交推送。

小地图原图复核已推进：新旧格式共用准入、原行SHA、PNG/BGR像素SHA及ROI尺寸核对，分散抽样并补最大采集间隔两端，提供原PNG/拼图/复核页。先同步后读取两条旧流12517样本，24张抽样图全部通过且已查看；最大相邻采集间隔0.537498秒和2.033072秒，无时钟倒退/run内布局变化，第二条有1次相邻复用。实际报告diagnostics/minimap-image-review/20261026T095554077691Z/，图标可被名字遮挡，未补身份标签、预测真值或地图认证。真实worker生成像素也跑通2图核验，证据diagnostics/minimap-image-interop-20261026/verification.json。454项完整离线回归通过（run-928e0d471f9c），无失败/错误/跳过/后台异常，源码在回归中未变；助手运行代码本批未改，未开游戏、联网、重建EXE或提交推送。见docs/MINIMAP_IMAGE_REVIEW.md。

小地图独立run自动索引/联合报告已完成：固定源码/便携版布局、独立包SHA索引、来源及身份复验；同包去重、同run不同有效清单整组阻止，与旧包重复流逐项标记不可累计，比赛保持未知。443项完整离线回归通过（run-9298be40a060），无失败/错误/跳过/后台异常且源码未在回归中变动。既有真实worker生成资料完成自动发现→报告，证据diagnostics/minimap-report-interop-20261026/verification.json，源文件SHA/mtime不变；正式3个旧包复核在diagnostics/record-content/20261026T094919589265Z/，未发现新格式小地图包，不混入夹具。助手运行代码本批未改，沿用1547项上批结果；明确比赛身份和实战图像/性能仍待后续，未开游戏、联网、更新EXE或提交推送。

小地图独立run收尾交接已接通：真实采样worker关闭源/辅助句柄并写完session后，另发固定五流/session SHA清单；容量/异常/未闭合不发成功标记，辅助缺口保留。Insights独立进出复验、关闭未读游标，输出样本/尝试计数和原采集UTC，比赛身份保持未知。生成两帧像素的跨进程核验在diagnostics/minimap-handoff-interop-20261026/，原session SHA不变；不是实战图像或地图认证。432项Insights（run-b0cc0d098cfa）和1547项助手（run-d8ec018f93d8）完整离线回归通过，无失败/错误/跳过/后台异常、源码未在回归中变化。自动索引/联合报告和明确比赛身份仍待推进，未开游戏、联网、重建EXE或提交推送。

BP前缀自动索引、联合报告和迟到比赛号独立绑定已完成离线接通。原截止身份/字节不改写，绑定必须同BP会话+前缀SHA，分析期间新增/改变绑定需重同步；重复副本明确去重、冲突整组阻止，不按UTC猜归属。422项Insights及1542项助手完整离线回归通过（run-05d069e695c8 / run-561ad86c2e7b），无失败/错误/跳过/后台异常，回归期间源码未变。新的双进程生成阶段验证在diagnostics/bp-bound-report-interop-20261026/，生产服务仍运行时自动生成BP联合报告，消费后正常停机且原前缀不变。真实阶段、UI回执和推荐质量仍未验证；旧EXE不变、未提交推送。

BP首个结束阶段的异步日志前缀交接已接通：Foresight只在主线程取得七条日志截止字节，独立线程哈希/发布；主日志继续追加，Insights只验证读取封存前缀，末尾增长不混入、前缀变化拒收。选人候选事件与UI回执分开计数，缺UI为null，未知比赛不按时间归属。真实manual服务在进入in_game且继续运行时，独立CLI只读到strategy截止；输入为手动生成阶段，不是游戏验证。408项Insights和1539项助手完整离线回归通过（run-fe1f44ea49c6 / run-85ca437afb3e），无失败/错误/跳过/后台异常，源码未在回归中改变。见docs/DRAFT_PHASE_PREFIX_V1.md及diagnostics/bp-phase-interop-20261026/。自动索引/联合报告/迟到比赛绑定与实战覆盖仍待推进；未重建EXE或推送。

原生装备包的大件队列/裁片适配已完成：新旧格式共用ItemReviewPriorities的≥2000目录价及<0.12分差挑样，原生证据绑定正文/原始行SHA；裁片重新核对当前独立装备索引、行、候选、图像SHA和显式客户端origin/格矩形，不回写标签或线上阈值。真实Foresight记录器另写生成像素，经独立Insights自动索引→挑样→裁片得到预期20×10蓝色区域；正式旧资料复跑仍为9组14张全部准备成功，没有将其当作新实战或99%准确率证据。400项完整离线回归通过（run-ca170c535b07），无失败/错误/跳过/后台异常且源码未在回归中改变。见docs/CLOSED_RECORD_REVIEW.md、diagnostics/native-item-crops-interop-20261026/verification.json及diagnostics/item-pipeline-verification-20261026/verification.json；未开游戏、联网、重建EXE或提交推送。

装备逐局包已完成自动发现/持久索引/联合报告接入：源码与免安装版根使用固定布局，equipment-handoff索引独立绑定装备包SHA；分析时再次核对身份/正文。显式比赛+game/record会话关联本人GSI，不用UTC猜归属；重复包省略，与旧装备日志范围重合时标记不可累计。393项完整离线回归通过（run-fe22c3a5fbfa），无失败/错误/跳过/后台异常、回归期间源码未变。正式本机3个旧闭合包复核报告在diagnostics/record-content/20261026T082835716251Z/，当前正式原生GSI/装备包均未发现；既有真实写入线程生成的装备夹具在隔离分析目录自动发现1包并生成报告，证据diagnostics/equipment-report-interop-20261026/。没有修改正式配置、用户历史或将夹具纳入正式索引。真实切局、图像内容和其余通道仍待推进。

装备观测原生单包接收已推进：Foresight在源代次退休及本会话提交截止后，等已接受作业完成再封存；Insights observation_handoff独立验证元数据/队列截止/身份/帧时间/正文SHA，进出复验并输出日志计数，图片仍待独立核验。真实写入线程仍处理第二局时，独立CLI已读第一局生成夹具；生产/消费证据在diagnostics/equipment-handoff-interop-20261026/。380项完整离线回归通过（run-34dc7ab885c7），助手1533项通过，均无失败/错误/跳过/后台异常且回归期间源码未变。自动目录发现/持久索引/联合报告和真实切局仍待推进，不把独立单包可读当整批交接完成。见docs/PER_MATCH_OBSERVATIONS_V1.md。

日常复盘入口tools/review_recordings.py已接通：先同步旧启动器与原生逐局资料，再重验来源并生成同一份内容报告。原生发现覆盖源码/免安装版固定目录，索引持久保存根标识/相对路径/包SHA，不复制正文；索引后来源变化或身份不符拒绝生成成功报告，相同包去重，与原启动器同一GSI主日志范围重合时明确标记不可累计。371项完整离线回归通过（run-09442374e19e），失败/错误/跳过/后台异常均0、源码未在回归中变化。实际一条命令复核3个已有闭合包，当前正式历史没有新格式逐局包，原生项为0而非伪造补齐；报告diagnostics/record-content/20261026T080344412289Z/。见docs/RECORD_REVIEW_PIPELINE.md；未开游戏、改原记录、提交推送或重建EXE。

本人经济复核补入同包显示检查：新旧交接格式复用SelfEconomyAudit，固定估值GSI时间、普通金额/原始金额、德鲁伊熊灵来源及合计、分项加总和缺项；不同时间/缺失金额不按零处理，reported净资产不与不完整分项相加。最近四场11513行中11345行同包可比且记录一致、分项无不符，168行不可用；没有reported净资产或熊灵混合显示行，因此仍不认证真实总经济或德鲁伊实战。358项完整离线回归通过（run-4884d2b82559），失败/错误/跳过/后台异常均0，源码在回归中未变。见docs/SELF_ECONOMY_AUDIT.md与diagnostics/record-content/20261026T075151837213Z/DISPLAY_REVIEW.md；原记录、线上估值和界面未修改。

不停观察的本人GSI逐局交接v1已接通：Foresight主写入线程按明确身份生成独立记录，首个POST_GAME后封存SHA/COMPLETE；Insights独立验证和原值简报，不等待启动器整体关闭。两个独立进程的合成GSI交接已跑通；其他通道尚未逐局闭合，所以仍标ready_with_gaps。最终345项完整离线回归通过（run-7ddb3712ba0f），失败/错误/跳过/后台异常均0，源码未在回归中改变。见docs/PER_MATCH_GSI_HANDOFF_V1.md；没有读取新实战或重建EXE。

本轮定位音频摘要矛盾并在Foresight生产端修正：最终磁盘摘要改为线程join之后原子发布。Insights保持原拒收门禁，补测running/worker_alive/finalizing及启动器未确认退出的负例。只修复新记录生产流程；已有三份摘要仍不回写、不解除原阻止状态，真实录音与26段听审仍待验证。

本批最终336项完整离线回归通过（diagnostics/offline-tests/run-a573495145ee/result.json），失败/错误/跳过/后台异常均0，回归期间源码未变；两仓库git diff --check通过。以下早期阶段中的“未改Helper/接收入口待做”是当时边界，现以本页顶部和TASKS最新状态为准；仍未提交推送、重建EXE或开展游戏验证。

## 本轮推进：复盘资料就绪报告

数据包接收端已补入Foresight：独立清单/正文校验后复用其原生统计、规则、来源绑定和原子写入，通过显式CLI导入，不跨仓库导入源码。生成算术夹具的独立生产→接收→既有DraftService读取已跑通，仅写Foresight diagnostics；正式统计/规则原SHA不变。真实旧统计在接收端仍stale，规则130来源通过但version_unverified，均未导入。读包不启动观察/模型，导入提交前有游戏状态与规则SHA/版本复查；未授权安装新EXE。证据见Foresight docs/INSIGHTS_DATA_DELIVERY_V1.md及diagnostics/insights-delivery-20261026/REPORT.md。

地图与迁移后续：建筑模板灰度/阵营轮廓两批各45项已冻结比较，候选不完整且兵营资源名可匹配实际塔图标，未增加正式地标或认证。最多16点的精确集合匹配以子集动态规划替代阶乘枚举。Pi托管部署原5文件遗漏拆仓依赖，已补为14文件静态闭包及公共锁名单，隔离-I -S子进程导入通过；只生成本机迁移清单，未联系/修改Pi、复制配置或读取私钥。源码解析入口也执行封存路径/footer身份检查，锁名单缺失时拒收。最终336项完整离线回归通过（run-cd1047903366），无失败/错误/跳过/后台异常；新地图/迁移证据见docs/MAP_BUILDING_GLYPH_REVIEW.md、docs/PI_MIGRATION_CHECKLIST.md及diagnostics/pi-migration-plan/20261026T064842862523Z/REPORT.md。

独立交付契约已推进：distribution_bundle.py提供规则/统计v1清单、正文SHA/内部校验和、COMPLETE收尾标记、来源文档SHA和版本/筛选/新鲜度检查。原生cache v1及capability v1/v2的纯校验逻辑独立适配，来源指纹另记，不跨仓库导入。实际已有统计包仍stale_statistics，规则包仍semantic_version_unverified；没有自动安装/激活，也未改Foresight消费入口。rule_source_audit已核对130项现存来源，SHA全部一致，inspect可选重新核对并输出source_bytes_verified=true；规则语义版本及新统计资料仍待补。见docs/DISTRIBUTION_BUNDLES_V1.md。最终320项完整离线回归通过（run-3d077f7b0c74），失败/错误/跳过/后台异常均0，源码未在回归中改变，git diff --check通过；实际报告diagnostics/distribution-20261026/REPORT.md。

闭合内容复核已推进：tools/review_closed_record_content.py逐行重验来源后按显式身份汇总经济/BP，并为共享小地图/性能日志生成UTC夹点候选行段；候选不进入比赛事实或训练。tools/prepare_record_item_review.py以原图SHA、客户端origin和slot矩形生成定向裁片。本机四场记录未发现可比分项加总不一致，本人英雄名下视觉装备观测均为0。9组大件14张首尾裁片实图检查：8张已知图标相容、1张未知散失候选相容、3张莲花/达贡候选不受画面支持、2张背包图无法判断；不当作准确率、不回写标签和金额。见docs/CLOSED_RECORD_REVIEW.md；具体报告在diagnostics/record-content/20261026T060009262096Z/和diagnostics/item-priority-review/20261026T060502950951Z/REVIEW.md。

本批最终296项完整离线回归通过（run-a5a035b2faa3），失败/错误/跳过/后台异常均0，git diff --check通过。复核流退出后不允许继续读取，实际解析路径进入封存目录时在读取/哈希前拒收。近期两份原始专项方案仍未找到，Downloads中仅存在两部分结构分析文档；未用新报告替代缺失原方案。地图/音频/实战验收仍未放行，未改Foresight或远端服务，未提交推送。

地图补核：15张既有地图图像完成4类官方图标的60项整图搜索，先冻结再比较，原记录不改。莲花池维持2个独立点的集合相容证据；新小尺寸瞭望塔候选经标注图查看是背景误匹配，神符未检全，未升级地图认证。工具、证据边界和复现命令见docs/MAP_GLYPH_REVIEW.md。地图非塔正式验证仍未完成。

新增tools/review_helper_records.py，交接后按比赛/本地会话整理八类通道的收尾、明确关联行数、来源时间字段和后续处理项。进入报告前重验包内容地址及来源指纹；变化来源不沿用旧统计。共享日志未绑定不分摊给每场，未记录/未收尾不补零，行数不作为准确率。见docs/RECORD_READINESS.md。

最新三个启动记录覆盖四场，全部ready_with_gaps。最新一场未发现小地图通道，三份音频均有收尾门禁缺口，部分经济/画面记录触及配额。具体编号与来源时间仅写本机diagnostics/record-readiness报告；本轮未读取图片/声音/录像真值，不宣称识别准确率。43项交接与报告专项离线检查通过（run-ccaeab9625c1），最终257项完整离线回归全部通过（run-2920a6fd5215），无跳过、后台异常或回归中源码变化；git diff --check通过。最终资料报告见diagnostics/record-readiness/20261026T053455945415Z/REPORT.md。

## 当前主线：Helper记录只读交接

接收端v1已实现：按比赛/本地会话建立来源清单，拒收未闭合、未排空或变化中的记录，进入分析前重验指纹。重复扫描复用同一清单，不搬动原始数据。当前245项完整离线回归通过（run-b83fbb07e997）。入口为tools/sync_helper_records.py，不要求用户操作EXE。

最近两个已关闭启动记录关联三场比赛，可用通道已接收并保留配额警告；音频终态矛盾暂未接收，另一个未闭合启动记录等待。具体编号和核验结果只存本机diagnostics/helper-handoff/。完整契约见docs/HELPER_RECORD_HANDOFF_V1.md。

当前兼容模式需要Helper停止观察并收尾；助手不停机逐局交接仍需Helper生产端新增闭合标记和共享日志边界。这一阶段不改Helper、不继续打包或扩展EXE。

## 备用应用（不作为当前开发重点）

独立免安装版0.1.0已生成：dist/insights-0.1.0-20261025-125559-566019/，完整解压ZIP后双击Dota2Insights.exe。提供本地DEM串行队列、固定Gem解析及事实报告；不在启动时自动解析、下载、采屏或录音。源码双击入口仍可使用。

214项离线回归通过（run-630442de5fee），实际冻结自检和ZIP4750文件逐项SHA/CRC通过。单场旧录像9044645010经ZIP隔离副本的真实队列完整到EOF，26000状态/20889事件与既有归档的SHA相同，原DEM不变，流程约66秒。详见docs/PORTABLE_RELEASE_20261025.md。

## 数据和验证边界

地图/眼位独立客户端验证、玩家可见性真值、跨局识别质量仍未认证。26段声音听审由用户稍后完成；前十分钟因果分析仍为计划，不擅自实现。

Pi管理代码在此仓库，但远端仍使用原部署，本轮没有连接或升级。本人录像登录/下载暂留helper，分析本地DEM不依赖该流程。现有启动记录的兼容交接v1已实现；原生逐局闭合、规则包和统计包契约仍待推进。

## 历史与开发

拆仓时原1623个测试编号全部保留，130个分析专用源码/测试文件从helper活动目录归档，原字节副本、ZIP和SHA均保留。来源见migration/source-manifest.json和拆仓时verification.json，后续修改不冒充初次导出字节。

独立应用构建阶段只修改insights；后续交付接收和音频收尾阶段也修改Foresight，两边各自独立测试。当前insights分支codex/insights-portable，未提交推送。新产物、固定第三方依赖及验证报告不进入Git；历史记录原位保留。首次失败构建已标记并保留日志，不作为交付。

仓库命名更新：用户已将实时助手命名为dota2-foresight。当前本地旧目录和history配置键保留，以维持历史来源路径；助手origin使用已核验可用的https://github.com/basti26/dota2-foresight.git；用户提供的SSH地址指向同一仓库，当前SSH连接超时。迁移来源清单保留当时的旧仓库名，不回写历史指纹。


- 2026-10-28 离线补查：三局装备审阅补充 15 张按 SHA 核验的同英雄同格图像，军团疑似刃甲有无数字均存在 unknown，虚空假面疑似大雷锤冷却期间由 known 变 unknown；记录中两例存在保留旧经济值的选择，但未核验屏幕显示或录像真值。详见本机 `diagnostics/terra-review-20261028/equipment-visual-review.md`。

2026-10-29：按用户要求重点复核最新三盘职业比赛255眼的重复/邻近落点。128/256/512距离敏感性、同方/对手/跨局分层、真实落点固定半径邻域及结束/重叠时序已输出；256内同方14对，未见同时存活。10项针对性回归通过；保持地图及效果独立核验未认证，不改Pi/Foresight、不提交。见docs/WARD_PROXIMITY_REVIEW.md。

2026-10-29：用户明确改为自然对局自动留证，已写入两仓AGENTS及docs/AUTOMATIC_EVIDENCE_WORKFLOW.md，旧专门开游戏核验方式由新流程覆盖。Foresight统一首页已自动衔接现有记录（52项离线检查），眼位镜头诊断等生产端缺口仍待；Insights不放宽准入/独立真值门禁，未自动勾选原待验证项。
