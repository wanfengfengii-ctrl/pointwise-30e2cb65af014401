"""Source-addressed corrections: validation, publication and fixed-snapshot reads.

Correction state is stored per snapshot as a mapping from a stable source key
(``"<batch>:<index>"``) to ``{"retracted": bool, "values": {identity: value}}``.
Overrides are keyed by field creation identity, never by display path, so a
rename keeps the corrected value and a drop/recreate under the same name does
not inherit it.  An identity that is absent from both payload and overrides
falls back to the identity's fixed creation default.
"""

import copy
import hashlib

from .catalog import encode
from .errors import ConflictError, ValidationError
from .schema import check_value, paths, path_of

LIVE = {'retracted': False, 'values': {}}


def source_key(batch, index):
    return str(batch) + ':' + str(index)


def entry_of(corrections, key):
    return corrections.get(key, LIVE)


def effective(catalog, cells, entry, identity):
    """Effective value of one scalar identity for one source."""
    values = entry['values']
    if identity in values:
        return values[identity]
    if identity in cells:
        return cells[identity]
    return catalog.field_event(identity)[1]


def baseline(catalog, cells, identity):
    if identity in cells:
        return cells[identity]
    return catalog.field_event(identity)[1]


def project_row(catalog, schema, cells, entry):
    """Public nested row under a fixed schema, honouring overrides."""
    row = {field['name']: {} for field in schema.values() if field['type'] == 'struct'}
    for identity, field in schema.items():
        if field['type'] == 'struct':
            continue
        value = copy.deepcopy(effective(catalog, cells, entry, identity))
        path = path_of(schema, identity).split('.')
        if len(path) == 1:
            row[path[0]] = value
        else:
            row[path[0]][path[1]] = value
    return row


def rows(catalog, snapshot_id):
    """[(source, row)] in batch-id then original-row order, retractions skipped."""
    snapshot = catalog.snapshot(snapshot_id)
    schema = snapshot['schema']
    corrections = snapshot['corrections']
    result = []
    for batch in sorted(snapshot['batches']):
        cells_list = catalog.batch_cells(batch)
        for index, cells in enumerate(cells_list):
            key = source_key(batch, index)
            entry = entry_of(corrections, key)
            if entry['retracted']:
                continue
            result.append(([batch, index],
                           project_row(catalog, schema, cells, entry)))
    return result


def read_rows(catalog, snapshot_id):
    return [dict(source=list(source), row=row)
            for source, row in rows(catalog, snapshot_id)]


def validate_request(request):
    if not isinstance(request, str) or not request or len(request) > 128:
        raise ValidationError('invalid request id')


def validate_source(source):
    if type(source) is not list or len(source) != 2:
        raise ValidationError('source must be a [batch, index] array')
    batch, index = source
    if type(batch) is not int or type(index) is not int:
        raise ValidationError('source components must be integers')
    if batch < 1 or index < 0:
        raise ValidationError('source components out of range')
    return batch, index


def _schema_scalar(schema, path):
    if not isinstance(path, str):
        raise ValidationError('correction paths must be strings')
    identity = paths(schema).get(path)
    if identity is None:
        raise ValidationError('unknown field path: ' + path)
    field = schema[identity]
    if field['type'] == 'struct':
        raise ValidationError('cannot patch a struct container')
    return identity, field['type']


def _check_like(kind, value):
    try:
        check_value(kind, value)
    except ValidationError as exc:
        raise ValidationError('invalid correction value: ' + str(exc)) from exc


def _public_row(schema, row):
    """Validate a complete public row shape and types; return it unchanged."""
    if type(row) is not dict:
        raise ValidationError('remove before must be a complete row object')
    roots = {identity: field for identity, field in schema.items()
             if field['parent'] is None}
    names = {field['name'] for field in roots.values()}
    if set(row) != names:
        raise ValidationError('remove before must contain exactly the public fields')
    for identity, field in roots.items():
        value = row[field['name']]
        if field['type'] == 'struct':
            if type(value) is not dict:
                raise ValidationError('struct containers must be objects')
            children = {child: child_field for child, child_field in schema.items()
                        if child_field['parent'] == identity}
            expected = {child_field['name'] for child_field in children.values()}
            if set(value) != expected:
                raise ValidationError('struct container must contain exactly its fields')
            by_name = {child_field['name']: child
                       for child, child_field in children.items()}
            for name, cell in value.items():
                _check_like(schema[by_name[name]]['type'], cell)
        else:
            _check_like(field['type'], value)
    return row


def _parse(catalog, schema, changes):
    if type(changes) is not list or not changes:
        raise ValidationError('changes must be a nonempty list')
    parsed = []
    seen = set()
    for change in changes:
        if type(change) is not dict:
            raise ValidationError('each change must be an object')
        op = change.get('op')
        if op == 'patch':
            if set(change) != {'source', 'op', 'before', 'after'}:
                raise ValidationError('invalid patch change keys')
            before, after = change['before'], change['after']
            if (type(before) is not dict or type(after) is not dict
                    or not before or set(before) != set(after)):
                raise ValidationError(
                    'patch before and after must be nonempty objects with equal keys')
            bindings = []
            for path in before:
                identity, kind = _schema_scalar(schema, path)
                _check_like(kind, before[path])
                _check_like(kind, after[path])
                bindings.append((identity, after[path]))
        elif op == 'remove':
            if set(change) != {'source', 'op', 'before'}:
                raise ValidationError('invalid remove change keys')
            expected = _public_row(schema, change['before'])
            bindings = expected
        else:
            raise ValidationError('unknown correction operation')
        batch, index = validate_source(change['source'])
        key = source_key(batch, index)
        if key in seen:
            raise ValidationError('duplicate source in one correction')
        seen.add(key)
        if op == 'patch':
            parsed.append(dict(op=op, source=(batch, index), key=key,
                               before=before, bindings=bindings))
        else:
            parsed.append(dict(op=op, source=(batch, index), key=key,
                               before=bindings))
    return parsed


def _check_old_image(catalog, snapshot, corrections, cells, parsed):
    """Validate source ownership, retraction and pre-batch images."""
    batches = set(snapshot['batches'])
    schema = snapshot['schema']
    for item in parsed:
        batch, index = item['source']
        if batch not in batches or index >= len(cells[batch]):
            raise ConflictError('source does not belong to this branch')
        entry = entry_of(corrections, item['key'])
        if entry['retracted']:
            raise ConflictError('source has been retracted')
        cells_for_source = cells[batch][index]
        if item['op'] == 'patch':
            for identity, _ in item['bindings']:
                path = path_of(schema, identity)
                if not _same(effective(catalog, cells_for_source, entry, identity),
                             item['before'][path]):
                    raise ConflictError('patch before image does not match')
        else:
            current = project_row(catalog, schema, cells_for_source, entry)
            if not _same(current, item['before']):
                raise ConflictError('remove before image does not match')


def _same(left, right):
    """Equality where int 1 equals float 1.0 and object key order is irrelevant."""
    if type(left) is dict or type(right) is dict:
        if type(left) is not dict or type(right) is not dict:
            return False
        return set(left) == set(right) and all(_same(left[key], right[key]) for key in left)
    if type(left) is bool or type(right) is bool:
        return type(left) is type(right) and left == right
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return left == right
    return type(left) is type(right) and left == right


def correct(catalog, branch, request, changes):
    validate_request(request)
    fingerprint = hashlib.sha256(encode(changes).encode('utf-8')).hexdigest()
    with catalog.transaction():
        head = catalog.head(branch)
        previous = catalog.db.execute(
            'SELECT fingerprint,snapshot FROM correction_receipts '
            'WHERE branch=? AND request=?', (branch, request)
        ).fetchone()
        if previous:
            if previous[0] != fingerprint:
                raise ConflictError('request id reused with different changes')
            return dict(snapshot=previous[1], request=request)
        snapshot = catalog.snapshot(head)
        parsed = _parse(catalog, snapshot['schema'], changes)
        cells = {batch: catalog.batch_cells(batch) for batch in snapshot['batches']}
        _check_old_image(catalog, snapshot, snapshot['corrections'], cells, parsed)
        corrections = copy.deepcopy(snapshot['corrections'])
        for item in parsed:
            entry = copy.deepcopy(entry_of(corrections, item['key']))
            if item['op'] == 'patch':
                for identity, value in item['bindings']:
                    if _same(value, baseline(catalog, cells[item['source'][0]][
                            item['source'][1]], identity)):
                        entry['values'].pop(identity, None)
                    else:
                        entry['values'][identity] = copy.deepcopy(value)
                if entry['values'] or entry['retracted']:
                    corrections[item['key']] = entry
                else:
                    corrections.pop(item['key'], None)
            else:
                corrections[item['key']] = dict(retracted=True,
                                                values=entry['values'])
        new_head = catalog.publish(branch, [head], snapshot['schema'],
                                   snapshot['batches'], corrections)
        catalog.db.execute(
            'INSERT INTO correction_receipts VALUES(?,?,?,?)',
            (branch, request, fingerprint, new_head))
    return dict(snapshot=new_head, request=request)
