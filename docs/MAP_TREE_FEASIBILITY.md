# 树木坐标与插眼前砍树：离线可行性

2026-10-30。用户要求分析树木坐标与职业眼位关系。本次仅用本机已安装游戏资源、已有10盘职业完整导出及本地协议描述；未开游戏、联网、连接Pi、重解析DEM、修改Foresight或自动提交。

## 已取得的静态树木

标准地图maps/dota.vpk中五份实体层，使用已有Source2Viewer CLI 20的DATA输出，只读解码，不自行猜测KV3二进制。父VPK固定SHA，逐资源CRC、编译字节SHA、解码文本SHA、解码器SHA均留在本机证据。当前steam.inf为ClientVersion 6969，不能据此补写职业录像的未知地图版本。

| 实体层 | ent_dota_tree记录 |
|---|---:|
| default_ents | 2306 |
| world_layer_radiant_base | 103 |
| world_layer_dire_base | 66 |
| radiant/dire destruction | 0 |
| 合计声明 | 2475 |

每条保存源实体层、实体序号、origin XYZ、模型及编译/Hammer标识。编译标识不当作运行时tree_id；同坐标多实体保留各自证据。基地子层激活状态、实际存活、碰撞范围均未知，2475不是任一时刻仍存活的树木数量。

新增dota2_insights/map_tree_inventory.py，只接受当前已识别的导出布局，按values对象隔离属性，不借邻近实体的origin。缺坐标、重复坐标字段、非有限/越界坐标、未解析树木和格式变化均拒收；没有ent_dota_tree的明确空层允许输出0。原游戏文件及历史记录只读。

## 与10盘1000条职业眼位的探索对照

复用此前冻结并核验的snapshot和profile，先核对收集清单的封存身份，再读取正文并验证SHA。以既有候选偏移16384连接原始眼位XY与地图中心坐标，只计算平面最近树距离，不填造眼位高度、树碰撞范围或路径通达性。

| 平面距离以内 | 假眼 | 真眼 |
|---|---:|---:|
| 32世界单位 | 2 | 1 |
| 64世界单位 | 10 | 8 |
| 128世界单位 | 58 | 158 |

这些阈值只是几何检查窗口，不是树的碰撞半径，也不是必砍树判定。录像game_build/map_version等均为空，当前地图与这些录像的版本对应未认证；故表内近树记录只作为候选。临近树木可能已经被其他技能清掉，也可能不妨碍放置，甚至属于另一版本的位置。

地图trees-and-wards.png：蓝色静态树木、绿色假眼、红色真眼。旧示意底图范围内2432条树木可见，另43条在底图外但完整保留在坐标清单。不能用裁剪后的图替代完整地图或强行移动这些坐标。未给任何点加黄色×；所有requires_cutting仍未知。

## 动态状态协议线索

本地固定protobuf确有CMsgBotWorldState.EventTree：tree_id、destroyed、respawned、location、delayed。其定义属于机器人世界状态，不能凭存在字段就声称普通录像或GSI会发送，也没有证实它与静态compile_source_id/Hammer ID相等。

首轮只有历史send-table补丁中的m_bWorldTreeState线索。后续单场6967普通录像的有界探查已实际发现uint64[256]字段及位变化，并找到补刀斧同帧变化、约0.467秒后同英雄插眼的强候选；详见[时间链核查](QUELLING_TREE_WARD_CHAIN.md)。TREE_CUT原始事件在该前十分钟窗口未见；树木索引—地图位置、位方向与必砍树仍未独立认证，未重跑所有录像。

后续按顺序：先核实录像与静态地图版本；在一场明确非封存录像中显式有界探查实际树木字段；建立运行时索引到静态坐标的对应并检查状态边界；最后结合插眼前时间窗、地形与放置空间判定。只验证“旁边树被砍过”仍不足以认证“必须砍树才能插”。证据不足时tree_clearance继续unknown。

## 验证与资料

860项完整离线回归通过diagnostics/offline-tests/run-59719be0a2e5/result.json，无失败/错误/跳过/后台异常，源码未变。6项新增提取器测试，以及原眼位出口/分期分析的20项专项通过。另以不调用提取器的逐行方式复核全部五层坐标多重集，与2475条提取结果相同；这是静态文件内部核对，不是游戏地图落位认证。

本机证据位于diagnostics/tree-feasibility-20261030/：static-trees.json、dota-map-inventory.json、compiled-resource-blocks.json、decode-summary.json、independent-and-protocol-check.json、ward-tree-comparison.json、ward-nearest-tree.jsonl、trees-and-wards.png及verification.json。原始游戏资源、导出文本和生成数据保持Git忽略，不随源码公开。
