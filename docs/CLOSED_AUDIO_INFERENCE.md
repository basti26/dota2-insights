# 已闭合对局录音的盲测入口

既有库/固定波形核验的新对照见[AUDIO_EXISTING_BANK_COMPARISON.md](AUDIO_EXISTING_BANK_COMPARISON.md)：同一录音v7粗频谱191候选，启用原0.30波形开关后14候选，保留11条靠近本人采样CD边沿的魅惑候选；仍无听审真值，不替换active库或启用预警。4段新增非重复复听片段已备好，人工判定均保留未完成。

候选分层导出已补入，见AUDIO_CANDIDATE_REVIEW.md：不改本轮181条预测，另取9段覆盖6类技能的短复听材料，拒绝跨暂停拼接，真实听审仍待用户安排。

`tools/analyze_match_audio.py`现在必须在`--handoff-package`和`--audio-match-package`中二选一。旧整次录音先运行`tools/sync_helper_records.py --latest 3`，从helper-handoff/packages中选取SHA命名的已闭合包。新逐局录音使用独立scope目录及清单SHA；两条入口都不能仅凭summary.json存在放行。

命令参数：`--run <已闭合录音目录> --bank <固定参考库目录> --output <本仓库diagnostics下的新目录> --handoff-package <SHA.json>`，可选`--engine baseline|mixture`及baseline专用`--waveform-check`。已有输出不覆盖，失败结果保留供排查，没有最终回执不能视为完成。

## 阶段与门禁

1. 配置根、包SHA、明确录音身份和闭合状态检查；先验证每个WAV的SHA、PCM格式、长度及摘要总量。未闭合、外来录音目录或源文件变化不能进入已完成分析。
2. 固定库bank.json与features.npy指纹，PCM推理使用原算法/阈值，不读取GSI参考能力列表。分段结束原因不是连续五秒段、take切换或音频偏移不连续时重置匹配窗口，不跨缺口拼技能。
3. 写出predictions.jsonl、scans.jsonl及prediction-manifest.json，固定预测/扫描SHA，标明GSI未用于推理。之后才读取交接包中已准入的reference-gsi流；嵌套match_id同样执行封存/外来比赛检查。
4. 参考流仅作本人采样CD边沿旁证，不能标注敌人声音，也不能以未命中旁证断言误报。无参考流保持未知，不擅自打开未准入的同名文件。
5. 最后再次验证录音、整包和参考库，核对预测清单与实际来源/结果SHA，再原子发布analysis-receipt.json。写入失败不残留半个成功回执；已冻结的预测阶段产物仍不等于分析全部完成。

完成回执只证明离线管线与输入字节一致，不证明技能可闻、分类准确、冷却预警可用或游戏音轨隔离。默认系统输出混音的原边界不变，未启用任何游戏内音效预警。

## 新逐局包盲测

```powershell
python tools/analyze_match_audio.py --run <match-audio/run_...> --audio-match-package <该run/closed-matches/scope_...> --expected-package-id <独立音频索引中的清单SHA> --bank <固定参考库目录> --output diagnostics/<新分析目录>
```

`--run`必须是包所属capture目录，不能指向其他录音。`--expected-package-id`可固定自动索引的SHA；若显式直接选包且未传此项，仍会在进入时固定当前清单并于结束复验。健康为gaps的包不能进入这个完成分析入口。整体run无须退出、也不要求全局summary存在；只分析本包已封存范围。

新输入适配器`open_match_analysis_inputs`调用有界读取器的延后参考模式。预测前，对reference-gsi.jsonl只检查原字节SHA、长度、行数/行尾，不解码技能或CD；结束上下文只作已封存范围的身份/阶段证据，不送进匹配器。传给既有PCM算法的片段信息投影仅含文件名/SHA、音频偏移/时长、连续段结束原因和take_id，未带英雄、阵容或能力列表。

预测和扫描落盘后，`authorize_reference_review`必须核对冻结阶段、源元数据SHA、每份WAV来源及实际预测/扫描SHA，才释放语义GSI读取；提前读取或修改冻结文件均拒绝。旁证按完整新包协议检查后才做既有采样CD对照。来源、参考库及冻结结果在完成收据前再次核验，失败保留阶段产物但不发布analysis-receipt。

原标记gsi_read_during_inference=false表示语义GSI没有用于推理；准入计算参考流原字节SHA不等于读取技能真值。新收据保留source_kind=native_audio_match_handoff和明确source_scope，完整捕获关闭、整局覆盖、音频/GSI精确同步及真实准确率仍未认证。

8项新增生成检查：同一2秒信号新旧入口的predictions/scans/post-gsi-review逐字节一致且真实分类器输出正候选；语义参考只能在预测冻结后解码；提前读参考、改预测/参考/WAV、错误索引、gaps健康均拒绝完成；空比赛号保持本地会话。

完整618项离线回归通过：diagnostics/offline-tests/run-345ae1ec2824/result.json，失败/错误/跳过/后台异常均0，回归期间源码未变。此前相关35项及新增专项22项也通过（run-1d67809b5a98/run-469d5eea2c70）。不改算法门槛、特征、固定波形参数或正式库。

独立CLI实际验证：diagnostics/audio-match-analysis-interop/20261026T203841779217Z/verification.json。生成PCM/GSI/前台回调驱动实际生产_record_take，生产进程及录音线程仍运行、全局summary尚不存在时，实际CLI读取已封存首局2秒噪声并得到1个生成参考候选；预测冻结、旁证对照、最终收据均完成，继续第二局后首局源SHA不变，生成库SHA不变。原生成脚本与两秒/噪声变体的SHA和差异留存；没有操作真实设备或游戏、没有触碰active。首次生成库缺hero_id字段的失败输出保留，修正夹具元数据后成功，没有修改生产分类器迎合夹具。

本机尚无真实新格式录音包，不拿这次生成正候选当实战命中率。新包的分层复听工具已接通，见AUDIO_CANDIDATE_REVIEW.md；人工听审及实际时序/性能仍待验证。逐来源原生GSI核查已接入独立日常技术复盘，见PER_MATCH_AUDIO_HANDOFF_V1.md；其函数拒绝盲测的延后参考包，不改变本节先冻结预测的顺序。

## 本次实际基线

输出：`diagnostics/closed-audio-analysis/20261026T165125847998Z/`。
使用现有v5-stage-balanced官方原始资源参考库（345模板），未用这两局重训或调阈值。961段、4788.2秒，分析阶段106.524秒；最终输入进出复验与完成回执通过。

| 模板技能候选 | 数量 |
|---|---:|
| skeleton_king_reincarnation | 171 |
| kunkka_ghostship | 6 |
| pudge_dismember | 1 |
| doom_bringer_infernal_blade | 1 |
| legion_commander_press_the_attack | 1 |
| legion_commander_moment_of_courage | 1 |

扫描原因：low_similarity 22457、candidate 181、low_level 743、repeat_suppressed 135、small_margin 398。次数只是扫描/候选，不能作为技能次数或准确率。

候选严重集中在template_086（骷髅王重生），提示当前基线区分能力仍需独立复听和负例检验；不能把171条称为171次真实释放，也不在这批数据上反复调门槛后声称泛化改善。当前50段候选裁片的先到先存策略还可能被高频模板占满，后续应以独立复核输出分层，保留原预测不回写。

本人参考流记录207次采样CD边沿，对应食人魔/魅惑魔女技能均不在当前库中，covered_by_bank为0，无法测这两局本人技能命中率。GSI参照只在预测冻结后读取；这不是漏识别率100%的证据。

下一步：固定这批预测，先按不同模板/技能及时间段抽取复听样本，确认是背景/其他系统音频、相似施法声还是模板覆盖过宽；人工听审仍由用户稍后安排，不自动填真值。不启用实战报警，不把同一探索集当独立测试集。

完整离线回归531项通过（diagnostics/offline-tests/run-c9ab78d07811/result.json），无失败/错误/跳过/后台异常，源码在回归中未变。包含实际生成信号运行分类器、预测先于GSI、变更来源/库/预测拒绝封存，以及原子回执发布失败不留成功标记；模拟测试不认证真实技能音效。
