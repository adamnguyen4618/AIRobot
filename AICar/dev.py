#!/usr/bin/env python3
"""Dev runner: restarts main.py when a source file changes (usage: python3 dev.py)."""
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
WATCHED = {'.py', '.ui', '.qrc', '.png'}
IGNORED_DIRS = {'__pycache__', '.idea', 'Application', 'build', 'dist'}
# Generated from .ui/.qrc; skip them so regeneration does not trigger a second restart.
GENERATED = {'Client_Ui.py', 'picture_rc.py'}


def snapshot():
    state = {}
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in IGNORED_DIRS]
        for name in filenames:
            if os.path.splitext(name)[1] in WATCHED and name not in GENERATED and name != 'dev.py':
                path = os.path.join(dirpath, name)
                try:
                    state[path] = os.stat(path).st_mtime
                except OSError:
                    pass
    return state


def regenerate(changed):
    for path in changed:
        base, ext = os.path.splitext(path)
        if ext == '.ui':
            cmd = [sys.executable, '-m', 'PyQt5.uic.pyuic', path, '-o', base + '.py']
        elif ext == '.qrc':
            cmd = ['pyrcc5', path, '-o', base + '_rc.py']
        else:
            continue
        try:
            subprocess.run(cmd, check=True, cwd=ROOT)
        except (OSError, subprocess.CalledProcessError) as e:
            print(f'[dev] regenerate failed for {os.path.basename(path)}: {e}')


def start():
    return subprocess.Popen([sys.executable, 'main.py'], cwd=ROOT)


def stop(proc):
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def main():
    known = snapshot()
    proc = start()
    try:
        while True:
            time.sleep(0.5)
            current = snapshot()
            changed = [p for p, m in current.items() if known.get(p) != m]
            if changed:
                print('[dev] changed: ' + ', '.join(os.path.basename(p) for p in changed))
                known = current
                stop(proc)
                regenerate(changed)
                proc = start()
    except KeyboardInterrupt:
        pass
    finally:
        stop(proc)


if __name__ == '__main__':
    main()
