# Helper记录交接：只读接收端v1

当前实现兼容已有Helper的 `field-recording/run_*/recorders.json`。不修改Helper，不移动原记录，不复制截图、声音或日志正文。由insights赛后执行命令，生成可复核的来源清单；不需要操作分析EXE。

```powershell
python tools/sync_helper_records.py --latest 3
```

本机根目录读取忽略的 `config/history.local.json`。搜索范围仅为：配置的helper_history，以及helper_releases中已知Helper/Economy程序目录下的diagnostics。最多取最近25个启动记录，默认5个；不递归扫描所有历史、录像或封存答案。

## 什么时候接收

1. 启动器索引明确 `closed=true`，且起止时间是有效UTC。未闭合或刚变动的索引等待，不打开其日志正文。
2. GSI/经济记录需有最终摘要、排空确认以及零pending/inflight，不能仍接受数据。BP必须有相符session的结束文件；小地图必须确认关闭且待写图像为零；FPS需关闭确认；音频需终止状态和启动器收尾确认一致。
3. JSONL逐行完整、身份不冲突；每个来源读取前后及全流程结束后再核对指纹。已关闭但尾行不完整、字段错误、文件缺失或来源仍变化，分别阻止接收或等待。
4. 记录只按显式比赛号、game_session_id和record_session_id关联，不用相邻时间猜英雄/比赛，不将启动器最后一个BP会话套到前一场比赛。人机没有比赛号时保留本地会话身份，不伪造可查录像的编号。

**文件已写完不等于比赛结束。** `match_end_evidence` 只有同比赛本人GSI明确记录POST_GAME才标为 `own_recorded_gsi_post_game`，否则为 `not_observed`。中途停止也可得到闭合日志，但不会被称为完整对局。

## 输出与契约

- `diagnostics/helper-handoff/index.json`：本次选中的最近N个启动记录及接收状态，不是完整历史数据库。
- `packages/<SHA256>.json`：不可覆盖的记录清单。相同来源与同一生成器再次运行复用同一清单；原文件发生变化或生成器变化会产生新快照。旧快照保留。
- `schema_version=1`、`kind=helper_record_package`、launcher_run_id、显式matches、components、sources、generator协议/代码SHA。
- 来源只记录配置根的逻辑键、相对路径、字节数、SHA及结构统计，不含绝对用户目录、原始GSI、图片base64或音频。
- 观测、采集、写入、接收UTC各自统计；不把索引生成时间当作画面或数据时间。
- `binary_assets_verified=false`：当前核验日志与收尾元数据，未逐张读图、未逐段听音，不宣称二进制素材或识别质量通过。

|状态|含义|
|---|---|
|ready|已配置通道的闭合日志通过结构与稳定性检查，无已知记录警告|
|ready_with_gaps|至少一条通道可接收；其他通道、配额、丢弃或覆盖范围有明确缺口|
|waiting|索引未闭合、来源尚未安静或读取时仍在变化|
|blocked|无可接收通道，或格式、身份、路径、指纹、资源边界不通过|

通道自己的 `closed_verified` 只证明上述接收条件，既不是完整比赛认证，也不是模型准确率。小地图、声音和性能文件跨多局共享时仅标 `launcher_scope_only`，须另行按时钟/身份对齐，不能整份归到每一场。

进入实际分析前必须调用 `helper_handoff.verify_package(package, roots)` 重验来源。旧索引不是持续有效的读许可。源路径受已知记录布局白名单限制，比赛锁在日志读取前执行；验证器不允许重定向到任意历史文件或封存答案。

资源边界：元数据512KiB、单JSONL128MiB、CSV512MiB、单次启动记录累计512MiB；JSONL最多10万行，CSV最多200万物理行。CSV行数含表头，不当作FPS/帧数。达到接收预算是“未核验”，不证明原记录损坏或实际丢失。

## 当前实机记录观察

本次选中两个已关闭启动记录，显式对应三场已知比赛（编号保存在本机报告）；另一个未闭合启动记录留在waiting。经济、BP、小地图、画面、GSI和性能资料按实际文件分别核验；部分截图/采样配额告警保留。

音频最终索引说shutdown_complete，但summary.json仍有finalizing/worker_alive标志；当前标记为未通过收尾门禁，不推断它正在运行，也不推断声音文件损坏，不用其他记录器的成功覆盖这个矛盾。

## Helper持续运行时逐局交接（本人GSI已接通，其余未实现）

2026-10-26新增本人GSI独立逐局文件协议，见[PER_MATCH_GSI_HANDOFF_V1.md](PER_MATCH_GSI_HANDOFF_V1.md)。不依赖启动器整体closed，Insights单独验证与读取，其他通道不能借用该成功状态。下面的兼容模式边界继续适用于旧资料和其余通道。

当前兼容模式等待整个观察进程关闭，不会因GSI出现POST_GAME就提前读取仍由记录器写入的共享文件。多局记录可一次接收，但并非已实现“助手不停、每局自动交接”。

Helper侧下一步应在每局记录切换后，等待该局各记录队列排空，再发布独立的每局闭合标记；整体启动器可继续运行。共享GSI/声音/性能日志应提供不可变的截止字节偏移与前缀SHA，或者发布该局本身独立写入的文件，避免复制整批原数据。需同时列出：比赛/本地会话身份、每个通道的收尾/失败状态、采集范围和配额缺口，且音频终态在worker真正收尾后发布。

本人GSI原生交接格式已在两仓库分别实现，其余通道仍需推进。不能将整个启动器索引伪改为closed来替代逐局协议。未给其他任务发消息或部署Pi；该文档是两边继续开发的接口依据。

验证：245项完整离线回归通过，包含31项交接专项；真实记录重复扫描复用同一清单，接收包再次核验来源。证据在diagnostics/helper-handoff/verification-20261025.json和REPORT.md，原始资料不进入Git。

## Foresight产品名兼容（2026-10-28）

实时产品统一命名Foresight；新完整版布局foresight-*/Foresight/diagnostics，经济版foresight-economy-*/ForesightEconomy/diagnostics。发现与各原生记录索引也继续允许原helper-*/Dota2Helper及economy-*/Dota2Economy布局。helper_history/helper_releases配置键、协议kind、索引目录和工具文件名保持v1兼容，不能对已有清单及来源路径作字符串替换。本批未移动历史资料或放宽SHA、路径、身份、封存与收尾校验。生成目录和拒收边界109项相关离线回归通过run-237fbf22294e。
