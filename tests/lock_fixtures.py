"""Isolated synthetic lock policy; tests never depend on operator configuration."""
import json
from pathlib import Path
import tempfile
from unittest.mock import patch


def install_local_lock(testcase):
    temp = tempfile.TemporaryDirectory()
    testcase.addCleanup(temp.cleanup)
    root = Path(temp.name)
    (root/'data').mkdir(); (root/'config').mkdir()
    for name, matches in [('data/validation-locks.json', []),
                          ('config/validation-locks.local.json', ['700000001'])]:
        (root/name).write_text(json.dumps({'schema_version':1,'heldout_matches':matches}), encoding='utf-8')
    patcher = patch('dota2_insights.replay_holdout.ROOT', root)
    patcher.start()
    testcase.addCleanup(patcher.stop)
