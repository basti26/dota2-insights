# 录音GSI旁证的明确来源身份

后续进展：同日已接通逐局音频封存、自动索引/日常报告、延后参考盲测及逐gsi_source_id原生日志核查，见PER_MATCH_AUDIO_HANDOFF_V1.md与CLOSED_AUDIO_INFERENCE.md；下文保留来源扩展最初验证时的范围，不能用初始未完成状态否定后续实现。真实新格式资料、精确声音时序/可闻性仍未认证。

2026-10-27。Foresight未来实战录音增加可选来源上下文；Insights只交换记录，不导入对方源码。

原音频context.game_session_id仍是本人统计模块的本地会话。不得将其当作原生GSI/装备运行会话。新增可空 `context.recorded_source_context`：

| 字段 | 约束 |
|---|---|
| schema_version | 整数1 |
| match_id | 正数十进制字符串或null；先查封存锁 |
| game_session_id | game_加32位小写hex |
| record_session_id | run_加32位小写hex |
| gsi_source_id | gsi_加32位小写hex |
| provider_at_utc | 与外层原provider_timestamp对应的UTC ISO值一致 |
| basis | accepted_same_GSI_callback_not_audio_sample_time |

`audio_source_context.validate_reference_context` 拒绝额外字段、错误版本/类型、同包比赛冲突或provider不符。旧记录缺失/null均保持未知，不按时间推算或回填。它只确认记录中声明的来源格式/同包一致性，尚未与原生GSI日志逐条核对，不认证音频采样时刻。

片段另有可空 `recorded_source_scope`，恰含match_id、game_session_id、record_session_id三项，公开比赛必须与原 `match_association.match_id` 一致。生产端在已知范围变化时先保存旧块；分析端在读取该片段WAV前检查此范围。原音频本地会话字段保留。日常技术报告新增 `segments_with_explicit_runtime_scope`，并保持 `native_gsi_source_join_verified=false`；有运行范围不等于片内每个声音块都有精确身份。

当前音频整体仍要求闭合录音及原来源SHA前后复验；这个可选扩展不放行未闭合录音、不实现不停观察时逐局交接，也不让GSI进入声音分类器。`AudioAnalysisInputs.reference_rows` 仍只在预测冻结后的旁证阶段读取GSI；身份校验不改变盲测顺序。

## 验证与后续

- 完整589项回归通过，失败/错误/跳过/后台异常均0、回归期间源码未变：`diagnostics/offline-tests/run-1da32382b267/result.json`。相关专项22项通过run-c0ddf5a81ee5。
- 助手对应完整1574项通过run-9d83b99e9ac6；新随机来源ID不提高旁证采样频率，缺上下文清空旧身份，人机允许空公开比赛号，手动斧王岛格式保持。
- `diagnostics/audio-source-context-interop/20261026T194959427589Z/verification.json`：独立生产进程执行真实启动器回调函数、写入真实WAV/片段并收尾，独立Insights读取800帧/0.05秒生成PCM，运行范围与同次来源ID一致，源SHA前后不变。仅生成GSI/PCM，没有游戏或录音设备，不当作实战或可闻性验收。

下一步仍是音频逐局封存/独立索引，以及合格新实战资料中来源ID覆盖和真正原生日志配对。旧录音不改写；缺公开比赛号保留本地会话，不借相近UTC补编号。没有修改声音门槛、旧候选或active，没有启用新预警。
