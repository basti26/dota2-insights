# 小地图原始图标目录与外观对照

2026-10-26。读取已有本机游戏资源导出，不启动游戏、不重新解析录像、不联网。目的是替代仅凭颜色/孔洞猜图形类别的做法，为后续图标族和方向模板提供原始参照；不把资源名直接当画面目标身份。

## 命令

```powershell
python tools/catalog_minimap_icons.py --icons <已配置的历史icon-reference目录> --output diagnostics/<新图标目录>
```

源目录固定读取mod_textures.txt和decoded/minimap_sheet_psd_b65d6dd9.png，不跟随定义中的任意文件路径。只提取materials/vgui/hud/minimap_sheet.vmat图集的叶子定义；保留原名字、材质名和x/y/width/height。重复名字/字段、非法名字拒收；缺矩形、零尺寸/越界保持unsupported，不猜坐标。

定义最多2MiB、512个图标；原图必须64–2048像素RGBA PNG，每矩形最大256px。PNG、定义及每张原RGBA裁片的字节/像素SHA均保留，导出前后重验原文件。原目录只读，禁止输出覆盖源或旧导出。输出含catalog.json、REPORT.md、icons/和每页12项的sheet-*.png。

图册三列预览为原色、示意绿、示意红。示意仅是RGB乘色并保留alpha，不能作为实际游戏着色器或玩家颜色的认证。原icons/裁片不染色。资源重名矩形别名保留各自名字，不当作独立视觉类别或样本数。

## 实际资源与来源

已有mod_textures及解码图集导出122个定义，无unsupported。包括minimap_herocircle、minimap_herocircle_self、minimap_herocircle_self_highlight、minimap_heroinvis、minimap_creep、minimap_controlledcreep、minimap_ward_obs、minimap_ward_invis及多种信使等。不同自身/隐身/图像模式资源不能只用一个“圆形模板”代表。

已核对已有sources.json中三份编译资源SHA均与原缓存文件一致；当前Dota pak01_dir.vpk的SHA为8fed0a4aed3f2b31cdfe99aaafda615247a850e135841051a17223453e7e24b8，与提取时记录一致。证据diagnostics/minimap-icon-catalog-20261026/source-check.json。它只说明目录字节一致及缓存字节匹配，未重新提取当前成员、重解码或证明运行时使用这些资源；catalog中的source_current_install_verified/game_patch_verified/semantic_identity_verified仍false。

## 实际查看与发现

已查看资源页1、2、3、9、10，以及原裁片8与8个资源族的并列对照。原裁片PNG SHA和资源裁片SHA在制作对照前核验，比较过程不改源文件，也不写分类标签。

minimap_ward_invis资源是空心眼形；此前未命名的空心菱形裁片在外观上更接近这种眼形，而不是能直接认定为英雄。部分英雄标记资源有单侧方向尖角，自身高亮与隐身资源也可空心；因此“有空洞”“在移动”两个线索仍不足以定义英雄。这里是外观推断，没有确认该裁片实际是哪一个游戏实体。

后续应分图标族、尺寸、方向共同比较，记录最佳/次佳和歧义；保留文字、眼、兵线、建筑等混淆模板，不能只与英雄模板比较后强制选英雄。资源匹配仍需独立的画面对象/可见性证据，不能延伸为世界坐标标定或精确眼位认证。冻结眼位新统计维度的约束保持不变。

实际图册：diagnostics/minimap-icon-catalog-20261026/v1/REPORT.md；对照图：同级上层marker-family-comparison.png及comparison.json。7项生成图集测试覆盖原矩形/RGBA、同矩形别名、其他材质不读取、重复/越界/缺字段、图像模式、输出边界/不覆盖及源变化。最终486项完整离线回归通过（diagnostics/offline-tests/run-6720b9c04122/result.json），无失败/错误/跳过/后台异常且源码在回归中未变。助手运行代码、线上配置、旧记录和EXE未修改，未提交推送。
