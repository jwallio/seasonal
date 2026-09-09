"""Restore validated current-method artifacts without overwriting valid cache pairs."""
import argparse
import json
from pathlib import Path
import shutil
import cfsv2_surface_phase as phase


def valid(directory, init, target):
    try:
        phase.load_month(directory, init, target)
        return True
    except (OSError, ValueError, KeyError, EOFError):
        return False


def restore(source, destination):
    source, destination = Path(source), Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    restored = kept = rejected = 0
    for metadata in sorted(source.glob('surface-phase-*.json')):
        try:
            meta = json.loads(metadata.read_text())
            init, target = meta['initialization'], meta['target_month']
            if metadata.stem != f'surface-phase-{init}-{target}':
                raise ValueError('Invalid bundle filename')
        except (ValueError, KeyError):
            rejected += 1
            continue
        if valid(destination, init, target):
            kept += 1
            continue
        if not valid(source, init, target):
            rejected += 1
            continue
        # Copy only a fully validated pair. Acquisition validates it again before reuse.
        for suffix in ('.npz', '.json'):
            shutil.copy2(metadata.with_suffix(suffix), destination / metadata.with_suffix(suffix).name)
        restored += 1
    print(f'Checkpoints: {restored} restored, {kept} cached pairs preserved, {rejected} obsolete/invalid skipped')
    return restored, kept, rejected


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source')
    parser.add_argument('destination')
    args = parser.parse_args()
    restore(args.source, args.destination)
