# 眼位接入地图版本与原型

2026-11-05。眼位层现绑定到明确的地图身份、VirtualMap模型ID及可选地图revision_id，并接入`map_updates.check_prototype`。原型v4支持按放置时间筛选，仍是离线历史候选，不是当前游戏视野或同时存活眼位。

## 使用

当前本机入口：`diagnostics/virtual-map-prototype-20261105-v4/打开地图原型.lnk`。新生成且允许预览的眼位包默认显示红绿点和GNV背景；绿假眼、红真眼，单击查看原始XY、候选平移后XY和放置比赛时间。

“按放置时间”可选全部、赛前、0–10、10–20、20–30分钟、30分钟后和时间未知。时间按放置事件分桶，不判断是否仍存活；边界采用半开区间，600秒归10–20分钟。null、非有限数、bool或字符串不补成0秒，独立归时间未知。切换筛选后，鼠标只命中本次筛选的点。

当前单盘样例41条：赛前2、0–10分钟24、10–20分钟15，其余0；原始记录和原始坐标没有修改。原型v3及其旧资料包保持不变，旧包没有binding时明确按legacy_unbound候选预览，不伪造已匹配版本。

## 来源版本与目标地图分别保存

- `source_map_identity`：只接受录像提取元数据中显式的`map_identity`（沿用MapIdentity字段）；没有就保持null，不用日期、目录名、语义补丁或本机版本补齐。
- `map_binding.target_identity/target_model_id`：绑定展示所用地图和坐标模型，包括构建、包SHA、边界及来源，不把目标身份伪装成录像来源身份。
- `target_revision_id`：可通过原型prepare的`--map-revision <已捕获revision目录>`绑定；必须与原型目标MapIdentity一致。
- 原始录像坐标仍在`raw_world_xy`，(-16384,-16384)只是显式候选平移。投影、原点转换、可插眼性和production_ready均未认证。

`source_map_relation`为unknown、exact_identity、same_map_bytes_other_context或mismatch。unknown允许明确标记的离线预览；同包但构建/上下文不同单列。已声明包SHA或地图名不匹配时保留数据，但禁止绘制。身份完全相同也不自动认证坐标投影。

缓存眼位的目标模型ID或目标版本变化时，`ward_map.status`拒绝继续投点；不会直接将旧图层套入新地图。`check_prototype`报告还包括`wards.target_matches_installation/requires_new_binding`，保持历史查看与当前游戏可用性分离；当前`reuse_for_current_game_allowed`始终false。

## 接口

`dota2_insights/ward_map.py`提供bind、status、phase_of、visible_points与check_installation。visible_points默认不释放未认证点，离线显示调用须显式`allow_unverified=True`；已知不匹配仍不能通过该参数绕过。

```python
from dota2_insights.ward_map import bind, visible_points

layer['map_binding'] = bind(layer, target_model, captured_revision)
points = visible_points(layer, target_model, '0–10分钟', allow_unverified=True)
```

原型prepare沿用原有ward-snapshot/ward-receipt/ward-match三项，新增可选`--map-revision`。接收仍先查封存锁、快照收据、完整EOF/解析身份/源SHA，进入/退出复验；不是只靠新绑定放行来源。无需启动游戏或重新解析历史录像。

## 验证边界

新增专项覆盖未知来源、明确版本不符、同包不同构建、身份相同仍未认证、模型/版本改变拒收、伪造认证、revision错配、分桶边界及未知时间。自有隐藏Tk检查原型实际41眼点击和全部7个时间选项；所有分桶合计恰好41，未把重复位置合并或将时间未知当零。

真实证据：`diagnostics/virtual-map-prototype-20261105-v4/`。全量回归与提交前指纹记录见STATUS、TASKS及本地verification.json。P2/P3/P4、真实落点、动态树木与眼位效果仍待独立认证。本轮不改Foresight。
