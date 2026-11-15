"""Rebuild moonmesh-shared-data-haworth.zip from its seven parts and unpack it into the MOONMESH data folder.

Works on Windows, macOS and Linux with only the Python standard library. From the hackNasa folder:

    python shared-data/unpack.py                          # into $MOONMESH_DATA_ROOT, or hackNasa/data if unset
    python shared-data/unpack.py --data-root D:\\moonmesh-data

hackNasa/data is where the app looks when MOONMESH_DATA_ROOT is not set, so the default needs no variable.
shared-data/unpack.ps1 does the same on Windows PowerShell, defaulting to C:\\moonmesh-data.
"""
import argparse
import hashlib
import os
import zipfile
from pathlib import Path

EXPECTED_SHA256 = 'a85982563c8ba0bd2624a3e2cb7c907f9affb388cc61eb2a5d439d3b25e1ebf2'
PARTS = 7
HERE = Path(__file__).resolve().parent
REPO = HERE.parent


def default_root():
    """The same folder moonmesh_train.paths.data_root() resolves to."""
    configured = os.environ.get('MOONMESH_DATA_ROOT', '').strip()
    if not configured:
        return REPO/'data'
    path = Path(configured).expanduser()
    return path if path.is_absolute() else REPO/path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--data-root', type=Path, help='folder to unpack into (default: $MOONMESH_DATA_ROOT, '
                                                       'otherwise hackNasa/data)')
    args = parser.parse_args(argv)
    root = (args.data_root.expanduser() if args.data_root else default_root()).resolve()

    parts = sorted(HERE.glob('moonmesh-shared-data-haworth.zip.0*'))
    if len(parts) != PARTS:
        raise SystemExit(f'Expected {PARTS} zip parts in {HERE} but found {len(parts)}. Run git pull again.')
    if any(part.stat().st_size < 1_000_000 for part in parts):
        raise SystemExit('A zip part is only a few bytes: it is a placeholder, not the data. Run git pull again '
                         '(and, if the repository ever moves to Git LFS, git lfs pull).')

    root.mkdir(parents=True, exist_ok=True)
    joined = root/'moonmesh-shared-data-haworth.zip'
    digest = hashlib.sha256()
    print(f'Joining {len(parts)} parts into {joined} ...', flush=True)
    with joined.open('wb') as out:
        for part in parts:
            with part.open('rb') as source:
                for chunk in iter(lambda: source.read(1 << 20), b''):
                    digest.update(chunk)
                    out.write(chunk)
    if digest.hexdigest() != EXPECTED_SHA256:
        joined.unlink()
        raise SystemExit(f'Checksum mismatch ({digest.hexdigest()}). The parts are incomplete or changed; '
                         'run git pull again.')

    print(f'Checksum OK. Unpacking into {root} (this can take a minute) ...', flush=True)
    with zipfile.ZipFile(joined) as archive:
        for member in archive.infolist():
            if not (root/member.filename).resolve().is_relative_to(root):
                raise SystemExit(f'Refusing to unpack {member.filename!r} outside {root}')
        archive.extractall(root)
    joined.unlink()

    print('Done: terrain, the trained LightGBM model and its reports are in', root)
    if root != (REPO/'data').resolve():
        print(f'Set MOONMESH_DATA_ROOT={root} before starting the app or running the training scripts.')
    print('Next: follow shared-data/README.md to create the training environment and start the app.')


if __name__ == '__main__':
    main()
