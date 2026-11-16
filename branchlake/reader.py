import copy
import json

from .schema import path_of


def read(catalog, snapshot_id):
    snapshot = catalog.snapshot(snapshot_id)
    schema = snapshot['schema']
    result = []
    for batch in snapshot['batches']:
        text = catalog.db.execute('SELECT payload FROM batches WHERE id=?', (batch,)).fetchone()[0]
        for cells in json.loads(text):
            row = {field['name']: {} for field in schema.values()
                   if field['type'] == 'struct'}
            for identity, field in schema.items():
                if field['type'] == 'struct':
                    continue
                value = copy.deepcopy(cells[identity] if identity in cells else field['default'])
                path = path_of(schema, identity).split('.')
                if len(path) == 1:
                    row[path[0]] = value
                else:
                    row[path[0]][path[1]] = value
            result.append(row)
    return result
