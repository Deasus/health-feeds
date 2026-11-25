#!/usr/bin/env python3
"""Run a backfill step the way conductor.py runs a feed (used only by .github/workflows/backfill.yml).

  python3 feeds/backfill/run.py fetch <slug>                        prepare work/backfill, restore the `backfill` branch
                                                                    (the previous legs' checkpoints), fetch one source
  python3 feeds/backfill/run.py merge --leg N --overlay DIR [--publish]
                                                                    restore, lay each fetch job's sources/<slug>/ from DIR
                                                                    over it, merge, and publish `backfill` as one
                                                                    parentless commit (conductor.publish)

Uses conductor.prepare / restore / publish unchanged, so the branch convention is the same as every feed's.
"""
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
import conductor  # noqa: E402

SPEC = {'code': ['backfill', '_shared', 'gdelt', 'wahis', 'paho', 'hdx', 'reliefweb']}


def main(argv) -> int:
    publish = '--publish' in argv
    overlay = argv[argv.index('--overlay') + 1] if '--overlay' in argv else None
    args = [a for i, a in enumerate(argv) if a not in ('--publish', '--overlay') and (i == 0 or argv[i - 1] != '--overlay')]
    wd = conductor.prepare('backfill', SPEC)
    data = os.path.join(wd, 'data')
    conductor.restore('backfill', data)
    if overlay and os.path.isdir(overlay):
        for root, _dirs, files in os.walk(overlay):              # artifacts arrive as <overlay>/<artifact>/sources/<slug>/…
            if os.path.basename(os.path.dirname(root)) == 'sources':
                dest = os.path.join(data, 'sources', os.path.basename(root))
                os.makedirs(dest, exist_ok=True)
                for f in files:
                    shutil.copy2(os.path.join(root, f), os.path.join(dest, f))
    # BACKFILL_REPO_ROOT: bf_gdelt reads the conductor's status branch through this checkout (ConductorGuard)
    r = subprocess.run([sys.executable, 'backfill.py', *args], cwd=wd, env=dict(os.environ, PYTHONUNBUFFERED='1', BACKFILL_REPO_ROOT=ROOT))
    if r.returncode != 0:
        return r.returncode
    if publish:
        conductor.publish('backfill', data, force=True)
        conductor.log('[backfill] published')
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
