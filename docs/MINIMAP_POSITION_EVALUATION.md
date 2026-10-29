# 小地图本人坐标离线评估

使用已有探索局和另一场留出局标注页导出的JSON。每份标注包含schema_version=1、明确match_id、marks；每个mark包含sequence、原image_sha256和原图像素x/y。运行器对照同目录samples.json中的selected列表及sample-XXXXXX.png，核对同比赛、唯一序号、图片SHA、PNG尺寸与标注边界，不使用文件mtime或最近时间补来源。

命令：

```powershell
python tools/evaluate_minimap_positions.py --train-samples <探索局samples.json> --train-labels <探索局标注.json> --heldout-samples <另一局samples.json> --heldout-labels <另一局标注.json>
```

省略任一labels参数可输出待标注状态，缺失不当0像素误差；至少探索局6点、留出局3点才尝试拟合。只能使用公开、非封存比赛。读取导出素材不表示重新核验原生采集/GSI包，输出original_sources_revalidated=false；图片/GSI语义仍来自原导出报告，不能把图SHA当作本人身份或地图版本真值。正式认证还需重新准入来源及独立身份确认。

仅使用source_record_status=exact_recorded_source_id、明确存活且未暂停、有有限坐标的标注；默认来源年龄与接收到采集延迟均在0到3秒内，缺时钟或无时区不拟合，拒绝原因单独保留。回调来源不保证帧与GSI原子同时，3秒是探索筛查上限，不代表误差可完全归为地图几何。

模型只在探索局拟合二维仿射变换，像素按各图尺寸归一化；拒绝同场训练/验证及共享图片，拒绝共线/退化训练点。报告分别输出训练和留出像素P50/P90/最大误差、每个留出样本预测及空间跨度，覆盖有限时标记；保留模型中心、尺度和系数供复算。

所有输出为exploratory_evaluation或insufficient_labels；calibration_verified始终false，不自动激活坐标模型或预警。人工本人身份、地图版本/布局兼容、轴向及精确同步仍待独立核验。合成测试只能验证计算逻辑，不代替真实标注。
