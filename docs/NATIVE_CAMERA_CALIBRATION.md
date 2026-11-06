# 原生镜头位置与三维地标标定

## 2026-11-06 对照测试与区域判断更正

此前人工将小地图静态白色地形线误当成视野边界，由此产生的约54–59px“区域冲突”结论已撤回。该白线也存在于固定terrain.png底图中，不是运行时镜头边界；纹理配准残差小不能证明线条身份。旧诊断及数值仅保留在retracted记录中，不作为标定拒绝或坐标不可用的证据。

用户后续明确对照local-record得到10条原生位置、4组不同目标XY、2张有效镜头候选配图；各事件本人位置保持不变，镜头目标随切屏明显变化，支持查询跟随镜头而非固定跟英雄。两图已经查看，源码接收/闭合SHA通过；仍不认证引擎同帧、Z/FOV或地标投影精度。无需因没等满一分钟而重测。

native_camera_region_check默认要求视野区域身份先有依据；region_identity_verified不是严格true时，仅保留几何关系，status=unavailable，不得报告镜头区域冲突。fit和CLI也拒绝无视野身份依据的冲突/兼容声明；实际标定仍需完整锚点检查，且不启用实机投影。

native_camera_calibration.fit使用原生镜头XYZ与目标XY推导的yaw，以及明确的三维地标→屏幕像素对应，求解俯角和焦距，再计算候选FOV、目标高度和3×4投影矩阵。没有固定60度、目标地面Z=0或默认FOV；缺地标高度不补值。

输入每个锚点必须带唯一id、centered_world_xyz、三维坐标、屏幕坐标、来源SHA及明确fit/check角色。最多32个点，至少6拟合点和4个单独检查点。两组都需覆盖二维画面区域；重复点、缺Z、错原点、无空间分布或异常来源拒绝。俯角30–80度仅是搜索边界，边界最优不接受；使用零roll、方形像素、视口中心主点的条件模型。

拟合点RMS阈值为1080p下2px，独立角色检查点最大误差阈值4px，随画面高度缩放。不为通过检查而丢点或把check点重新拟合。只有两组都通过才返回conditional_candidate，且live_usable/图像同帧/准确率认证仍为false。检查点角色独立不等于地标来源或人工标签已独立认证。

project只投影显式XYZ，未知高度、相机背后或视口外点返回空。不能自动启用覆盖层，不能把模型拟合误差报作真实地图误差。

内部离线入口：`python tools/calibrate_native_camera.py --input anchors.json --output candidate.json`。输入kind=explicit_native_camera_anchor_input、schema_version=1，字段native_position、native_viewport与anchors；读取限1MiB，检查读取期间字节变化，输出独占新建，不安装配置。此入口面向开发数据，不增加游戏中手动流程。

## 数据补齐

Foresight在现有取帧节奏内，镜头候选接收后350ms范围内的已有帧可额外留图，最多每分钟1张/每run6张，仍受80图/128MiB总预算。未来收到坐标不代表必然有邻近截图；不能为凑样本借用未来接收时间或已过期坐标。该图片扩展在清单单列，不把全部图片都宣称为每300秒三帧。

Insights报告native_calibration_readiness，分开统计有原生候选的location帧、其中已核验图片文件字节的帧。图像与日志靠近不认证引擎同帧；缺显式三维地标或时间对齐仍不允许发布实机标定。
