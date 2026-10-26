# BP结束阶段日志前缀交接 v1

2026-10-26。选人结束后仍可能有迟到的UI回执、阵容补全和主日志追加，因此不把整个BP会话伪改成closed。Foresight在首次记录到strategy/in_game/finished时，将当时已有日志的字节边界交给独立线程计算SHA，发布不可变前缀清单。

## 数据边界

元数据与最后标记：drafts/session_<32位会话>/phase-handoff.json、phase-handoff.COMPLETE。标记为清单原字节SHA256及换行。schema_version=1、kind=foresight_draft_phase_prefix，包含session_id、match_id_at_cutoff（可空）、ending_phase、cutoff_recorded_at_utc、last_observed_at_utc（可空）、源记录错误/限额标志。

固定七条可选日志：events、ui-presented、manual-actions、panel-visibility、portrait-visibility、chat-candidates、ban-window-candidates。存在的日志分别记录file/bytes/rows/sha256；其余记missing_at_cutoff，不补0行。每条最多32MiB/10000行/每行1MiB，总计128MiB，清单16KiB。图片、pool观察和整个游戏过程不属于本清单。

字节边界由BP主线程在写完本次结束阶段事件后取得，不在该线程解析或哈希正文；计算在独立低频等待线程中执行。最多128个会话、8个待处理任务，溢出/缩短/半行/写入失败保留错误且没有成功标记。相同会话只取第一次截止；不同会话各自独立。助手继续运行时，主日志允许追加，封存清单不改写。

scope固定processed_logs_through_first_recorded_ending_phase，later_tail明确may_append_not_included_in_this_prefix。晚于截止才处理的选人回执不纳入；没有UI流保持未知，不能据此说整轮没展示推荐。ending_phase是软件记录到的阶段，不等于游戏真值；match_id_at_cutoff为空且没有独立显式绑定时，只按本地BP会话分析，不根据时间猜归属。

## 独立接收

```powershell
python tools/review_draft_phase_handoff.py --diagnostics-root <已配置历史diagnostics根> --session <32位BP会话>
```

draft_phase_handoff.verify先读有界元数据并检查已知比赛封存锁，再读日志前缀。路径固定、禁止符号链接/目录联接/越界，清单与missing名单必须完整覆盖七种流；严格核对前缀字节/尾行/行数/SHA与行内session/match身份。允许源文件比前缀长，后追加内容不读取、不哈希、不要求解析成功；原前缀缩短或改写仍拒绝。

open_rows进入/退出复验，可固定expected_package_id；迭代器退出后不可继续。分析区分selection候选事件行与selection候选UI应用回执行，strategy/in_game候选不冒充选人期展示。缺失事件/UI流的对应统计为null，不按0处理；旧UI回执没有view_input快照时单列未知格式数。source_verified仅表示文件前缀，不认证图片、推荐质量或用户确实看到。截断边界不读取后续尾部，也不证明完整BP覆盖。

已接入生产端、独立单包读取及自动索引/联合报告。不要用本人GSI/装备索引放行本格式，旧session-end/launcher交接规则继续保留。

## 迟到比赛号与自动接收

相同运行中BP会话首次拿到明确比赛号时，另写phase-match-binding.json及phase-match-binding.COMPLETE，不修改原前缀。绑定schema_version=1、kind=foresight_draft_match_binding，必须包含相同session_id、phase_prefix_package_id、match_id、bound_recorded_at_utc，basis固定same_runtime_draft_session_explicit_match_binding。这只是软件同会话的显式身份依据，不认证游戏真值。不同会话不继承，已绑定后出现冲突比赛号拒绝；原截止已知比赛号不另发绑定。

Insights先检查绑定元数据和封存锁再读取日志正文。effective_match_id是原截止比赛号或独立绑定号；原match_id_at_cutoff仍保留null。读取绑定不代表覆盖截止以后的日志。open_rows进入/退出同时复验前缀SHA及绑定SHA；无绑定也固定为null，分析期间新增绑定使本次结果失效，重新同步后再读。

tools/review_recordings.py先同步旧包、本人GSI、装备和BP，再生成联合报告。BP独立索引位于diagnostics/draft-phase-handoff/index.json，类型draft_phase_prefix_index。只扫描源码diagnostics/drafts/session_*及已知helper-*/economy-*便携版固定布局，不递归搜索无关目录。索引同时保存两份SHA、截止/有效比赛号；文件mtime只用于发现排序。

相同前缀SHA副本去重，有明确绑定的副本优先于无绑定副本；两个不同非空绑定出现冲突时整组blocked，不挑一个继续。与闭合启动器重复的流逐项标记，不可累计。联合报告可按明确比赛号列出GSI/装备包，仍不声称BP会话等于采集会话；没有比赛号不按UTC关联。

本轮422项Insights完整离线回归通过（run-05d069e695c8），助手1542项通过（run-561ad86c2e7b），均无失败/错误/跳过/后台异常，回归期间源码未变。新增专项覆盖独立绑定、封存比赛先拒读、读取期间新增绑定、索引后变化、源码/便携版去重及冲突、未知比赛不误绑等。

新的独立双进程证据在diagnostics/bp-bound-report-interop-20261026/：真实manual DraftService按生成阶段运行，截止比赛号未知，随后同会话接收生成比赛号12345。生产进程仍处于in_game时，独立Insights自动发现并生成一份BP报告，截止比赛号仍null、有效比赛号12345，未读in_game尾部、缺UI为null；消费后正常退出，前缀原字节不变。未开游戏、采屏、联网或将夹具纳入正式历史索引，不是实战验收。

## 生成数据验证

真实DraftService以manual来源运行，统计缓存为不存在的生成路径，依次输入selection→strategy→in_game测试指令，未开游戏/OCR/采屏/联网。strategy后发布前缀，服务仍在运行并已追加in_game日志时，Insights独立CLI读取成功：只有waiting/selection/strategy事件，没有混入in_game尾部；UI流缺失返回null。之后才停止生产进程，原前缀清单字节不变。

证据在diagnostics/bp-phase-interop-20261026/producer-ready.json、consumer-live.json和producer-closed.json；生产脚本在Foresight diagnostics/bp-phase-interop-20261026/produce.py。测试阶段是手动生成输入，不是实战阶段识别或推荐质量验收；match_id为空，没有将它绑定真实比赛。

最终Insights408项完整离线回归通过（diagnostics/offline-tests/run-fe1f44ea49c6/result.json），助手1539项通过（run-85ca437afb3e），均无失败/错误/跳过/后台异常且回归期间源码未变。独立接收专项覆盖尾部在读取前/读取中增长仍不混入、前缀改写/缩短拒收、缺UI为null、计算事件与实际UI回执分开、跨会话/路径/类型/标记篡改、旧收据失效和上下文退出后不能续读。没有增加游戏内验证结论。
