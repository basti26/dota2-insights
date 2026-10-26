# 日常记录一条命令复盘

2026-10-26。源码目录运行：

```powershell
python tools/review_recordings.py --latest 3
```

使用本机忽略配置config/history.local.json中的helper_history/helper_releases。不启动游戏、不采屏录音、不联网、不解析DEM，也不要求用户操作新的EXE。旧报告和原始资料保留，新报告写入Insights自己的diagnostics/record-content/<UTC>/。

## 执行流程

1. helper_handoff.sync接收已关闭启动器包，保持原身份、收尾、限额和来源SHA检查；运行中的启动器不改为closed。
2. match_journal_index.sync和observation_index.sync分别发现源码历史与免安装版diagnostics下已封存的本人GSI/装备逐局目录，原子更新diagnostics/native-match-handoff/index.json和diagnostics/equipment-handoff/index.json。两种索引互不代替；只索引根标识、相对路径、包身份和状态，不复制正文、截图、录音或配置。
3. review_closed_record_content.generate读取两种索引；先按配置根和固定目录布局复查路径，再依据索引记录的manifest原字节SHA进入原生open_rows，分析前后重新验证源文件。旧package继续使用ClosedJournals复验。
4. 输出同一份REPORT.md/report.json，原生本人GSI、原生装备与整次启动包分节；完整性缺口保留，等待/阻止通道不补零，不因其他通道可读而冒充已完成。装备和GSI只按比赛及两种会话明确关联，不以相同UTC猜身份。

源码历史路径形如self-economy/self-economy-<UUID>/closed-matches/match_<ID>/game_<会话>。免安装版只在helper-*或economy-*目录下的Dota2Helper或Dota2Economy/diagnostics按同一固定路径查找，不递归搜索无关目录。最多100个release目录、10000个候选，所选latest为1–25（受旧包入口上限约束）；新原生索引自身上限128项。选择最近目录时使用文件系统时间排序，不把这个时间当游戏数据时间。正文仍用记录内provider/接收UTC。

索引不是持久读许可。正文/清单在索引后变动、SHA不符、身份不符、符号链接/目录联接或越界路径均拒绝本次内容分析；拒绝前不生成成功报告目录。已有索引/报告不因此被删除，也不被默认为新执行成功。原子更新失败保留之前的索引，锁和临时文件收尾；之前索引仍必须在使用时重验。未知进程遗留锁不自动删除。

## 重复范围

多个source/release目录内若发现相同manifest SHA，只保留一个原生包，记录duplicate_packages_omitted。原生包与已闭合启动器包可能来自同一条主日志，此时按根/主日志相对路径/比赛/game_session_id/record_session_id标记also_in_closed_launcher_scope=true，报告明确不可相加；不将两份重复的GSI行汇总成更多独立样本。

两种原生清单分别封存本人GSI与已提交装备观测。BP另以结束阶段前缀及独立迟到比赛绑定接收，见docs/DRAFT_PHASE_PREFIX_V1.md；它不是整局BP闭合。小地图独立run已接入自动索引/报告，见docs/CLOSED_MINIMAP_RUN_V1.md，但比赛归属仍未知；音频、性能仍需原通道闭合或后续逐局边界。不同格式的成功不互相代替。仅文件契约、同包算术和来源验证通过，不能声明实时识别准确率、真实全净资产或地图独立验证通过。

分别执行也仍支持：先tools/sync_helper_records.py，再tools/review_closed_record_content.py。直接读取单个逐局包继续用tools/review_match_journals.py；其核心审计逻辑已移到dota2_insights.match_journal_review，CLI只是入口，不由核心库导入工具程序。

## 离线验收

371项完整离线回归通过（diagnostics/offline-tests/run-09442374e19e/result.json），无失败/错误/跳过/后台异常、源码未在回归中改变。新专项13项包括免安装版正例、重复包、越界/错误布局、重新封存后包身份变化、原子更新失败保留旧索引、原生资料未等待启动器关闭即可复盘，以及原生/旧主日志范围重合。

本机实际执行--latest 3，3个旧闭合启动器包完成来源复验并生成diagnostics/record-content/20261026T080344412289Z/REPORT.md；正式历史没有新格式原生包，报告为0项，不拿生成夹具冒充实战。没有启动游戏、采集、联网或重跑历史录像。已验证流水线和记录字段，不代表其他通道的逐局闭合或游戏识别质量已完成。

装备自动索引接入后的最终393项完整离线回归通过（run-fe22c3a5fbfa），包括免安装版发现/去重、索引后重封存或正文变动拒绝、空通道、与旧装备日志重合，以及同时间不同会话不误绑GSI。正式资料新报告diagnostics/record-content/20261026T082835716251Z/；独立线程生成的装备数据在隔离目录完成1包自动索引→联合报告，见diagnostics/equipment-report-interop-20261026/，没有修改正式配置或索引去引入夹具。

BP索引与独立绑定接入后，完整422项离线回归通过（run-05d069e695c8）。实际再次运行--latest 3，3个已有闭合包均ready_with_gaps，报告在diagnostics/record-content/20261026T093430291319Z/REPORT.md。正式原生GSI、装备、BP前缀均未发现；报告明确0项，不混入diagnostics/bp-bound-report-interop-20261026/的生成资料。两仓库git diff --check通过。
