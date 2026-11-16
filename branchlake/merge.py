import copy

from .errors import ConflictError, ValidationError
from .schema import validate


def reconcile(base, left, right):
    result = {}
    for identity in sorted(set(base) | set(left) | set(right), key=int):
        b, l, r = base.get(identity), left.get(identity), right.get(identity)
        if l == r:
            value = l
        elif l == b:
            value = r
        elif r == b:
            value = l
        else:
            raise ConflictError('incompatible changes to field identity ' + identity)
        if value is not None:
            result[identity] = copy.deepcopy(value)
    try:
        validate(result)
    except ValidationError as exc:
        raise ConflictError('merged schema is incompatible: ' + str(exc)) from exc
    return result


class Ancestry:
    def __init__(self, catalog):
        self.catalog = catalog
        self.cache = {}

    def ancestors(self, snapshot):
        if snapshot not in self.cache:
            # Iterative traversal also handles long linear snapshot histories.
            pending, found = [snapshot], set()
            while pending:
                current = pending.pop()
                if current in found:
                    continue
                if current in self.cache:
                    found.update(self.cache[current])
                    continue
                found.add(current)
                pending.extend(self.catalog.snapshot(current)['parents'])
            self.cache[snapshot] = found
        return self.cache[snapshot]

    def base(self, left_ancestors, right_ancestors):
        common = left_ancestors & right_ancestors
        if not common:
            raise ConflictError('no common ancestor')
        maximal = set(common)
        for candidate in common:
            maximal.difference_update(self.ancestors(candidate) - {candidate})
        candidates = sorted(maximal)
        first = candidates[0]
        schema = self.catalog.snapshot(first)['schema']
        accumulated = set(self.ancestors(first))
        for candidate in candidates[1:]:
            ancestry = self.ancestors(candidate)
            earlier = self.base(accumulated, ancestry)
            schema = reconcile(earlier, schema, self.catalog.snapshot(candidate)['schema'])
            accumulated.update(ancestry)
        return schema


def merge(catalog, target, source):
    with catalog.transaction():
        left_head, right_head = catalog.head(target), catalog.head(source)
        left, right = catalog.snapshot(left_head), catalog.snapshot(right_head)
        history = Ancestry(catalog)
        base = history.base(history.ancestors(left_head), history.ancestors(right_head))
        schema = reconcile(base, left['schema'], right['schema'])
        snapshot = catalog.publish(target, list(dict.fromkeys([left_head, right_head])),
                                   schema, left['batches'] + right['batches'])
    return dict(snapshot=snapshot)
