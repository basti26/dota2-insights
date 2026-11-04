# 地图包根资源与静态坐标交叉核对

2026-11-05，当前本机客户端6971，地图包SHA仍与此前6966/6969记录一致。本轮纯离线，不打开游戏、采屏、联网、改Foresight或重解析录像。

## 修正清单遗漏

此前1026项是`maps/dota/`目录内资源。完整地图包还包含4个同名根资源，现`map_assets.inventory`共统计1030项，且明确区分目录内1026和根部4项；其他地图变体不混入。

| 资源 | 字节 | 本轮能确认的内容 |
|---|---:|---|
| maps/dota.vmap_c | 116784 | 编译主入口，RERL含1415条外部引用，DATA块为空 |
| maps/dota.gnv | 104992 | 观察到32字节头 + 320×328单字节网格，逐格值保留 |
| maps/dota.trm | 29920 | 观察到头、3个层名及2484条int32 XY/层号记录 |
| maps/dota.vhcg | 2372758 | 原字节/CRC/SHA保留，尚未解码为高度或碰撞网格 |

这些游戏二进制和派生图片只留在Git忽略的diagnostics。不能因发现了`.vhcg`文件就声称已有精确高度图。

## 两条可复核的交叉结果

1. 对同一vmap原字节，自有RERL解析器与本机固定Source2Viewer20输出的1415组ID/资源名集合完全相等；后者按固定CLI、单线程重复两次且导出字节一致。主入口确实引用了`maps/dota/world.vwrld`和`maps/dota/world_physics.vmdl`。这证明依赖关系，不证明引擎的世界放置矩阵。world节点已有origin=0、physics无骨骼/绑定姿态，CTRL只声明内嵌PHYS块，仍没有取得显式放置变换。
2. TRM的XY与层号多重集，对比独立保存的实体导出：2475条实体树记录全部精确匹配，无需平移、舍入或最近邻吸附。TRM共2484条，有1条重复XY/层记录和8个未在该实体目录中精确配对的位置，总计9条未配对记录，全部保留。它们不自动当成9棵新树或垃圾条目；默认层2315记录对应实体2306，天辉/夜魇基地区层103/66条精确相符。

相同地图包是版本准入依据：历史树inventory有来源地图包SHA，文件SHA由原verification.json绑定，进入/退出复验。当前源码重新提取到另一个目录，root-assets.json与首轮正式输出逐字节一致。不是拿变化后的程序继续复用旧源码指纹。

上述是不同编译表示之间的静态坐标一致性，不能认证运行时树ID、当前存活状态、子层激活、碰撞范围、必须砍树或游戏像素落点。

## GNV只展示原始字节

观察布局按小端`<IfffIIii`读取：标识0xFADEBEAD，64/32/32三个浮点数，320/328两个维度，-160/-168两个有符号整数；剩余长度恰为320×328。严格限定当前观察布局，坏长度/维度/非有限数拒绝，不猜补字节。

以64为候选格距、32为候选中心偏移时，XY候选窗口为X=-10240..10240、Y=-10752..10240，行索引候选沿世界Y增加。它与已知世界节点范围接近，但仍不是独立坐标认证，不能用相近包围盒证明方向/放置正确。

9种实际原始值为0、1、4、5、13、16、17、20、25。当前只按原值着色，使用最近邻显示，避免插值制造不存在的字节类别。鼠标悬停可读候选行列与十六进制原值；窗口外返回未知，不填0。没有把任何bit解释为可走、树、可插眼、悬崖或视野状态。

## 代码与产物

`dota2_insights/map_root_assets.py`包含严格观察布局解析、原始格查询与实体/TRM多重集对照。`tools/extract_map_root_assets.py`只读本机包、逐资源CRC/SHA、来源及版本进入/退出复验后生成COMPLETE，不调用游戏。

```powershell
python tools/extract_map_root_assets.py --game <安装目录>/game/dota --tree-inventory <已有静态树目录>/static-trees.json --output diagnostics/<新目录>
```

`tree-inventory`可不传；传入时必须有同目录既有verification.json绑定和相同地图包SHA。不能给另一地图的数据硬套世界原点。

实际产物：`diagnostics/map-root-assets-20261105/validated/`、`reproduced/`、`verification.json`；完整资源审计为`diagnostics/virtual-map-20261105-with-root-assets/audit.json`。原型v3已加入GNV/TRM图层及四栏对照图，见VIRTUAL_MAP_PROTOTYPE.md。尚未通过P2/P3/P4或碰撞世界放置认证。
