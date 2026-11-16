import copy

from .corrections import entry_of, effective, source_key
from .errors import ConflictError, ValidationError
from .schema import validate


def _same(left, right):
    """Equality where numeric 1 == 1.0 and object key order is irrelevant."""
    if type(left) is dict or type(right) is dict:
        if type(left) is not dict or type(right) is not dict:
            return False
        return set(left) == set(right) and all(_same(left[key], right[key]) for key in left)
    if type(left) is bool or type(right) is bool:
        return type(left) is type(right) and left == right
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return left == right
    return type(left) is type(right) and left == right


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


def _identity_universe(catalog, states):
    identities = set()
    batches = set()
    for state in states:
        identities.update(state['schema'])
        for entry in state['corrections'].values():
            identities.update(entry['values'])
        batches.update(state['batches'])
    for batch in batches:
        for cells in catalog.batch_cells(batch):
            identities.update(cells)
    return identities


def _source_map(catalog, state):
    result = {}
    for batch in state['batches']:
        for index in range(len(catalog.batch_cells(batch))):
            result[source_key(batch, index)] = (batch, index)
    return result


def _normalise(catalog, cells, entry):
    values = {}
    for identity, value in entry['values'].items():
        if not _same(value, _baseline(catalog, cells, identity)):
            values[identity] = copy.deepcopy(value)
    return values


def _baseline(catalog, cells, identity):
    if identity in cells:
        return cells[identity]
    return catalog.field_event(identity)[1]


def _merge_source(catalog, key, base, left, right, identities):
    """Resolve one source relative to its common-ancestor state.

    ``base``/``left``/``right`` are correction entries (``{retracted, values}``)
    or None when the source is absent from that state.  Returns the merged
    entry.  A source present identically on both sides and absent from the
    ancestor is a shared introduction and keeps one copy of its state.
    """
    batch, index = (int(part) for part in key.split(':'))
    cells = catalog.batch_cells(batch)[index]

    if base is None:
        if left is None and right is None:
            raise ConflictError('internal: source vanished')
        if left is None:
            return dict(retracted=right['retracted'],
                        values=_normalise(catalog, cells, right))
        if right is None:
            return dict(retracted=left['retracted'],
                        values=_normalise(catalog, cells, left))
        # Both sides hold a source the consolidated ancestor lacks (for example
        # a batch both sides merged in from a third branch).  Its virtual
        # ancestor is the immutable payload/default state, so independent
        # corrections merge cell by cell and the source is kept exactly once.
        base = {'retracted': False, 'values': {}}

    if left is None or right is None:
        # A state without a committed source carries no information about it;
        # in monotonic histories this only arises while consolidating a virtual
        # ancestor, where the missing side is unchanged from the base.
        if left is None:
            left = base
        if right is None:
            right = base

    if base['retracted']:
        return dict(retracted=True, values={})

    if left['retracted'] or right['retracted']:
        if left['retracted'] and right['retracted']:
            return dict(retracted=True, values={})
        live_entry = right if left['retracted'] else left
        for identity in sorted(identities, key=int):
            if not _same(effective(catalog, cells, live_entry, identity),
                         effective(catalog, cells, base, identity)):
                raise ConflictError(
                    'retraction conflicts with modification of field identity '
                    + identity)
        return dict(retracted=True, values={})

    base_values = {identity: effective(catalog, cells, base, identity)
                   for identity in identities}
    left_values = {identity: effective(catalog, cells, left, identity)
                   for identity in identities}
    right_values = {identity: effective(catalog, cells, right, identity)
                    for identity in identities}
    values = {}
    for identity in sorted(identities, key=int):
        b, l, r = base_values[identity], left_values[identity], right_values[identity]
        if _same(l, r) or _same(r, b):
            value = l
        elif _same(l, b):
            value = r
        else:
            raise ConflictError(
                'incompatible corrections to field identity ' + identity)
        if not _same(value, _baseline(catalog, cells, identity)):
            values[identity] = copy.deepcopy(value)
    return dict(retracted=False, values=values)


def merge_data(catalog, base, left, right):
    """Three-way correction merge; returns {source: entry} without publishing."""
    identities = _identity_universe(catalog, (base, left, right))
    source_map = {}
    source_map.update(_source_map(catalog, base))
    source_map.update(_source_map(catalog, left))
    source_map.update(_source_map(catalog, right))
    base_sources = _source_map(catalog, base)
    left_sources = _source_map(catalog, left)
    right_sources = _source_map(catalog, right)
    corrections = {}

    def entry_of_source(state, sources, key):
        if key not in sources:
            return None
        return entry_of(state['corrections'], key)

    for key in sorted(source_map, key=lambda item: tuple(int(part) for part in item.split(':'))):
        merged = _merge_source(
            catalog, key,
            entry_of_source(base, base_sources, key),
            entry_of_source(left, left_sources, key),
            entry_of_source(right, right_sources, key),
            identities)
        if merged['retracted'] or merged['values']:
            corrections[key] = merged
    return corrections


class Ancestry:
    def __init__(self, catalog):
        self.catalog = catalog
        self.cache = {}
        self._state_cache = {}
        self._virtual = {}

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

    def state(self, snapshot):
        if snapshot not in self._state_cache:
            record = self.catalog.snapshot(snapshot)
            self._state_cache[snapshot] = dict(
                schema=record['schema'], batches=record['batches'],
                corrections=record['corrections'])
        return self._state_cache[snapshot]

    def base_state(self, left_ancestors, right_ancestors):
        """Recursively consolidate multiple nearest common ancestors.

        The candidate order and ancestor accumulation match the historical
        schema-only baseline: maximal common ancestors in snapshot order, each
        folded into the accumulated virtual state against its recursive base.
        """
        key = (frozenset(left_ancestors), frozenset(right_ancestors))
        if key in self._virtual:
            return self._virtual[key]
        common = left_ancestors & right_ancestors
        if not common:
            raise ConflictError('no common ancestor')
        maximal = set(common)
        for candidate in common:
            maximal.difference_update(self.ancestors(candidate) - {candidate})
        candidates = sorted(maximal)
        state = copy.deepcopy(self.state(candidates[0]))
        accumulated = set(self.ancestors(candidates[0]))
        for candidate in candidates[1:]:
            ancestry = self.ancestors(candidate)
            earlier = self.base_state(accumulated, ancestry)
            record = self.state(candidate)
            schema = reconcile(earlier['schema'], state['schema'], record['schema'])
            corrections = merge_data(self.catalog, earlier, state, record)
            batches = sorted(set(state['batches']) | set(record['batches']))
            state = dict(schema=schema, batches=batches, corrections=corrections)
            accumulated.update(ancestry)
        self._virtual[key] = copy.deepcopy(state)
        return state

    def base(self, left_ancestors, right_ancestors):
        return self.base_state(left_ancestors, right_ancestors)['schema']


def merge(catalog, target, source):
    with catalog.transaction():
        left_head, right_head = catalog.head(target), catalog.head(source)
        left, right = catalog.snapshot(left_head), catalog.snapshot(right_head)
        history = Ancestry(catalog)
        base = history.base_state(history.ancestors(left_head),
                                  history.ancestors(right_head))
        schema = reconcile(base['schema'], left['schema'], right['schema'])
        corrections = merge_data(catalog, base, left, right)
        batches = sorted(set(left['batches']) | set(right['batches']))
        snapshot = catalog.publish(target, list(dict.fromkeys([left_head, right_head])),
                                   schema, batches, corrections)
    return dict(snapshot=snapshot)
