# 独立逐局录音包读取v1

逐局包的冻结候选复听导出已接入原CLI，见[AUDIO_CANDIDATE_REVIEW.md](AUDIO_CANDIDATE_REVIEW.md)。裁片只使用原PCM和冻结候选，参考流只验字节不解码；逐来源语义GSI核查仍使用本文件后文的独立技术复盘路径，两者不能互相放行。

2026-10-27。显式读取Foresight写入线程已封存的本局音频元数据，允许该录音run继续记录其他场次，不要求整个观察进程退出。只交换数据，不导入Foresight源码。

## 读取入口

```powershell
python tools/review_audio_match.py <run_.../closed-matches/scope_...目录>
```

可传`--expected-package-id <manifest原SHA256>`固定已索引或此前确认的包。报告写入新的diagnostics/audio-match-review/<UTC>/report.json，不覆盖输入或旧输出。

`audio_match_handoff.verify`返回已验证的清单、包ID、两个独立元数据流及PCM技术结果；`open_package`进入/退出重验固定包和所引用WAV。原run的summary、主日志或后续WAV可变化，不将它们误当本局包的一部分。

## 协议

- capture目录必须为run_<32hex>，包目录固定closed-matches/scope_<64hex>，任何路径越界、符号链接/目录联接均拒绝。
- 清单kind=foresight_audio_match_handoff、schema_version=整数1。三个范围字段为match_id（可空）、game_session_id、record_session_id；scope目录是这三字段排序/紧凑JSON的SHA256。
- 先以小清单身份查公开封存锁，再读COMPLETE、元数据和WAV。COMPLETE必须严格等于清单原SHA加LF。缺失/不一致不读正文。
- end_context只含原match_id、provider_timestamp、game_state、clock_time_s及recorded_source_context；要求POST_GAME、来源时间/身份与清单一致。该来源是录音写入线程消费赛后请求的证据，不是声音采样时间。
- 固定两个流segments.jsonl和reference-gsi.jsonl，各自字节数/行数/SHA；总元数据≤64MiB，单行≤64KiB，片段≤5000，旁证≤100000。读取时和结束时复验，异常不补0。
- 每片段必须明确属于本包运行范围，保留旧音频私有会话字段；原WAV仅从capture根的audio-NNNN.wav读取，验证SHA、16kHz单声道int16 PCM、实际长度/时长及片段偏移不重叠；单WAV≤4MiB，总WAV≤512MiB。
- 旁证是独立GSI流，记录角色仍为post_test_reference_only，保留同次回调来源ID与两个运行会话；不进入声音分类器，不按邻近UTC与原生日志join。
- health的四个布尔量是音频worker累计的错误/旁证错误/限额/结束请求错误；有任一项即recording_integrity=gaps，否则complete。此complete只指本范围交付文件和该口径健康，不证明整局未漏录或旁证完整。

文件写入由生产端原录音线程执行：先保存匹配尾块，关闭/fsync逐局元数据，再写清单及最后的COMPLETE。赛后请求为有界非阻塞队列、按范围去重，未见赛后/未录到音频不发布；新场次不改旧包。WAV不复制，仅保留原root中的不可变文件引用。交付错误不能停止原录音或伪造COMPLETE。

## 验证及未完成项

独立生成协议/PCM夹具覆盖运行继续、封存锁先于正文、缺COMPLETE、索引SHA变化、分析退出来源变化、WAV变动、跨会话/错误结束上下文、路径/容量、空公开比赛号和健康不一致。

最终598项完整离线回归通过：diagnostics/offline-tests/run-eec8d7c515c3/result.json；助手1584项通过run-a0e8b5397b73。失败/错误/跳过/后台异常均0，回归期间源码未变。初次run-a67de9a6c468的CRLF夹具导致3失败/2错误，修正生成标记字节后同批24项run-39b510f57cdb通过，读取器未放宽COMPLETE比较。

跨进程证据：diagnostics/audio-match-interop/20261026T200920190556Z/verification.json。实际生产_record_take由生成回调/GSI/模拟前台驱动；消费者运行时通过Popen.poll确认生产进程仍活跃，首局1片段/0.6秒/6旁证可读，第二局也封存，首局文件及WAV SHA前后不变。只用生成声音，不是实战、音效准确率或真实收尾性能认证。

自动发现/持久索引及日常联合报告已在后续批次接入，见下节。新包盲测推理入口也已接通，见CLOSED_AUDIO_INFERENCE.md：预测前参考流只验字节/行数，不解码技能；冻结后才放行完整旁证检查，最终收据固定本包身份，不要求整run退出。原生GSI逐来源join、精确音频时序与真实对局验收仍未完成；已有旧录音不补写新清单。目录身份为空时保留null和本地会话，不猜比赛号。未改任何旧音效库、阈值、active或HOME。

## 自动索引与日常入口

原命令不变：

```powershell
python tools/review_recordings.py --latest 3
```

`tools/sync_helper_records.py`也接入同一索引。新 `audio_match_index.py` 只扫描本机已配置helper_history和helper_releases的固定源码/免安装版布局：match-audio/run_*/closed-matches/scope_*/manifest.json。release只认既有helper-/economy-目录及Dota2Helper/Dota2Economy/diagnostics，不递归搜索其他目录。最多100个release、10000个候选；原生索引latest上限128，完整日常命令还受旧启动器1–25限制。先按清单mtime挑候选，再去重；mtime只是选择顺序，不是音频或比赛时间。

索引在本仓库diagnostics/audio-match-handoff/index.json，原子替换并独占同步锁。只存根标识、相对目录、包SHA、明确范围及计数；不复制WAV/GSI。相同清单副本只留一份；同一capture运行及相同三范围字段出现不同有效清单时整组阻止，不任选一个。失败更新保留旧索引，旧索引也必须读取时重新验证。

日常报告新增audio_match_reports及独立中文技术表，按索引SHA重新进入/退出单包读取器；索引后正文变动、重新封存或身份重标都拒绝本次成功报告。缺索引、空索引和被阻止包区分，不将不可用当零音频。

与旧全次录音的重合以明确capture运行ID、WAV文件名和两边已核验字节SHA匹配，另报同根/同相对路径重合数。因此便携版副本即使路径不同仍不能作为新独立样本；不靠录音相似度或邻近UTC猜测。旧audio_segments_verified与新audio_match_segments_verified分别报告，不相加。

与原生本人GSI仅在公开比赛号非空且两个运行会话完全一致时列出关联包；相同UTC但不同会话不关联。仅有范围关联不能把native_GSI_journal_join_verified视为true，逐来源核查另见下段。人机空编号只保留本地会话，不追认旧资料。

后续已增加逐来源核查：日常报告的gsi_source_alignment按同一公开比赛号、两个运行会话及gsi_source_id四项精确相同查找原生记录，再核对provider时间、比赛钟、阶段及双方有值的英雄身份。来源ID重复时即使内容相同也保留ambiguous；没有落盘或不在所选包中的回调不按时间戳补齐。缺基础内容是not_comparable，不把缺失写成数值矛盾。

共享技能字段按固定槽位和名称比较level/cooldown/charges/can_cast/ability_active/ultimate；只比较双方明确报告的有效值，0和false有效，缺字段不转0，native探针省略技能时不当作冲突。共享值不一致则该行不标为确认来源。报告给出配对覆盖、缺口原因和可比字段数，不把采样CD值相等当作精确施法/冷却边界或声音真值。

实现为audio_gsi_alignment：prepare提供带原生包SHA/行序号的详细行结果；日常review在原open_package生命周期内调用核心核查，GSI文件通过open_rows进入/退出复验，音频仍由外围退出复验。日常JSON只保存摘要，不重复塞入全部GSI行。仅当非空的本包已有旁证行全部找到唯一、相容来源时，native_GSI_journal_join_verified才为true；它不表示整个对局或所有回调都有记录。没有选中原生包时，native_sources_verified_before_and_after为null，来源配对保持未知。

这项核查读取语义GSI，属于独立技术复盘，不进入音频分类器；传入盲测的延后参考包会在打开原生GSI前被拒绝，不能借此绕过预测冻结门禁。人机空公开比赛号仍只保留本地范围，本版不做原生GSI逐局包配对；不回填旧资料。

实际双写入器生成验证位于diagnostics/audio-gsi-source-interop/20261026T205530234937Z/verification.json：默认SelfStatsRecorder及真实录音_record_take接收同次生成GSI，独立进程在生产仍活跃时跑日常入口，每局6条旁证/6条唯一原生记录、30个共享字段相容，继续第二局后首局两类源SHA不变。夹具通过生成stunned状态变化触发原有关键记录保留，未改采样设置；这不是正常实战100%覆盖，更不是音效准确率。尚无真实新格式比赛据此认证。

来源核查最终628项完整离线回归通过：diagnostics/offline-tests/run-c99a8f073a46/result.json，无失败/错误/跳过/后台异常、回归源码未变。10项新增检查覆盖同秒不同ID/会话、重复ID、时钟/英雄/共享技能值冲突、缺字段与缺索引不补0、索引后来源变化、日常摘要及拒绝盲测延后参考包。助手生产源码、原采样配置和旧记录未改。

实际生产_record_take的生成回调跨进程自动验证：diagnostics/audio-match-auto-interop/20261026T202207527157Z/verification.json。生产进程确认仍存活时，独立进程调用实际日常run入口，自动发现并报告首局1片段/0.6秒/6旁证；第二局结束后自动索引2局，首局来源SHA不变。只读生成数据，不是实战时序或性能验收。

本机旧资料兼容运行报告：diagnostics/record-content/20261026T202331724926Z/REPORT.md。3个已有闭合启动包、4个性能范围、961段旧录音、2份原生GSI/装备/BP前缀及1个小地图run保留；新音频逐局格式0包，未将旧记录或生成资料写进正式历史。这次实际运行发生在便携版跨路径重合分支补齐前；该分支用独立生成复制场景另行回归，不把本机0包当该分支的验证。

自动链最终610项完整离线回归通过：diagnostics/offline-tests/run-dd4155a3d2ac/result.json，失败/错误/跳过/后台异常均0，回归期间源码未变。12个新自动索引场景包含源码/便携副本、跨路径旧流重合、空编号、明确会话关联及相同UTC不同会话不关联、索引重标/来源变动/重新封存拒收和原子更新失败保留旧索引。初次43项专项中1项旧流夹具被真实静默期门禁暂缓，改用生成闭合夹具的显式零静默期准入后同批run-06d714a60a3a通过；没有降低生产等待条件。助手源码本批未改，不重复跑其1584项回归。
