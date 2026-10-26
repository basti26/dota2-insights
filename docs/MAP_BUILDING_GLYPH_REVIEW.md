# 建筑图标的离线补核（2026-10-26）

使用既有15张地图图像和原官方图集，增加minimap_ancient、minimap_racks90、minimap_racks45三类探索。没有增加眼位统计维度、改拟合矩阵、打开游戏或读取封存答案。

两批均先整图搜索并冻结SHA，再读取原模型：

- building-observations-v1：原灰度NCC，阈值保持0.92，尺寸8–32；建筑候选间距6像素、每尺度32上限，允许较近的建筑图标而不沿用旧14像素间距。45项均未检出候选。
- building-tint-observations-v1：另立轮廓方法，先取红/绿显著通道，再匹配图集非黑色前景轮廓。预设NCC≥0.92且轮廓IoU≥0.8，不使用预测坐标搜索。各场景兵营模板合并后只有3或5个候选，少于完整12点名册；遗迹模板仍0。

已实际查看day_radiant候选框图，racks90框落在防御塔图标，racks45也可落在地图中部塔样图标。资源名并不等于画面中的实体类型；这种模板不能作为独立命名地标观测。当前只能报告候选不完整/语义不确定，不能据此判定坐标矩阵错了，更不能补齐标定点或放行精确眼位。

集合比较从枚举扩为最多16点的有界子集动态规划，保留最佳与次佳匹配检查歧义；12点不做12!枚举、不重新拟合。合成测试与小集合穷举核对一致，覆盖12点重排、非有限数、歧义及上限。该算法通过不认证实际地标。

原basic图标观察格式仍可读取；新增profile单独记录，不覆盖旧60项观察、正式P2名册或原模型。实际比较目录为diagnostics/map-landmarks-20261026/building-comparison-v1及building-tint-comparison-v1，所有formal_p2_pass/map_certified保持false。此分支暂不继续调低阈值追求数量；下一步必须取得能独立确认实体类型、图标中心及原点关系的证据。

最终336项完整离线回归通过（run-cd1047903366），包括12点有界匹配、小集合穷举对照、染色合成图像、profile错配拒收和旧观察兼容检查。实际图像未因此升级为独立命名地标，测试通过不替代正式P2。

可复现命令：

```powershell
python tools/review_map_glyphs.py observe --package <历史package-v4> --icons <历史icon-reference> --profile buildings_tint --output diagnostics/<新冻结目录>
python tools/review_map_glyphs.py compare --package <历史package-v4> --batch diagnostics/<新冻结目录> --output diagnostics/<新比较目录>
```
