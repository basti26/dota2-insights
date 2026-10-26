# 开发规则

开发前阅读 `docs/GIT_WORKFLOW.md`、`STATUS.md` 和 `TASKS.md`，检查 Git 状态。

本仓库只负责离线录像、数据分析、评估和数据包发布。实战采集、GSI接收、游戏覆盖层、键鼠控制留在 dota2-foresight（原 dota2-helper）；禁止通过 sys.path、软链接或绝对源码路径导入另一个项目。

历史资料留在原地，通过忽略的 `config/history.local.json` 定位。历史记录只读，不移动、删除、改写或纳入 Git。独立封存比赛保持锁定；不能因拆仓丢失锁名单或读取封存答案。

分析失败、缺失字段、未知身份、未完成解析不能冒充成功或零值；拒收次数不能作为准确率。GSI本人金额可信，下降不擅自平滑。保留采集时间、原值、来源和验证边界。

赛后读取Helper资料前先运行 `tools/sync_helper_records.py`，也可用 `tools/review_recordings.py` 连续同步和复盘，按 `docs/HELPER_RECORD_HANDOFF_V1.md` 检查接收状态，分析前调用 `helper_handoff.verify_package` 重验来源。未闭合、队列未排空或来源仍变化时等待，不拿索引时间冒充数据时间。兼容模式需要观察进程收尾；原生逐局本人GSI按 `docs/PER_MATCH_GSI_HANDOFF_V1.md` 用 `match_journals.open_rows` 进入/退出复验，索引SHA也需匹配，其他通道不停机交接仍未实现。

离线回归可运行，不启动游戏、不采屏、不录音、不访问外网；不自动解析全部历史录像。网络下载、远端服务修改、Git提交推送按用户本次明确范围执行。

原生装备观测按 `docs/PER_MATCH_OBSERVATIONS_V1.md` 使用 `observation_handoff.open_rows` 进入/退出复验，并核对独立equipment-handoff索引的包SHA；元数据声明比赛后必须先查封存锁再读正文。不能用本人GSI索引放行它，也不能把declared图片SHA当作图片已核验。GSI与装备只按明确比赛/会话字段关联，不按UTC猜归属。

BP结束阶段前缀按 `docs/DRAFT_PHASE_PREFIX_V1.md` 使用 `draft_phase_handoff.open_rows`，只读清单截止字节并进入/退出复验。主文件后追加允许，不能把后续尾部混入，也不能用整文件mtime变化否定合法前缀；前缀本身改写/缩短仍拒收。自动索引用独立draft-phase-handoff索引，同时固定前缀SHA与绑定SHA（未绑定也固定为null）。迟到比赛号只接受相同BP会话与前缀SHA的独立显式绑定，不按时间猜；读取期间绑定新增/改变须重新同步，缺UI流保持未知。

首次迁移保留原模块的来源指纹，见 `migration/source-manifest.json`；修改后不能继续声称与原源码逐字一致。

小地图独立run按 `docs/CLOSED_MINIMAP_RUN_V1.md` 使用 `minimap_handoff.open_rows`，固定独立minimap-run-handoff索引清单SHA并进入/退出复验；源session和固定五流均校验。当前match_id固定未知，不能按UTC关联GSI/装备，不代表整局覆盖；图片声明与文件核验分开。旧run不补写新收据；同run不同收据整组阻止，同包去重，与旧启动器重合流逐项标记不可累计。
