"""Independent, deliberately naive state reference for the correction tests.

Each branch state maps a source key ``(batch, index)`` to the *effective*
public cells of that source::

    {(batch, index): {"retracted": bool, "cells": {field_name: value}}}

Rows are written here as explicit cell dictionaries (defaults already applied),
which keeps the reference completely separate from the production readers,
schema or merge code.  Corrections rewrite cells explicitly, and merges compare
effective cells relative to the ancestor state exactly as the documented rule
reads.
"""


def state(**cells):
    return {'retracted': False, 'cells': dict(cells)}


def copy(states):
    return {key: {'retracted': entry['retracted'], 'cells': dict(entry['cells'])}
            for key, entry in states.items()}


def patch(states, source, before, after):
    entry = states[source]
    assert not entry['retracted']
    for name, value in before.items():
        assert entry['cells'][name] == value, (name, entry['cells'][name], value)
    updated = copy(states)
    cells = dict(entry['cells'])
    cells.update(after)
    updated[source] = {'retracted': False, 'cells': cells}
    return updated


def remove(states, source, before):
    entry = states[source]
    assert not entry['retracted']
    for name, value in before.items():
        assert entry['cells'][name] == value, (name, entry['cells'][name], value)
    updated = copy(states)
    updated[source] = {'retracted': True, 'cells': dict(entry['cells'])}
    return updated


def merge(base, left, right, names):
    """Three-way merge of explicit states; raises ValueError on real conflict."""
    result = {}
    for source in sorted(set(base) | set(left) | set(right)):
        b = base.get(source)
        l = left.get(source)
        r = right.get(source)
        if b is None:
            if l is None or r is None:
                chosen = r if l is None else l
                result[source] = {'retracted': chosen['retracted'],
                                  'cells': dict(chosen['cells'])}
            elif l == r:
                result[source] = {'retracted': l['retracted'],
                                  'cells': dict(l['cells'])}
            else:
                raise ValueError('source introduced independently on both sides')
            continue
        if b['retracted']:
            result[source] = {'retracted': True, 'cells': dict(b['cells'])}
            continue
        if l['retracted'] or r['retracted']:
            if l['retracted'] and r['retracted']:
                result[source] = {'retracted': True, 'cells': dict(b['cells'])}
                continue
            live = r if l['retracted'] else l
            if any(live['cells'][name] != b['cells'][name] for name in names):
                raise ValueError('retraction vs modification')
            result[source] = {'retracted': True, 'cells': dict(b['cells'])}
            continue
        cells = {}
        for name in names:
            bv, lv, rv = b['cells'][name], l['cells'][name], r['cells'][name]
            if lv == rv or rv == bv:
                cells[name] = lv
            elif lv == bv:
                cells[name] = rv
            else:
                raise ValueError('cell conflict on ' + name)
        result[source] = {'retracted': False, 'cells': cells}
    return result


def project(states):
    """Public read_rows shape, ordered by batch then original row index."""
    rows = []
    for key in sorted(states):
        entry = states[key]
        if not entry['retracted']:
            rows.append({'source': [key[0], key[1]], 'row': dict(entry['cells'])})
    return rows
