# 官方音效实验库的独立构建

2026-10-27。音效资源准备现在由Insights独立完成，不能通过助手源码路径导入实时项目。迁移来源及原字节SHA见migration/audio-builder-source.json；迁移后增加固定旧库扩展、当前资源逐项复验及只在最后发布可加载清单。原事件阶段/瞬态预算和特征算法保持；不把迁移后的代码称为与原文件逐字一致。

## 入口与边界

```powershell
python tools/build_official_ability_sound_bank.py --vpk <游戏目录>/pak01_dir.vpk --decoder <Source2Viewer-CLI.exe> --ffmpeg <ffmpeg.exe> --events <已导出的英雄事件目录> --mapping data/audio_event_extensions.ogre.json --base-bank <固定官方参考库目录> --output diagnostics/<新实验目录>/bank --templates-per-skill 4
```

映射和工具路径必须明确指定，输出必须是本仓库独占diagnostics下的新目录；已有结果不覆盖。没有--activate或修改active.json的API。--base-bank可省略，省略时只生成本次明确映射的模板，不表示完整英雄/技能库。

输入包括当前安装的只读VPK目录、现有Source2Viewer CLI和FFmpeg，以及明确导出的KV3事件文本。VPK读取复用迁移的有界目录/资源读取器，检查目录格式、路径、长度和原资源CRC，另外绑定每份编译资源SHA。Source2Viewer负责解码，不自行解释VSND音频编码；FFmpeg单线程转换到8kHz单声道16位PCM。实际解码及转换日志留在新输出目录，失败不发布可加载bank.json。

事件映射只是人工整理的官方事件命名空间，不是实际对局施放或可闻标签。共享/物理/语音资源排除，多个技能共享完全相同PCM时不作为某一个技能的独有模板；缺失事件、未解析层和解码失败保留原状态。事件文本的导出过程仍需单独保留来源证据，不能把任意手改文本当作官方语义认证。

## 保留旧库与资源更新

扩展前验证旧库manifest、features和每份固定模板WAV的SHA、PCM格式及连续模板ID。已有技能不允许由扩展映射覆盖；旧模板元数据、特征行和WAV原字节复制到新目录，不回写旧库。最后再次核对全部旧输入指纹。

游戏VPK目录SHA变化时，不能直接断言旧音效已变化或继续混用。只在旧模板依赖的每一份编译音效及事件资源均与当前安装字节SHA完全一致时，才允许保留旧特征；任一资源缺失或变化即拒绝本次扩展。这项校验在构建前后执行，并保留新旧目录SHA及逐资源对照。它不认证补丁语义、当前游戏版本、技能可闻性或识别准确率。

本轮旧v7目录SHA为2d2505abd66fb50e619be0723a9ae7308157cc48c8e46c0f820bae2cbc69f874，当前目录SHA为8fed0a4aed3f2b31cdfe99aaafda615247a850e135841051a17223453e7e24b8。实际273份依赖、103432198字节逐项核对均相同，目录原字节前后不变；不是用全局目录SHA代替资源核验。

## 本轮食人魔扩展

当前安装的game_sounds_ogre_magi.vsndevts_c由本机CLI重新导出。明确映射火焰爆轰、引燃、嗜血术的事件，分别保留施放/命中相关源阶段；每技能最多4模板，不根据这两局录音分数选模板或调参数。

| 来源范围 | 结果 |
| --- | --- |
| 固定v7原有模板 | 402个，元数据/WAV和特征前缀不变 |
| ogre_magi_fireblast | 新增4个 |
| ogre_magi_ignite | 新增4个 |
| ogre_magi_bloodlust | 新增4个 |
| ogre_magi_smash | 尚未可靠映射，不把FireShield事件硬套过去 |
| 最终实验库 | 414模板、118个具备模板的技能 |

新增14份编译音效均成功解码；没有未解析层或解码失败。coverage.json中的missing_skills为空仅表示显式映射和固定旧库范围内没有缺模板，不代表所有游戏技能都覆盖；现有本人参照还缺Smash及魅惑魔女蹦蹦跳跳。

实验结果位于diagnostics/official-audio-extension/20261026T185235704815Z/：event-export.json、base-resource-check.json、bank-v8-ogre-experiment/及build-verification.json。验证器确认414份模板WAV与格式/哈希相符、原402行特征逐值/逐字节不变，旧库及active指纹未变。实验库未激活，当前助手仍使用原配置；没有重建EXE。

## 固定参数的已闭合录音探索对照

在同一已准入961段/4788.2秒录音上保持原0.30差分PCM门槛和全部推理代码不变，仅替换隔离参考库。两份完成回执、音频/原索引/参考流SHA及代码/开关口径均核对，预测仍先于GSI旁证冻结。

| 口径 | 原v7固定波形 | 扩展v8固定波形 |
| --- | ---: | ---: |
| 候选窗口 | 14 | 19 |
| 原14个窗口逐项保留 | — | 14 |
| 新增嗜血术候选 | 0 | 5 |
| 本人采样CD边沿中模板覆盖 | 90/207 | 196/207 |
| 同技能候选靠近本人CD采样边沿 | 11 | 16 |

5条新增嗜血术候选均靠近本人该技能的采样CD变化；不是5次听审确认的施放。火焰爆轰19条、引燃31条采样CD边沿已有模板覆盖，但本轮没有通过现有门槛的同技能候选；不能仅凭覆盖或0输出认证命中/漏报。Smash10条及蹦蹦跳跳1条仍未映射。

扩展是针对已知覆盖缺口选择的同一探索录音，不是独立最终测试集。未降低门槛、回写旧预测或启用实时预警，也没有按GSI技能名单限制推理候选；GSI仅在预测冻结后作本人采样CD旁证。下一步固定这些结果，结合后续可闻正负例核对源阶段和混音，不能在同一批资料上反复调门槛后声称泛化改善。

结果为同目录closed-analysis-v8-fixed-waveform/和extension-comparison.json，包含最终回执及双方产物指纹。报告的116.196秒只描述本次离线执行，不能作为游戏性能或速度因果结论。

## 两项未映射技能的官方声明核查

当前VPK中只读提取npc_abilities.txt及两份英雄NPC定义，校验资源CRC/SHA后保留原字节；另重新提取、解码魅惑魔女事件文件。证据在diagnostics/official-audio-ability-audit/20261026T191417272382Z/，包含原资源指纹、明确能力块、声明路径、编译事件/导出SHA及verification.json。原VPK目录和资源在前后复验一致，没有开游戏或改安装目录。

- ogre_magi_smash的明确能力块没有直接AbilitySound字段；包含multicast_fireshield_aoe等参数，只能作为别名调查线索，不能仅凭字符串把FireShield音效认证为这个技能的唯一提示。缺字段也不证明游戏不发声音。
- enchantress_bunny_hop、enchantress_little_friends、enchantress_enchant三个能力块均直接声明Hero_Enchantress.EnchantCreep。这个声明事件不是单技能独有，不适合仅凭它确认具体技能或启动某一个技能的CD。
- 当前已冻结的11条魅惑候选实际使用template_397/398，对应Hero_Enchantress.EnchantCast和enchant_cast.vsnd；EnchantCreep对应另一个enchant_creep.vsnd资源。共享声明不能自动推成这11条候选都正确或都错误，也不能把Cast与Creep当成同一个阶段。
- 嗜血术定义的直接AbilitySound是Bloodlust.Target，但本轮5条嗜血候选均来自Bloodlust.Cast模板411。说明单个AbilitySound字段不是完整声音阶段清单；需要结合事件资源、实际波形和最后的GSI旁证，不能直接替代事件映射。

以上只核对所查文件的静态声明，不宣称已经复原服务端所有发声路径或声音全局唯一性。本轮不新增Bunny/Smash模板、不重命名旧候选、不修改已有实验输出或启用短CD提醒；后续核验仍需保持阶段和技能身份的区别。

7项专项及576项完整离线回归通过：diagnostics/offline-tests/run-94eeaa9435b5/result.json，失败/错误/跳过/后台异常均0、源码未在回归中变化。专项使用自有VPK/PCM夹具与模拟解码器；上述实际构建另使用已有本机工具并验证原资源，不把夹具等同真实声音准确率。未运行游戏、录音、联网或提交推送。
