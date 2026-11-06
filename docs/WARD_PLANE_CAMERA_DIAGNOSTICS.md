# 三维镜头候选与开口轮廓核查

2026-11-06。继续解决眼位真实贴地，先验证现有二维投影是否能支持三维恢复。没有开游戏、采集、联网或整场录像解析。

## 关键发现

三维恢复在已知条件的生成相机上成立，但现有数据尚不能直接启用。已有camera_outline只要求下边与两条侧边：当顶部开口时，它把可见侧边的上端连接为四边形。这个连接是可见线段范围，不是完整游戏视口的上边界。原代码却未显式标geometry_inferred，容易被后续当成完整镜头边界。

Foresight automatic_minimap_camera版本改为v3：额外测量顶部边的像素支持，达到0.72才标为四边支持；否则observed_edge_count=3、geometry_inferred=true、geometry_basis=visible_side_endpoints_not_full_viewport_corners。顶部充分支持时也不宣称outline_accuracy_verified。仍保留原试验平面显示，不把本次改动说成视觉坐标已经修准。

新证据包含top_edge_support、observed_edge_count、geometry_basis与reason，经AutomaticCamera、WardTerrain、worker传入原有有限记录。WardEvidence源指纹加入ward_auto_camera.py，避免几何判定变化后无法辨认生产源码。界面状态改为“轮廓不完整，当前为试验估算”，不把所有开口轮廓都叫默认镜头。

## 数学候选

Insights ward_plane_camera.recover在明确条件下尝试从metric平面H恢复3×4候选P：透视相机、方形像素、零斜切、主点位于原生客户区中心、H确实表示Z=0平面。利用K^-1*h1与K^-1*h2正交且等长求焦距，再构造高度方向；原平面投影保持不变。

仿射/退化、无正焦距、超出暂行焦距范围、轴不一致或相机不在平面上方时拒收。暂行范围与2%旋转一致性只用于候选诊断，不是Dota参数真值。project接收的是该参考平面的XYZ，不能把raw_z直接代进去；本批没有自动应用-16384或替换实际显示。

### 保留的反例

生成的正确相机在1920×1080/2560×1440、不同yaw、0/128/256/768高度恢复误差约1e-12像素，属于数学一致性检查。

另一个生成相机使用非方形像素，却也能产生“通过”的条件候选：平面误差约0，Z=128时误差约20.3像素，Z=512时约83.1像素。这证明单平面吻合和恢复成功本身不能认证相机内参。该反例作为测试保留，不通过删除反例让指标变好。

## 现有资料结果

先运行本机sync_helper_records --latest 1，再通过ward_camera_review对最新5份已闭合眼位记录做源/身份/SHA进出复验；1833个已有主画面矩阵均不满足“明确底图镜头且非推断轮廓”的新三维候选来源要求，没有新镜头模型通过。四份记录有上限导致的缺口，不当作整局连续覆盖。

再使用已有15张冻结小地图裁片，逐图核验manifest与图片SHA；只把图片嵌入生成的2560×1440画面，注入记录ROI与屏幕尺寸，通过生产WardTerrain尝试配准。它不是自动ROI或真实全画面验证。

得到6个主平面矩阵。旧判定把其中4个标成非推断，但这些H都没有正焦距解；查看原小图和检测代码后确认顶部开口不能当完整视口角点。v3重算后6个均明确为推断，原矩阵数值逐项不变，没有修改数据去迎合相机模型。其余9图没有主平面矩阵，均保留失败。当前没有一张能据此认证完整三维镜头。

## 实现与验证

- Insights：dota2_insights/ward_plane_camera.py及6个测试；ProjectionDiagnostics/ward_camera_review新增候选原因统计与最多8条未认证模型例子。
- Foresight：ward_auto_camera.py、ward_terrain.py、worker记录字段及ward_evidence源指纹；新增闭合顶边/开口顶边区别测试。
- 本机资料：Insights diagnostics/plane-camera-20261106/下review.json、generated-geometry.json、frozen-crop-camera-review-v2.json、verification.json；Foresight同名目录保存两个时期的裁片矩阵与生产源码SHA。旧结果保留。

121项Foresight眼位/桌面专项run-657b5e5338ed与280项Insights地图/眼位专项run-acc5e2cb832b通过，0失败/错误/跳过/后台异常、源码稳定，当前来源/代码SHA复验。该批是专项回归，不把上批1753/1017全量结果冒充当前版本全量认证。

## 下一步

优先解决完整平面视口而非继续猜高度屏幕偏移：尝试用三条已观测边界线加同帧本人世界/画面锚点约束完整平面，保留参与拟合的锚点来源与失败原因，再用未参与拟合的对应检查。可见线段端点不得自动升级为真实上角。

三维相机假设、真实视口范围、地面层和自然对局对应仍未独立验证。新模型只做离线候选，Foresight当前显示仍是原试用平面链路；本机设置/眼位包不改，旧EXE不重建，未提交推送。目标“完全重叠”继续未完成。
