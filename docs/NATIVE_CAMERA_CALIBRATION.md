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

## 三维目标方向候选

`native_camera_calibration.fit_orientation`先独立复验被动方向事件的日志代次、Alt范围、接收凭据、目标XY和派生方向。它使用报告方向作为条件输入，不再搜索俯角；由fit点解析求解焦距及画面旋转的两个系数，再由单独的check点拒收不相容结果。仍要求至少6个fit点、4个check点，沿用来源、三维高度、重复点、二维分布及2px/4px缩放误差门槛，不使用检查点选参或修正方向。

同一CLI新增`kind=explicit_native_orientation_anchor_input`，字段为`native_orientation`、`native_viewport`及`anchors`，schema_version仍为1。候选结果包含vertical_fov_candidate_degrees和clockwise_image_roll_candidate_degrees；后者明确定义为屏幕坐标（X向右、Y向下）中绕视口中心的顺时针旋转角，不是已认证的引擎roll指令值。继续假定方形像素及视口中心主点，不假定零旋转，不安装覆盖层配置。

报告中的方向来自非原子查询，不能视为截图时刻的完整姿态。结果即使通过拟合及检查，atomic_pair_verified、anchor_truth_verified、capture_frame_alignment_verified、coordinate_accuracy_verified和live_usable仍为false；声明的地标SHA也不代替原文件准入。真实地标像素与相机—截图配对未认证时，不发布实时精确地图结论。

方向输入另须包含`image_reference={"file":"frame.png","sha256":"…"}`：file为相对输入JSON目录的PNG/JPEG路径，不接受绝对路径、越界或链接；图片上限16MiB，解码尺寸必须与native_viewport完全相同。读取前后复验原字节、路径与SHA，缺图或图片变化不发布候选。旧位置模式兼容未带图片的开发输入，但source_image_binding_verified保持false。截图绑定通过仅证明使用了这份图片，pixel_annotation_truth_verified仍为false，不自动确认点的语义或镜头同帧。

## 保存截图的离线像素标注

内部入口`python tools/annotate_native_camera.py --input pixel-plan.json`，可用`--draft anchor-draft.json`恢复草稿。它只打开计划中绑定SHA和原始尺寸的保存图片，不采集屏幕、不查询游戏，也不显示拟合或预测像素位置。

计划格式为 `schema_version=1`、`kind=native_anchor_pixel_plan`。镜头数据在 `native_orientation` 与 `native_position` 中二选一，另带 `native_viewport`、`image_reference`、`anchors` 和可选 `region_check`。仅有位置日志时可使用同一个标注窗口，完整导出交给位置求解器。每个地标包含 `id`、`role`、`coordinate_basis`、`source_sha256`、`world_xyz`，位置计划另要求 `point_semantics` 描述实际可点击的点。至少 6 个 fit 和 4 个 check，角色预先固定，禁止预填像素。

地标来源需能明确对应截图的比赛或地图版本，并指明三维坐标与像素指向的同一模型部位。实体原点、图像中心和可见底座分别记录。没有明确对应关系的实体原点只作候选，版本不符或缺 Z 的点保留为参考，点数不足只保留素材和标注草稿。声明的点语义和 SHA 不会自动认证世界坐标或标注真值。

选地标后在图片点击，支持适合窗口、原尺寸、2倍放大及滚动，并显示该点世界XYZ与原图像素。清除和不可辨认保持空值，不自动补点；未全部标记只能保存草稿，不能导出标定输入。草稿绑定原计划与图片SHA，角色和三维坐标不可在标注窗口修改。导出时重新核对计划、图片、像素边界及两组二维分布，按新文件名保存到原输入目录以保留相对图片引用，已有文件不覆盖；完整导出可直接交给calibrate_native_camera.py。

标注记录仍声明pixel_annotation_truth_verified=false、live_usable=false。点击来自此窗口只说明标注输入方式，不自动成为独立地标真值、未看过预测的证明或镜头同帧认证。此入口用于已有资料离线核对，不要求专门开游戏补采。

## 数据补齐

Foresight在现有取帧节奏内，镜头候选接收后350ms范围内的已有帧可额外留图，最多每分钟1张/每run6张，仍受80图/128MiB总预算。未来收到坐标不代表必然有邻近截图；不能为凑样本借用未来接收时间或已过期坐标。该图片扩展在清单单列，不把全部图片都宣称为每300秒三帧。

Insights报告native_calibration_readiness，分开统计有原生候选的location帧、其中已核验图片文件字节的帧。图像与日志靠近不认证引擎同帧；缺显式三维地标或时间对齐仍不允许发布实机标定。

`image_log_linkage` 只处理带已核验图片的记录。位置事件须在同一明确记录上下文中与截图引用的完整回执逐字段匹配，且截图本机单调时间与记录的候选年龄一致。若在引用回执到截图之间，同上下文已有不同位置或目标的回执，报告单列冲突，该图不计入已关联位置回执。方向事件须引用相同的位置回执，日志在截图后才出现时单列。缺失或重复回执保留对应状态。日志可读时间只用于关联本机记录，镜头采样时刻和实际运动仍待独立验证。

新闭合记录可携带可选的 `installed_map_evidence`。它记录助手留证开始和停止附近，对唯一安装的默认 `game/dota/maps/dota.vpk` 两次后台读得的字节数与 SHA256，每次读取上限 64 MiB。两端字节和文件属性一致时标记为磁盘首尾一致，变化、读不完及无法定位单列。运行时加载的地图、语义版本和截图的对应关系仍未知。旧记录缺此字段时保留未记录，不从当前安装补写历史。
