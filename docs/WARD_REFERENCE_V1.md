# 单局职业眼位参考包 v1

输出kind=historical_ward_reference，schema_version=1；保存明确match_id、source_replay_sha256、source_wards_sha256、原始坐标口径gem_cell128_plus_vec_raw_coordinates与map_version（未知不补当前版本）。points保存每个原放置记录，不聚类或只挑长存活点；id为比赛号:原行序号，x/y、ward_type(observer/sentry)、team、placed_game_time_s、tree_clearance和tree_evidence保留。source_ward_count和unavailable分开，数量不算职业偏好或准确率。

`python tools/prepare_ward_overlay.py --pi-match <比赛编号>` 只读收集该场已完成的Pi结果、核验目录完成/源DEM SHA/manifest/wards SHA及数量后导出；Pi仍在解析时拒绝，不中断或另起重复解析。`--folder <已收集目录>`可重新从manifest.json+wards.json导出。不能直接读取独立封存比赛。原录像和结果保持原位不改写。

默认全部tree_clearance=unknown，没有树木证据不推成not_required。可选`--tree-reviews <JSON>`，格式为match_id、wards_sha256、points列表；列表每项id、clearance(required/not_required)、非空evidence。绑定来源不符、重复/不存在ID、空依据都拒绝。该格式表达独立观察者确认，不自动判定“靠近树”等价“必须砍树”。

Foresight只消费导出包，不导入本仓库源码。实验性世界坐标浮层有独立手工镜头标定/视觉跟踪，不因眼位提取或来源SHA通过就声称当前地图或主画面投影已认证。
