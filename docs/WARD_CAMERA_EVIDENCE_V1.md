2026-10-29：新记录默认已升级为v2原尺寸JPEG/每分钟留图，见[WARD_CAMERA_EVIDENCE_V2.md](WARD_CAMERA_EVIDENCE_V2.md)。本文保留v1 PNG及其验证历史；新读者同时支持两版，旧记录不转换。

# 眼位镜头自动留证 v1

2026-10-29，用户明确授权从Insights扩展到Foresight补自动记录，再回Insights分析。不开游戏、不采真实画面做开发验收；生产端使用用户以后自然对局。两仓不互相导入源码。

## 正常启动

使用源码统一入口Start-Foresight.cmd，开始Windows实战观察、已有“每次HUD/界面记录”选项开启时，启动器自动初始化内嵌眼位页的后台记录，不切换页签、不抢焦点、不要求用户取参考或点击开始。尚无新鲜对局GSI时只记等待，不截菜单或桌面。PRE_GAME/GAME_IN_PROGRESS阶段且provider相对真实采集UTC在[-2,5]秒内才留定位样本；采集适配器继续检查Dota前台和稳定客户区。公开封存锁在自动取帧前检查。

记录与浮层独立。没有镜头参考仍自动记录unlocalized/no_reference及有限候选原帧，矩阵null、投影0；不会为满足记录而猜位置或自动画点。原型浮层仍需要已有的显式镜头参考，不能宣称已经自动标定或全地图可靠贴地。

“暂停浮层”只暂停显示；“停止记录”关闭本段记录，不在同次观察中自动重启。停止观察、观察进程结束或退出应用都会停止留证并有界收尾；新一次显式观察可以开启新段。旧EXE未重建，不包含本次源码能力。

## 来源与字段

源目录diagnostics/ward-camera/run_<32位ID>/。session.json为运行状态，events.jsonl为白名单事件，frame-NNNN.png为有界原始客户区候选图；CLOSED.json最后发布。只有CLOSED和session为同一闭合字节版本时才可消费，文件大小/SHA逐项进出复验。没有CLOSED不读取事件或图片，也不据此断言进程仍活着。

CLOSED公开contexts为本段出现的明确match_id/game_session_id/record_session_id范围；先检查封存锁再读取正文。逐行原生GSI来源ID、provider UTC、比赛钟、帧原UTC/单调时钟均保留；本地人机可空比赛号，不借职业参考包编号或按相近UTC补归属。换局清空旧视觉参考并改变epoch；同一运行可以包含多个明确范围，不能当单局完整录像。

location记录帧ID/尺寸/原采集时间、来源上下文、历史参考包SHA与独立historical_reference_match_id、参考epoch/index、内点/匹配数/内点重投影中位误差、世界到屏幕矩阵、投影点及省略数。参考误差只是图像配准误差，不能当真实地面坐标误差。reference_added记录原参考帧来源、像素内容SHA、矩阵与epoch；图像仍受采样上限，不能把声明像素SHA当文件已核验。

surface记录实际Tk应用/隐藏动作、关联帧及原因，os_visibility_verified始终false；它不能证明OS已呈现或玩家看到了点。失败、无帧、等待独立记录，不保存异常原文、原始GSI、配置或密钥。

## 成本、损失与收尾

后台原型捕获线程自动取样最多每5秒一次；原型显示开启时沿原2Hz采集，定位留证最多每2秒一次。PNG最短15秒、最多80张，事件最多4096条，本段最长2小时，数据128MiB；图像队列容量2，客户区不超过4096×2160。达到截图数后保留元数据，不能声称全局图像覆盖；达到事件/容量/时长上限、队列满或异常均保留limited/dropped/error状态。

PNG编码/写盘在独立后台写入线程。UI与捕获不等待写盘队列，队列满明确丢弃；写盘收尾最多等2秒，超时即使后台稍后结束也不冒充完整。complete只表示本段写入队列收尾且无已记损失，不表示所有捕获成功、整局覆盖、定位正确或可见性认证。

## Insights消费

现有tools/review_recordings.py的日常报告自动发现源码与已知便携布局中最新闭合记录，默认最多5段、扫描最多2000目录；不额外复制图片。闭合版本冲突整组拒绝，副本去重；图片只做字节大小/SHA核验，不自动生成像素真值。来源变动、未闭合、封存、预算超限分别拒收。丢弃/限额已明确且文件闭合时可以报告closed_verified_with_gaps，绝不充作完整对局或零误差。

经济/BP/BKB其余实际显示留证仍是后续任务，本次只完成眼位链路。地图独立标定、真实2K/高低坡误差、必砍树、截图实际覆盖/性能影响仍未验证；按用户要求等自然对局，不安排专门开游戏。


## 本次离线交付证据

Foresight完整1650项离线回归通过（run-477a0e82ae6d），Insights完整830项通过（run-5555ff1d0a26），均0失败/错误/跳过/后台异常，覆盖源码/数据SHA逐项与当前文件相同。49项眼位/界面专项通过。旧入口夹具因绕过构造缺AutoStartGate的三项错误已补齐；中途源码变化的run-061cfe0c0267明确不作为最终通过证据。

跨进程合成互通：Foresight真实采样循环+真实异步写入器，采集适配器按真实签名替换为生成像素；两个明确游戏范围、2条unlocalized/no_reference与1张真实生成PNG。独立Insights进程通过原有generate日常报告读取，不导入Foresight生产代码；图片SHA、会话分离、原采集时钟与来源清单均检查。多个开发重复样本不计作独立实战。证据保存在Insights diagnostics/ward-camera-interop-20261029/verification.json、producer-result.json、consumer-result.json及其中daily_report路径。

本机已有auto_start/minimap_with_observation/every_hud_attempt均为true，本次没有修改配置；源码入口重启后按已有设置生效。没有游戏、真实采屏/录音、外网、Pi部署、EXE构建或Git提交推送。


## 定位—投影—回执内部一致性（2026-10-29续）

Insights日常接收现增加每帧投影结构核查：原生尺寸、有限且不越界的点、唯一点ID、红绿/砍树布尔标志、保存256点及明确省略数。无定位却带矩阵/投影、错误点或声明数不符拒收。统计的是投影记录/重复观测，不是唯一眼位或识别准确率。

显示回执只在同一run内按frame_id＋公开比赛号（可空）＋game_session_id＋record_session_id＋gsi_source_id完整相同，且只有一条先行定位记录时关联。不按UTC挑近似帧，不选未来记录，不在重复ID里选一条。缺对应采样、来源缺失、重复或顺序不明输出未知；正常限频和队列丢弃可能造成无配对，不能据此说浮层未显示。

“shown但该帧无可靠定位/无投影”“以无点隐藏但实际投影非空”等，只标记录内部矛盾，不当地图错误率或OS显示认证。配准残差统计只汇总已报告图像RANSAC内点误差，未知保留null，不当真实地图落点误差；不计算无可靠桥的显示时延。原始来源字节与旧报告均不改写。

正向双进程生成验证：真实Foresight ORB/RANSAC、投影、异步写入和panel.poll，捕获及Tk输出均模拟；已知纹理平移20/10像素，原控制点200/300恢复到219.9031/310.0071。输出4事件（含参考、2定位、1回执）及2PNG，独立Insights日常报告严格配对1条回执、无记录矛盾。该控制场景只验证2D算法与记录链，不认证Dota地图、3D地面、OS或实战成本。

证据：diagnostics/ward-camera-interop-20261029/positive-producer-result.json、positive-consumer-result.json及其report路径。没有游戏/真实采屏/录音/联网或生产端代码修改。

最终Insights完整836项离线回归通过（run-965b53f89fc9），0失败/错误/跳过/后台异常，337覆盖文件SHA逐项一致。Foresight生产端未再改动，1650项对应530文件仍相同；无参考真实生成包也由当前reader重验，误差保持null。当前证据projection-analysis-verification.json；此前verification.json绑定的是上一轮830版本，不当当前全源码证明。


## 未发布记录的异常状态（2026-10-29续）

Insights现对没有CLOSED.json的目录单独读取有界session.json元数据（默认最多5段、单份128KiB），继续禁止读取事件或图片正文。可区分记录器声明失败且已收尾、失败但未收尾、已声明收尾但缺交接收据、未收尾且进程状态未知、元数据不可用；不会从文件存在/mtime/closed标志推断OS进程存活或退出。报告不使用这些未核验正文的事件/图片数量。

元数据仍先核对公开比赛范围及封存锁，任意异常原文/私有路径不透传，只输出有限格式的错误类型。超出本次元数据检查数另计省略，不把未检查的目录当成没有失败。读取期间出现新CLOSED时要求下轮重新同步，不把它当半成品放行。

生成初始化OSError经Foresight真实worker异常分支与实际异步写入器，正确留session.closed=true/complete=false且不发布CLOSED。独立Insights日常报告只看元数据，就能标“记录器声明失败并已收尾，缺交接收据”，同时evidence_payload_read/process_liveness_verified均false；正文哈希哨兵确认未访问未发布数据。文件在diagnostics/ward-camera-interop-20261029/failure-producer-result.json和failure-consumer-result.json。它是生成故障，不是本机发生过的实战采集故障。

20项专项覆盖失败/缺收据/未收尾、状态元数据限额、封存阻止、错误文本过滤及收据发布竞争；成功定位与无参考两条已有生成日常链也在当前reader复验通过。未改Foresight生产代码、未使用游戏/设备/外网、未改原始历史或提交。

最终完整842项离线回归通过（run-4b1642e778d3），0失败/错误/跳过/后台异常、337覆盖文件SHA一致；Foresight1650项对应530文件仍未改变。当前三类生成链（有定位/无参考/初始化失败）的报告SHA与语义已复验，见unpublished-analysis-verification.json；此前较旧验证文件按其生成版本保留。
