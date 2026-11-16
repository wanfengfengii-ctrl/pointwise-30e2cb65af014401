import hashlib

from .catalog import encode
from .errors import ConflictError, ValidationError
from .schema import check_value, paths


def payload(schema, rows):
    if type(rows) is not list or not rows:
        raise ValidationError('rows must be a nonempty list')
    by_path = paths(schema)
    result = []
    for row in rows:
        if type(row) is not dict:
            raise ValidationError('each row must be an object')
        values = {}
        for key, value in row.items():
            if key not in by_path:
                raise ValidationError('unknown root field')
            identity = by_path[key]
            field = schema[identity]
            if field['type'] == 'struct':
                if type(value) is not dict:
                    raise ValidationError('struct containers must be objects')
                for child, cell in value.items():
                    child_id = by_path.get(key + '.' + child) if isinstance(child, str) else None
                    if child_id is None or schema[child_id]['parent'] != identity:
                        raise ValidationError('unknown struct child')
                    check_value(schema[child_id]['type'], cell)
                    values[child_id] = cell
            else:
                check_value(field['type'], value)
                values[identity] = value
        result.append(values)
    return result


def append(catalog, branch, request, rows):
    if not isinstance(request, str) or not request or len(request) > 128:
        raise ValidationError('invalid request id')
    fingerprint = hashlib.sha256(encode(rows).encode('utf-8')).hexdigest()
    with catalog.transaction():
        head = catalog.head(branch)
        previous = catalog.db.execute(
            'SELECT fingerprint,snapshot,batch FROM receipts WHERE branch=? AND request=?',
            (branch, request)
        ).fetchone()
        if previous:
            if previous[0] != fingerprint:
                raise ConflictError('request id reused with different rows')
            return dict(snapshot=previous[1], batch=previous[2], request=request)
        snapshot = catalog.snapshot(head)
        encoded_rows = payload(snapshot['schema'], rows)
        cursor = catalog.db.execute(
            'INSERT INTO batches(writer,payload) VALUES(?,?)', (head, encode(encoded_rows))
        )
        batch = cursor.lastrowid
        new_head = catalog.publish(branch, [head], snapshot['schema'],
                                   snapshot['batches'] + [batch],
                                   snapshot['corrections'])
        catalog.db.execute('INSERT INTO receipts VALUES(?,?,?,?,?)',
                           (branch, request, fingerprint, new_head, batch))
    return dict(snapshot=new_head, batch=batch, request=request)
