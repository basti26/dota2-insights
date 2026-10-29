# 职业分析跨批累计与去重

2026-10-29用户目标：Insights持续推进，职业新比赛不断进入地图统计，不安排专门开游戏验证。本功能只处理显式本机完整批次，不修改历史/远端，不启动解析或下载DEM。

## 流程

```powershell
# 可分页收集既有完整结果；next_before_match非空时可用于下一批--before-match。
python tools/collect_pi_analysis_snapshot.py --source tier1 --limit 8 --output diagnostics/<新批次>

# 初次累计；以后沿用--previous引用的原批次，再加新批次。
python tools/merge_analysis_batches.py --batch diagnostics/<批次> --output diagnostics/<累计1>
python tools/merge_analysis_batches.py --previous diagnostics/<累计1>/registry.json --batch diagnostics/<新批次> --output diagnostics/<累计2>

# 直接消费累计快照，不必手工拆成单场目录。
python tools/review_ward_phase_profile.py --snapshot diagnostics/<累计2>/snapshot.json --map <固定SHA示意底图.jpg> --output diagnostics/<新地图报告>
```

原批次必须有完整receipt.json和SHA绑定的snapshot.json，可带逐场capture-ID.json。累计输出registry.json、供原事实库/地图工具直接消费的snapshot.json及选中版本的原始capture文件。全部写新目录，不修改原批次或旧累计。后续增量使用--previous，不把累计目录冒充原始收集批次。

## 身份与冲突

先查公开封存锁，之后才解码正文。每个版本检查manifest/比赛身份/完整EOF/来源未变标记、wards SHA与数量、catalog来源/分类/分析状态，逐字匹配可选上下文的manifest和wards，并验证上下文正文SHA。

版本ID绑定比赛号、manifest原字节SHA、wards原字节SHA及规范化catalog来源/分级。相同版本跨批只计一次，原始批次SHA全部保留；来源顺序不影响版本。解析器、原始manifest或分类依据变化视作不同版本，包括重解析后仅manifest时间改变，宁可待核对也不静默覆盖。

同场多版本默认整场暂不进入累计快照，registry明确列conflicting_matches；其他无冲突比赛可继续。全部比赛都冲突时只发布registry，不生成伪空的“成功快照”。使用--select MATCH_ID=SOURCE_ID明确选择后才恢复单版本计数；所有备选版本仍在注册表。之前唯一版本的自动选择不等同用户明确选择，后续冲突不会沿用它悄悄放行。

原收集快照、收据和上下文文件指纹都在下次累计重新验证；正文在读取期间变化也拒绝发布。不拿索引时间代替帧或比赛时钟。上下文字节接收成功不替代load_capture语义/时钟准入。

边界：单次128批、256场、512解析版本、同场最多8版本；快照/输入内存预算显式检查，超限报错，不删除历史或修改Pi录像额度。当前是显式命令增量，尚未部署常驻自动分析任务。分级/地图未知、系列赛相关性、眼位效果仍不由去重自动认证。

## 本次实际贯通

- collected-v1三场，collected-v2四场。registry-v1→registry-v2累计得到4场，识别3条重复输入，只计378眼。再次把collected-v2加入生成registry-idempotent-check，仍4场，无重复累计。
- 四个原上下文文件按原字节保留。新事实库patterns-v2为4场378眼；context-v3为272条事件、404个采样包、20200个逐人指标值。其中280包对局内、124包赛后/结束边界；仍按明确暂停规则投影且保留原始内容。
- 新地图diagnostics/ward-phase-profile-20261029/v3/report.html直接读取累计快照，378眼；24分层、24邻域与原始冻结JSON独立复算一致，24组DOM逻辑筛选通过。phase-0-10.png已查看。底图仍是已有示意投影，不认证最新地图落位。
- 新样本改变支持比例：10–20分钟天辉假眼原热点从3/3变3/4场、仍4次；0–10分钟天辉有一真眼邻域4/4场、4次。不是有效性排名。
- 最新第四场9066084051已完成纳入；9066180509在收集时waiting_retry，未纳入且未因此停止其他工作。

743项完整离线回归通过，证据diagnostics/offline-tests/run-3874ee160317/result.json；源码期间未变，失败/错误/跳过/后台异常均0。覆盖跨批去重、同场多版本、显式选择、重复增量幂等、旧来源/收据/上下文变更拒收、读中变化、分页及快照地图入口。

本轮未开游戏、采屏或录音，未改Foresight/Pi服务，未自动提交推送。
