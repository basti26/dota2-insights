# 小地图采样来源上下文 v1

2026-10-27。为未来自动采样增加可选的跨进程来源上下文，供离线分析按明确会话找到原生GSI资料。旧记录原字节不变，不按相近UTC、文件mtime或同一启动器范围补比赛身份。

## 生产与时序

Foresight已有观察进程把白名单game_context交给启动器；启动器沿现有GUI轮询，将其写入容量4的非阻塞队列，仅自动实战小地图录制接收。没有观察进程、停止中、非Windows正常游戏模式时发送空上下文使旧身份失效。每个新录制进程有独立32位relay_id与递增sequence，不复用上一run的队列。

接收端只选择发送单调时钟不晚于该帧captured_monotonic的最新消息，不使用识别完成或落盘时间。消息到采集最多2.5秒，provider原始UTC到采集UTC为0–5秒；未来消息、过期/错误/缺失上下文、视频文件输入都保留unknown及原因。失效消息之后不能退回更早身份。队列满会丢弃本次发送，不阻塞采样或额外启动识别线程；后续上下文过期即未知。

该关联的固定basis为last_explicit_GSI_context_before_capture_not_frame_atomic：它是采样前最近明确的GSI来源，不是GSI与像素原子同步认证。切局边界、传输延迟和画面真实性仍有不确定性。

## 可选字段

session.json及正常收尾record-handoff.json同时包含frame_context_relay：schema_version=1、relay_id、basis、排序去重的declared_match_ids（最多32个非空公开比赛号）。接收器先检查声明及封存锁，再打开固定流；两处摘要必须一致。

samples.jsonl的sample行可含game_context_binding。旧run没有该字段仍按原契约读取。

| 字段 | 约束 |
| --- | --- |
| schema_version / status | 1；explicit_last_context，或unknown并附reason |
| relay_id / basis | 必须与摘要一致 |
| sequence | 正整数；消费流不可倒退，同序号不可重新定义上下文 |
| sent_monotonic_ns / capture_monotonic_ns | 发送不晚于采集，采集值须与行内captured_monotonic一致 |
| provider_age_at_capture_s | 必须与两份原UTC计算相符，不能用HTTP接收时间替代 |
| context.match_id | 正数字符串或null；非空值必须在摘要声明且不属封存比赛 |
| context.game_session_id | game_加32位十六进制运行时会话 |
| context.record_session_id | run_加32位十六进制记录会话 |
| context.provider_at_utc | GSI原provider的UTC；缺失不造时间 |
| context.game_state | PRE_GAME或GAME_IN_PROGRESS |
| context.clock_time_s | 有限数值或null，保留比赛时钟原口径 |
| context.gsi_source_id（可选） | gsi_加32位随机十六进制，本次接受回调的来源ID；旧上下文可缺失 |

上下文只准这些字段，不携带原始GSI、路径、图片、token或其他配置。基础run/sample的match_id仍为null，match_association仍为unknown_no_time_inference，scope仍是closed_standalone_run_not_whole_match；不把一个run晋升为整局覆盖。

## 独立消费与报告

Insights的minimap_game_context.py独立校验，minimap_handoff.open_rows沿用清单SHA、源session/五流进出复验及固定索引包SHA。联合入口tools/review_recordings.py先同步，再按(match_id, game_session_id, record_session_id)三项完全一致，列出linked_native_gsi_packages。同比赛同时间但记录会话不同也不能关联。

match_id=null时可保留明确本地双会话，报告为“未知（仅本地会话）”；不借同会话的其他帧回填公开比赛号，也不连接要求正比赛号的原生逐局包。来源关联、PNG完整性、地图标定、英雄身份与可见性是不同证据层；pixel_identity_verified仍为false，不据此计人数、生成训练真值或启用预警。逐行配对还须独立gsi_source_id，不能按重复的provider时间猜同一记录，见[来源记录配对](MINIMAP_GSI_SOURCE_ALIGNMENT.md)。

## 已有离线证据

真实learning_worker使用生成像素/模拟采集源，2帧分别携带两组不同公开比赛与双会话，正常收尾后由独立Insights CLI核验。原文件SHA前后一致，run比赛号仍为空。证据：diagnostics/minimap-context-interop/20261026T174858403060Z/consumer.json及verification.json。

同目录automatic-index-fixture是隔离的自动发现→索引→联合报告夹具；没有匹配原生GSI包时关联列表为空。不得将夹具根加入正式历史配置或冒充实战。独立测试另覆盖精确三身份匹配、错记录会话不匹配、空比赛号/混合分组、乱序/同序重定义、未来/过期、视频输入、原始载荷注入及封存号拒读。

未来实战仍须确认自动启动、源会话变化/停止后的失效、真实采集时钟和性能；旧EXE未更新，当前实现仅在源码入口。
