# 已收到的GSI位置、状态和目标证据

日常复盘的原生本人GSI部分现包含gsi_evidence_census；单独导出使用：

`C:\Python312\python.exe tools/review_gsi_evidence.py --latest 2`

先同步原生索引，再通过match_journals.open_rows固定包SHA并进入/退出复验。CSV及报告只写Insights diagnostics，旧记录不移动或改写，不导入Foresight源码。

## 最新两局实际结果

证据：`diagnostics/gsi-evidence/20261026T172404830121Z/REPORT.md`与report.json。
原生两包共4946条采样记录；其范围是收到的GSI直到首次POST_GAME，不是完整对局无遗漏证明，不能与旧启动器重合流相加。

| 比赛 | 已收到Probe | 有效本人坐标记录 | 其中存活且来源年龄≤5秒 |
|---|---:|---:|---:|
| 9062710567（食人魔） | 2178 | 2126 | 1964 |
| 9062765692（魅惑魔女） | 2768 | 2638 | 2333 |

合计4764条坐标，其中4297条为新鲜存活记录。数据为GSI xpos/ypos原值，0有效，缺轴/非有限数/bool不转0；要求playing、明确本人英雄及来源basis与self_status一致，和本人经济英雄不冲突。原坐标包身份、源payload SHA、原行序号和sequence均保留。

每局CSV包含比赛、两个会话、英雄、provider时间、接收UTC、比赛时钟、x/y、alive、source_age_s、阶段、暂停及昼夜。死亡坐标不删除，但不能当作存活英雄小地图标记；缺时间或来源年龄异常也不冒充实时。CSV另有SHA绑定。

原始坐标曾超出±8192，不进行截断，也不以这两条路线的极值认定地图边界。尚未认证世界坐标→小地图像素映射、单位、帧级时间或敌方隐藏位置。要把轨迹用于小地图自动标定，还需明确的小地图比赛/会话绑定和实际可见标记，不按UTC猜归属。

## 状态字段

收到过明确布尔值：alive、silenced、stunned、disarmed、hexed、muted、break、smoked、has_debuff、magicimmune。沉默/眩晕/缴械/妖术/禁用/破坏/开雾等均有实际true样本；本批magicimmune只见false，不能据此认证它与BKB的关系。

两局中debuffimmune、rooted、invulnerable、channeling、teleporting均未报告，不能记为false，更不能说游戏永远不支持。has_debuff只是一个布尔量，不是全部正负面buff名称、来源或剩余时间；TP物品CD也不等同正在TP。

报告的true/false是采样记录数，不能当释放次数或持续秒数。仅在同一明确比赛/双会话/英雄，sequence递增、provider间隔0–2秒、接收间隔0–2.5秒时记相邻布尔变化；缺包、身份变化、时间倒退不跨越。变化仍是采样边沿，不是精确生效/结束时间。最多保存1000个边沿示例，截断明确标记。

daytime、nightstalker_night、paused有明确布尔值；这两局nightstalker_night和paused只见false。未实测夜魔/光法/凤凰改变昼夜的语义，不仅靠字段名认证特殊技能效果。

## 肉山与魔方

roshan、tormentor、miniboss节点和map.roshan_state在两局没有报告，但events中确实有roshan_killed。重复事件按type与原game_time/timestamp组成的键去重；没有时间的提及单独计数，不捏造唯一事件。

| 比赛 | 事件提及 | 不同带时间事件键 | 事件原game_time | 首次收到时比赛时钟 |
|---|---:|---:|---:|---|
| 9062710567 | 30 | 1 | 1940 | 1732（28:52） |
| 9062765692 | 33 | 1 | 2160 | 1908（31:48） |

原game_time与同包map.game_time一致，但与比赛clock_time相差208/252秒，不能直接当比赛时钟或UTC。这里只保存首次收到时的两种时钟，没有自动换算精确击杀时间，更没有把30/33次提及当30/33次击杀。每局仅见一个不同事件键，也不保证本局只有一次肉山击杀。

这提供了今后验证肉山提示的事件起点；魔方仍无此类事件证据。没有修改实战提醒或启用新报警。

## 验证

550项完整离线回归通过（diagnostics/offline-tests/run-6800653a012f）；状态中文表和导出报告补齐后相关34项通过（run-dcf7a4e7b361），均无失败/错误/跳过/后台异常、回归时源码未变。测试覆盖零坐标、半轴/错误英雄拒收、true/false/未知分离、死亡/过期记录分层、缺口不桥接、重复目标去重、事件时间不混用、源变化拒绝导出和原生CSV身份/SHA。

联合报告已实际跑通：`diagnostics/record-content/20261026T172240104227Z/`。独立报告含更完整的中文状态及事件时间表；两者来自相同原生包。没有开游戏、采屏、录音、联网、改旧资料或提交推送。
