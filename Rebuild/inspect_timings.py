#!/usr/bin/env python3
"""Summarize a local timing trace without reading sessions or changing terminals."""
import argparse
from collections import defaultdict
import json
from pathlib import Path


def summarize(path):
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    ends = {r['span']: r for r in rows if r['event'] == 'span.end'}
    children = defaultdict(float)
    for row in ends.values():
        if row.get('parent'): children[row['parent']] += row['duration_ms']
    totals = defaultdict(lambda: [0, 0.0, 0.0])
    for ident, row in ends.items():
        values = totals[row['name']]
        values[0] += 1
        values[1] += row['duration_ms']
        values[2] += max(0, row['duration_ms'] - children[ident])
    print(path.name)
    if rows:
        print('Action:', rows[0].get('action', 'unknown'), '| elapsed:', round(rows[-1]['elapsed_ms']/1000, 3), 'seconds')
    print(f'{"stage":30} {"calls":>6} {"total s":>10} {"self s":>10}')
    for name, (count, total, own) in sorted(totals.items(), key=lambda item: -item[1][1]):
        print(f'{name:30} {count:6} {total/1000:10.3f} {own/1000:10.3f}')
    print('Total includes child spans; self excludes them. Do not sum nested totals.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('path', nargs='?', type=Path,
        default=Path.home()/'Library/Application Support/Free Parking/Diagnostics')
    args = parser.parse_args()
    path = args.path
    if path.is_dir():
        files = list(path.glob('timing-*.jsonl'))
        if not files: parser.error('No timing traces in this directory.')
        path = max(files, key=lambda p: p.stat().st_mtime)
    summarize(path)
