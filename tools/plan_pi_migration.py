"""Produce a local-only Pi migration/dependency checklist; no SSH or subprocess calls."""
import argparse
from datetime import datetime,timezone
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def main():
    from dota2_insights.pi_deployment import build_plan
    from dota2_insights.history import checked_output
    from dota2_insights.distribution_bundle import read_json
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--legacy-config',type=Path)
    args=parser.parse_args()
    plan=build_plan(ROOT)
    local={'new_config_present':(ROOT/'config/pi_replay_library.local.json').is_file(),
           'legacy_config_present':False,'configuration_copied':False,'private_key_file_opened':False,
           'configuration_values_exported':False}
    if args.legacy_config is not None and args.legacy_config.is_file():
        data,_=read_json(args.legacy_config,16384)
        local.update(legacy_config_present=True,legacy_enabled=data.get('enabled') is True,
            identity_file_present=isinstance(data.get('identity_file'),str) and Path(data['identity_file']).is_file(),
            managed_mode=data.get('managed_on_pi') is True,
            explicit_program_path=bool(data.get('remote_program')),explicit_data_path=bool(data.get('remote_data')),
            local_recordings_configured=isinstance(data.get('own_replay_directories'),list))
        # No credential file is opened; configuration values are not exported.
    plan['local_configuration_checks']=local
    output=checked_output(ROOT,ROOT/'diagnostics/pi-migration-plan'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    output.mkdir(parents=True,exist_ok=False)
    (output/'plan.json').write_text(json.dumps(plan,ensure_ascii=False,indent=2),encoding='utf-8')
    lines=['# Pi迁移离线准备','',
        '仅静态源码与本地配置存在性检查，没有SSH、部署、重启服务、下载或解析录像。','',
        f"部署源码/公共数据文件：{len(plan['files'])}；总字节：{sum(r['bytes'] for r in plan['files'].values())}。",
        f"外部依赖根：{', '.join(plan['external_import_roots']) or '无'}。需在Pi原有固定环境核查，未打包Windows依赖。",'',
        '本机配置状态（不含地址、用户名、私钥或路径值）：',json.dumps(local,ensure_ascii=False),'',
        '后续远端验收：确认当前解析任务已结束→备份程序与服务配置→仅更新代码/公共锁名单→检查固定Gem/Protobuf/Snappy环境→单场非封存录像验收到EOF→确认本人录像不占100盘高分额度及两个数据目录不变。',
        '本轮不执行上述远端步骤，不自动把旧配置复制成启用状态。','',
        '| 部署目标相对路径 | 字节 | SHA256 |','|---|---:|---|']
    for name,ref in plan['files'].items():lines.append(f"|{name}|{ref['bytes']}|{ref['sha256']}|")
    (output/'REPORT.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(json.dumps({'report':str(output/'REPORT.md'),'files':len(plan['files']),
        'remote_deploy_executed':False},ensure_ascii=False))


if __name__=='__main__':main()
