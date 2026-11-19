"""Run the host-only executable models for the AquaLight implementation plan."""

from pathlib import Path
import subprocess
import sys


def main():
    model_dir = Path(__file__).resolve().parent
    models = sorted(model_dir.glob('check_*.py'))
    if not models:
        print('No communication models found.', file=sys.stderr)
        return 1

    failed = []
    for model in models:
        print(f'\n{model.name}', flush=True)
        result = subprocess.run([sys.executable, '-B', str(model)], cwd=model_dir.parents[1])
        if result.returncode:
            failed.append(model.name)

    print(f'\n{len(models) - len(failed)}/{len(models)} models passed.', flush=True)
    if failed:
        print('Failed: ' + ', '.join(failed), file=sys.stderr)
    return int(bool(failed))


if __name__ == '__main__':
    raise SystemExit(main())
