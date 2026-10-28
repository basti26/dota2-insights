# 闭合录音技术复核

后续盲测入口已补闭合包、参考库前后验证和最终原子回执；见CLOSED_AUDIO_INFERENCE.md。技术完整性通过不等于音效可用，本次实际基线候选集中于重生模板，仍不启用预警。

入口：`C:\Python312\python.exe tools/review_recordings.py --latest 3`。与经济、装备、BP、小地图交接及性能报告写入同一份`diagnostics/record-content/<UTC>/REPORT.md`和`report.json`。

`dota2_insights/audio_integrity_review.py`只检查已有闭合的record-only录音，不启动录音、播放或技能分类。未闭合/旧摘要矛盾列为不可用，不写成零录音；原始资料不回写。

## 验证范围

- 读取前后验证原交接包和日志；摘要必须complete、shutdown_complete，线程停止、非finalizing、无error，身份与所在目录一致。
- 日志固定在同一采集目录，拒绝重名、路径跳出、符号链接或目录连接；每段最多4MiB、单声道16000Hz int16 PCM，时长至多10秒，总段数至多20000。
- WAV与记录SHA匹配，解码长度/帧数/时长一致；全扫描后再读WAV核对SHA和大小，不拿日志声明代替文件核验。
- 同take音频偏移不重叠、不倒退；摘要段数、音频秒数及WAV总字节须与核验值一致。
- 全零、RMS低于-60dBFS、int16极值样本为技术指标。非零不证明技能可闻，未触顶不证明混音无失真；不自动标注、训练或修改识别阈值。
- 写入时match_association检查已知/封存比赛；它是写入时快照，不能认证每个采样点属于哪局，不把全take按UTC猜分局。

当前声音来自默认输出设备系统混音，不是独立Dota进程音轨。host_delivery_and_consume_boundaries不是精确音频采样墙钟，不能据此发布精确技能使用时间或实战延迟。

## 已有资料结果

2026-10-26晚结束的最新启动记录：961段、4788.2秒、153264684字节全部核验通过。音频偏移无缺口；全零段0、RMS低于-60dBFS段0、int16极值样本0。写入时比赛标签为420段与541段；只作声明计数，不是精确分局边界。两份更早的音频摘要仍未通过收尾门禁，没有改写。

最终实际联合报告：`diagnostics/record-content/20261026T163951741342Z/`。没有听审或运行音效分类，26段旧听审仍由用户稍后安排。
完整离线回归524项通过，失败/错误/跳过/后台异常均0、回归源码未变；证据`diagnostics/offline-tests/run-92d902c1a788/result.json`。
