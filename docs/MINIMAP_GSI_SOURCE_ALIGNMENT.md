# 小地图与GSI来源记录配对

2026-10-27。先把明确来源记录配准，再为本人像素/世界坐标标定准备资料；不按最近时间或相同时间戳猜测同一条GSI。

## 实际发现

最新两份原生本人记录共4946条，provider时间没有缺失，但并不唯一：

| 比赛 | 记录数 | 不同provider时间 | 重复的时间键 | 同时间键下坐标或比赛钟不同 | 同时间最多记录 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 9062765692 | 2768 | 2645 | 117 | 107 | 3 |
| 9062710567 | 2178 | 2046 | 127 | 101 | 3 |

证据：diagnostics/gsi-evidence/20261026T181313023994Z/。读取前同步、原包进出复验通过，原坐标4764条仍保留。旧记录没有来源ID，不能赛后补写或按时间合并成精确配对。

## 新记录的来源ID

Foresight GameSession对每次接受的回调生成gsi_source_id，格式gsi_加32位随机十六进制。它不同于比赛号、运行时会话、本人日志sequence和队列relay sequence。WindowsEconomyService在本次锁内返回该回调的上下文快照；启动器直接把此快照传给本人记录器，不先解锁再读取可能已被下一次同秒请求替换的最新上下文。

本人记录仅在公开比赛号、provider UTC、双会话有效且一致时复制来源ID；原有采样签名/关键事件/速率不包含此随机ID，不因每次ID变化而提高写入频率。被采样省略、限额或丢弃的来源不伪造记录。小地图的可选来源白名单同步携带该ID；缺失ID的旧帧仍可以做会话分组，但不能进行精确记录配对。

## 使用

在Insights目录运行：

```powershell
python tools/prepare_minimap_gsi_alignment.py --run <32位小地图run编号> --latest 25
```

入口只扫描已配置固定根和闭合原生目录，先同步两个独立索引；latest为1–128的有界扫描，所需run不在扫描范围时明确报错，不找临近run替代。输出在Insights diagnostics/minimap-gsi-alignment/<UTC>/，不改正式配置或来源数据。

日常tools/review_recordings.py已自动执行相同配对，联合REPORT.md/report.json的每个小地图run包含gsi_source_alignment摘要：样本数、已记录来源ID数、精确来源配对数、存活/新鲜/非暂停位置证据数及未配对原因。不把整份逐帧载荷重复塞入总报告；详细坐标仍用上述专门命令导出。原生GSI索引缺失单列missing_not_treated_as_zero，旧上下文无来源ID与未找到已选择原生记录分开，扫描窗口之外的包不冒充不存在。

## 来源范围选择诊断（2026-10-29）

按“最近N份”分别选择不同通道时，最近小地图run可能属于更早的比赛。零配对不能直接归因于GSI缺失或采样故障。新增`native_scope_selection`，逐个已知比赛/双会话列出涉及帧数、不同来源ID数及实际进入/退出核验的原生包：

- scope_not_in_selected_index：本次索引没有选中对应范围，不证明源文件不存在。可显式扩大有界latest扫描，不按时间猜替代包。
- matching_index_entries_not_ready：对应索引项未就绪，不读其正文或冒充已核验。
- matching_packages_verified：匹配范围包已准入并退出复验；具体来源ID仍可能缺失、重复或内容不符，继续使用原逐行门禁，不因范围找到就强行配对。

该摘要也进入日常JSON及中文报告。公开比赛号未知的上下文不生成可配对范围；同ID多帧仍按帧分别报告，不将涉及帧数、不同来源ID数或跨run数据当独立比赛数。

实际同包对照：三个近期run共12010帧，原索引前三份未覆盖其中两个旧比赛。显式最多25份扫描后，两段分别得到1527/767帧精确来源配对、1278/698帧可用位置证据；另一段2937帧中的2936帧公开比赛号未知，扩大扫描也不能修复，保留本地身份。不是新采集或新独立验证集；匹配不到的来源可能与GSI日志采样省略有关，未认证具体原因。原始记录不回填、不提高生产频率。

证据`diagnostics/minimap-scope-review-20261029/comparison.json`及`REPORT.md`保留两个索引SHA和同包两种范围结果；详细配对输出`diagnostics/minimap-gsi-alignment/20261028T215311045422Z/`。像素身份、图像文件与地图标定仍未因来源配对通过而认证，未启用实时预警。

匹配键为公开比赛号、game_session_id、record_session_id、gsi_source_id四项完全一致。随后核对原provider UTC、比赛钟和游戏阶段；同ID存在多行时，即使坐标相同也报告source_id_ambiguous，不任选一条。缺记录、旧ID、空公开比赛号、身份不同或内容冲突保持未知；不同公开号/本地会话不自动回填。没有时间最近匹配或回溯猜测分支。

生成前固定两个索引的包SHA，用原生open_rows和minimap_handoff.open_rows进入/退出复验；所有来源退出复验完成后才创建报告目录。report.json最后原子发布，REPORT.md没有独立完成语义。每行保留小地图原行SHA、原采集UTC/单调时钟、声明图片引用、来源包SHA及本人行号/sequence。

精确来源匹配后才导出同一原记录的本人英雄/坐标/存活/暂停/原时间，复用现有GSI具名证据门禁。position_evidence_usable仅表示本人位置记录存活、新鲜且明确非暂停；死亡坐标保留但不作可用标定位置。它不代表PNG已核验、本人标记已找到或坐标映射正确；images_verified、pixel_identity_verified、calibration_verified保持false，不自动训练或启用预警。

## 离线交接证据

真实小地图worker及本人记录器使用生成的像素/载荷，发布两份不同比赛与双会话的原生包。独立Insights进程同步和配对得到2条exact_recorded_source_id，坐标(100,-100)与(200,-200)，原来源文件SHA前后一致。证据：diagnostics/minimap-source-id-interop/20261026T181327883301Z/verification.json及isolated-consumer下报告。

这份夹具不在正式历史配置中，不是实际游戏或地图正确性证据。游戏采样、切局、采样省略后的未知比例、实际性能及本人像素定位仍待后续实战；旧资料和旧EXE保持原样。

最终完整离线回归：Insights567项（diagnostics/offline-tests/run-d1902a6d0cc2/result.json），Foresight1567项（run-c58124833c3a），失败/错误/跳过/后台异常均0、源码未在回归中变化。包含同秒不同来源、旧上下文兼容、采样频率不变、源ID重复/内容冲突、源变化拒绝，以及同单调时钟刻度合法/倒退拒绝。20轮生成夹具验证了后一个边界的稳定性，未发送原生游戏输入。

接入联合报告后Insights569项完整离线回归通过（run-b15133ce4022），无失败/错误/跳过/后台异常、源码未变。真实写入器的生成资料由独立进程走日常入口，报告精确配对2条、位置证据可用2条，来源SHA前后不变；证据diagnostics/minimap-source-id-auto-report/20261026T182457387212Z/verification.json。本批不改助手代码，不重跑其回归；旧实战资料仍没有新来源ID，不回填。
