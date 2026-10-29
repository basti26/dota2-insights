# 开发规则

## 自然对局取样工作方式（2026-10-29用户要求）

后续不安排用户专门开游戏、回放或斧王岛配合验证，不由模型操作游戏逐点检查。采集需求先写成Foresight可实施的有界自动记录，再利用用户自然对局与Pi已解析录像做赛后复核；每个待办明确所需字段、触发条件、来源和失败状态。自动记录不等于独立真值，听审/地图/图像认证缺证据时继续保留未完成。见docs/AUTOMATIC_EVIDENCE_WORKFLOW.md。

开发前阅读 `docs/GIT_WORKFLOW.md`、`STATUS.md` 和 `TASKS.md`，检查 Git 状态。

本仓库只负责离线录像、数据分析、评估和数据包发布。实战采集、GSI接收、游戏覆盖层、键鼠控制留在 dota2-foresight（原 dota2-helper）；禁止通过 sys.path、软链接或绝对源码路径导入另一个项目。

历史资料留在原地，通过忽略的 `config/history.local.json` 定位。历史记录只读，不移动、删除、改写或纳入 Git。独立封存比赛保持锁定；不能因拆仓丢失锁名单或读取封存答案。

分析失败、缺失字段、未知身份、未完成解析不能冒充成功或零值；拒收次数不能作为准确率。GSI本人金额可信，下降不擅自平滑。保留采集时间、原值、来源和验证边界。

赛后读取Helper资料前先运行 `tools/sync_helper_records.py`，也可用 `tools/review_recordings.py` 连续同步和复盘，按 `docs/HELPER_RECORD_HANDOFF_V1.md` 检查接收状态，分析前调用 `helper_handoff.verify_package` 重验来源。未闭合、队列未排空或来源仍变化时等待，不拿索引时间冒充数据时间。兼容模式需要观察进程收尾；原生逐局本人GSI按 `docs/PER_MATCH_GSI_HANDOFF_V1.md` 用 `match_journals.open_rows` 进入/退出复验，索引SHA也需匹配。装备、BP前缀、小地图run和音频分别按下文独立协议准入，不借GSI成功放行；性能目前仍要求原采集器收尾。

离线回归可运行，不启动游戏、不采屏、不录音、不访问外网；不自动解析全部历史录像。网络下载、远端服务修改、Git提交推送按用户本次明确范围执行。

原生装备观测按 `docs/PER_MATCH_OBSERVATIONS_V1.md` 使用 `observation_handoff.open_rows` 进入/退出复验，并核对独立equipment-handoff索引的包SHA；元数据声明比赛后必须先查封存锁再读正文。不能用本人GSI索引放行它，也不能把declared图片SHA当作图片已核验。GSI与装备只按明确比赛/会话字段关联，不按UTC猜归属。

BP结束阶段前缀按 `docs/DRAFT_PHASE_PREFIX_V1.md` 使用 `draft_phase_handoff.open_rows`，只读清单截止字节并进入/退出复验。主文件后追加允许，不能把后续尾部混入，也不能用整文件mtime变化否定合法前缀；前缀本身改写/缩短仍拒收。自动索引用独立draft-phase-handoff索引，同时固定前缀SHA与绑定SHA（未绑定也固定为null）。迟到比赛号只接受相同BP会话与前缀SHA的独立显式绑定，不按时间猜；读取期间绑定新增/改变须重新同步，缺UI流保持未知。

首次迁移保留原模块的来源指纹，见 `migration/source-manifest.json`；修改后不能继续声称与原源码逐字一致。

逐局性能按 `docs/PER_MATCH_PERFORMANCE_HANDOFF_V1.md` 用 `performance_handoff.open_package` 进入/退出复验，先查小清单封存身份，再校验固定四源及原生退出码/ETW诊断；只消费其自有采集器已关闭的包。自动链使用独立performance-match-handoff索引，包SHA/明确身份/来源成本必须重验，同采集冲突清单整组阻止；新旧格式按明确采集、比赛/双会话及已核验CSV/context SHA标记跨路径重合，计数不可相加。GSI/经济等观察仍运行不等于该采集器未关闭。只用明确范围内新鲜前台QPC相邻窗口，不拿封存/HTTP/索引时间当游戏帧时间；没有整局覆盖或助手开关对照就保持未验证。

逐局音频按 `docs/PER_MATCH_AUDIO_HANDOFF_V1.md` 用 `audio_match_handoff.open_package` 进入/退出复验，并固定独立audio-match-handoff索引清单SHA：先以小清单检查封存身份，独立元数据与原capture根WAV都重验；原run可继续写其他范围，不把全局summary未关闭当成本包失败，也不能据本包推断整run关闭。公开比赛号可空，不按UTC补编号。相同capture运行及范围的不同有效清单整组阻止；旧整次录音与逐局记录按明确capture ID/文件名/已核验SHA标记重合，不可累加。日常接收成功不等于音频精确同步、逐来源原生GSI join、可闻性或整局完整。

音频盲测用 `closed_audio_analysis.open_match_analysis_inputs` 适配新逐局包：预测前只对参考流做原字节完整性检查，不解码技能/CD；既有PCM算法不接收参考能力或期望英雄。预测冻结并核对来源/预测/扫描SHA后才释放语义旁证读取，完成收据必须在源/库退出复验之后。不能把参考延后模式的reference_semantics_verified=false当作技术报告已核验参考内容，也不能用生成正候选冒充真实可闻性或准确率。

音频逐来源核查用 `audio_gsi_alignment`，只能在语义参考已准入的音频open_package中核对原生GSI：公开比赛号、双会话及gsi_source_id精确一致，重复ID拒绝任选，缺记录不按时间补齐；共享字段有值才比较，0/false不得当缺失。日常只放摘要；这类来源覆盖不等于声音可闻、施法/CD精确边界或整局记录完整，不能把语义核查塞进盲测冻结前。

小地图逐行GSI来源配对按 `docs/MINIMAP_GSI_SOURCE_ALIGNMENT.md`：公开比赛号、双会话及gsi_source_id四项精确相同，再核对provider/比赛钟/阶段；重复来源ID拒绝任选，缺失ID不按相同或临近时间补齐。原生GSI时间戳实际可重复，旧资料不回填。来源配对不认证像素身份、地图标定或预警。

小地图独立run按 `docs/CLOSED_MINIMAP_RUN_V1.md` 使用 `minimap_handoff.open_rows`，固定独立minimap-run-handoff索引清单SHA并进入/退出复验；源session和固定五流均校验。基础match_id固定未知，不能按UTC关联GSI/装备，不代表整局覆盖；可选帧来源扩展按 `docs/MINIMAP_FRAME_CONTEXT_V1.md` 校验，关联原生GSI必须公开比赛号及双会话完全相同，空比赛号不回填，像素身份仍未认证。图片声明与文件核验分开。旧run不补写新收据；同run不同收据整组阻止，同包去重，与旧启动器重合流逐项标记不可累计。
