# 职业比赛接入现有赛后事实库

2026-10-29：完成原postgame_store只接受own/d2pt的兼容缺口。职业比赛保留为独立tier1来源，不改名成高分路人局，不改旧库或旧报告。

## 来源门禁与兼容

来源必须是非空、无重复的own/d2pt/tier1列表。含tier1时必须带明确赛事编号、名称、整数tier=1及https://liquipedia.net/dota2/下的无凭据分类链接，保留为source_catalog.json证据及match_competitions表。分组优先级为own > tier1 > d2pt，保留全部source_types：本人比赛继续归本人；仅同时进入职业/高分队列的比赛归职业，不重复计入两组。

旧事实schema主字段和版本保留，新增赛事元数据表；旧库无需重写。未知来源/缺分级依据拒收，原footer、完整EOF、原始来源SHA、wards SHA/数量、比赛时长检查保留。读快照时先查封存比赛，之后才解码正文。地图/补丁未知仍未知。

ward_patterns、ward_revisits、ward_region_stability及ward_time_controls的来源显示增加“Tier 1 职业”，不再让职业来源落入“本人/高分来源”的二选一后备标签。现有cohort键包含source_group，职业与其他来源分开。Pi上下文扩展旧工具仍是明确D2PT抽样，不擅自用它重解析职业全库。

## 只读收集

```powershell
python tools/collect_pi_analysis_snapshot.py --source tier1 --limit 8 --output diagnostics/<新目录>
```

默认只取最新一批已下载校验且完整解析的职业比赛；未完成行写入excluded_incomplete，不当成0眼。单批上限16是响应/内存预算，不改变Pi无限额职业下载策略。现可用next_before_match游标分页、merge_analysis_batches.py显式跨批去重累计，见ANALYSIS_BATCH_REGISTRY.md；尚未部署常驻自动分析，不可将一批视为全赛事。

通过现有本机Bridge只读取manifest/wards及可选postgame_context，不取DEM、不启动解析、不改服务。每场先查封存锁，再查catalog与文件SHA/身份，末尾重新核对所选catalog来源信息；manifest或正文变化拒绝成功收据。保留赛事分类白名单字段，不导出账户/玩家名/聊天、本机路径或凭据。context_collected仅认证传输字节，语义/时钟准入由已有load_capture独立执行。

## 实际贯通结果

本机证据：diagnostics/tier1-facts-20261029/。

1. collected-v1：只读收集9065794389、9065883621、9065986613，共三场；9066084051在收集时仍analyzing，单列排除。
2. patterns-v1：原CLI直接建库成功，三个match的source_group均tier1；match_competitions保留PARI UNIVERSE by FISSURE / 20395 / tier1。255眼中17眼开局前，206眼属于完整10分钟时段，32眼是尾段参考，计数闭合。
3. context-v1：严格时钟门禁实际拒绝暂停期间指标时钟偏差，保留失败目录，不冒充完成。
4. context-v2：显式使用既有reconcile_recorded_pauses和retain_missing策略，三场共7个指标包按同tick/明确暂停记录投影到最终时钟，原字节不改。完成入库179条事件、280个指标包、14000个逐人指标值；187包在对局内，93包在赛后/结束边界，不用后者作对局事实。构建号6969、模式2来自同DEM上下文，语义补丁/地图修订仍未知。
5. revisits-v1、events-v1：两条已有工具实际复跑成功。事件窗口选出176条，其中130条仍是hero_death_candidate，不能称独立确认击杀；其余含27条塔倒、3条肉山死亡、10条兵营倒、6条买活记录。每个比赛内多事件不算独立比赛。

729项完整离线回归通过，证据diagnostics/offline-tests/run-2d3b5efe32f0/result.json，源码未在回归期间变化；失败/错误/跳过/后台异常均0。三份生成页面的JavaScript经node --check通过，只认证语法而非浏览器视觉验收。收集测试在本机生成档案上运行完整远端读脚本并逐字比较前后文件，覆盖未完成排除、正文变化、来源变化及封存前置拒收。

未启动游戏、采样设备、重解析历史录像、修改Foresight/Pi服务、提交或推送。独立眼位/当前地图认证、系列赛相关性和有效性评估仍待证据。
