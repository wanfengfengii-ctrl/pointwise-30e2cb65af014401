import copy
import math
import re

from .errors import ValidationError

KINDS = {'int', 'float', 'str', 'bool', 'struct'}


def parts(path):
    if not isinstance(path, str):
        raise ValidationError('path must be a string')
    result = path.split('.')
    if len(result) not in (1, 2) or any(
        not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', part) for part in result
    ):
        raise ValidationError('paths have one or two identifier components')
    return result


def path_of(schema, identity):
    field = schema[identity]
    if field['parent'] is None:
        return field['name']
    return schema[field['parent']]['name'] + '.' + field['name']


def paths(schema):
    return {path_of(schema, identity): identity for identity in schema}


def check_value(kind, value):
    if value is None and kind != 'struct':
        return
    ok = ((kind == 'int' and type(value) is int)
          or (kind == 'float' and type(value) in (int, float)
              and (type(value) is int or math.isfinite(value)))
          or (kind == 'str' and type(value) is str)
          or (kind == 'bool' and type(value) is bool)
          or (kind == 'struct' and type(value) is dict and not value))
    if not ok:
        raise ValidationError('value does not match ' + kind)


def validate(schema):
    seen = set()
    for identity, field in schema.items():
        if field['type'] not in KINDS:
            raise ValidationError('unknown type')
        parts(field['name'])
        if '.' in field['name']:
            raise ValidationError('field names must be single components')
        parent = field['parent']
        if parent is not None:
            if (parent not in schema or schema[parent]['parent'] is not None
                    or schema[parent]['type'] != 'struct' or field['type'] == 'struct'):
                raise ValidationError('invalid struct parent')
        path = path_of(schema, identity)
        if path in seen:
            raise ValidationError('duplicate path: ' + path)
        seen.add(path)
        check_value(field['type'], field['default'])


def locate_parent(schema, path):
    components = parts(path)
    if len(components) == 1:
        return components[0], None
    parent = paths(schema).get(components[0])
    if parent is None or schema[parent]['type'] != 'struct':
        raise ValidationError('missing struct parent')
    return components[1], parent


def add(catalog, schema, definition):
    if (type(definition) is not dict or set(definition) - {'path', 'type', 'default'}
            or not {'path', 'type'} <= set(definition)):
        raise ValidationError('field requires path and type')
    path, kind = definition['path'], definition['type']
    if not isinstance(kind, str) or kind not in KINDS:
        raise ValidationError('unknown type')
    field_name, parent = locate_parent(schema, path)
    if path in paths(schema):
        raise ValidationError('duplicate path: ' + path)
    default = definition.get('default', {} if kind == 'struct' else None)
    check_value(kind, default)
    identity = catalog.identity(kind, default)
    schema[identity] = dict(name=field_name, parent=parent, type=kind,
                            default=copy.deepcopy(default))
    validate(schema)


def create(catalog, definitions):
    if type(definitions) is not list:
        raise ValidationError('fields must be a list')
    schema = {}
    for definition in definitions:
        add(catalog, schema, definition)
    return schema


def alter(catalog, original, changes):
    if type(changes) is not list or not changes:
        raise ValidationError('changes must be a nonempty list')
    schema = copy.deepcopy(original)
    for change in changes:
        if type(change) is not dict or not isinstance(change.get('op'), str):
            raise ValidationError('invalid change')
        op = change['op']
        if op == 'add':
            add(catalog, schema, {key: value for key, value in change.items() if key != 'op'})
            continue
        expected = {'op', 'path', 'to'} if op == 'rename' else {'op', 'path'}
        if op not in ('rename', 'drop') or set(change) != expected:
            raise ValidationError('invalid change keys or operation')
        parts(change['path'])
        identity = paths(schema).get(change['path'])
        if identity is None:
            raise ValidationError('unknown field')
        if op == 'drop':
            children = [key for key, field in schema.items() if field['parent'] == identity]
            for key in children + [identity]:
                del schema[key]
        else:
            field_name, parent = locate_parent(schema, change['to'])
            schema[identity]['name'] = field_name
            schema[identity]['parent'] = parent
        validate(schema)
    return schema
