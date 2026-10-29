"""Prepare same-slot swap/removal controls from current closed equipment packages."""
import argparse
from html import escape
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from dota2_insights.history import history_roots,checked_output
from dota2_insights.observation_index import sync
from dota2_insights.record_content_review import item_catalogue
from dota2_insights.item_transition_review import prepare


def main():
    cli=argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--latest',type=int,default=3);cli.add_argument('--limit-per-kind',type=int,default=24)
    cli.add_argument('--output',type=Path,required=True);args=cli.parse_args()
    output=checked_output(ROOT,args.output)
    if output.exists():cli.error('new output directory required')
    roots=history_roots(ROOT/'config/history.local.json');index=sync(ROOT,roots,latest=args.latest)
    catalogue,source=item_catalogue(ROOT);output.mkdir(parents=True);summaries=[];links=[]
    for entry in index['entries']:
        if entry['state']!='ready_with_gaps':continue
        folder=output/entry['package_id']
        r=prepare(entry,roots,catalogue,folder,limit=args.limit_per_kind)
        summaries.append({'match_id':r['match_id'],'package_id':entry['package_id'],'cases':len(r['cases']),'counts':r['counts']})
        links.append('<li><a href="'+entry['package_id']+'/index.html">'+escape(str(r['match_id']))+'</a> · '+str(len(r['cases']))+'组</li>')
    (output/'summary.json').write_text(json.dumps({'status':'complete','packages':summaries,'catalogue_source':source,
        'index_blocked_entries':[e for e in index['entries'] if e['state']!='ready_with_gaps'],
        'accuracy_verified':False},ensure_ascii=False,indent=2),encoding='utf-8')
    (output/'index.html').write_text('<!doctype html><meta charset="utf-8"><h1>物品变化反例</h1><ul>'+''.join(links)+'</ul>',encoding='utf-8')
    print(json.dumps(summaries,ensure_ascii=False))


if __name__=='__main__':main()
