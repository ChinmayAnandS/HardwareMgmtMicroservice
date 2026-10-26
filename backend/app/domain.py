"""Pure inventory invariants, shared by all transactional commands."""
from copy import deepcopy


class Conflict(Exception):
    pass


def require(condition, message):
    if not condition:
        raise Conflict(message)


def validate(hardware, allocations):
    total, out, reserved = (hardware[k] for k in ('total', 'out', 'reserved'))
    require(total <= 1000000, 'Total exceeds supported batch size')
    require(len(allocations) <= 200, 'Maximum 200 project allocations per hardware pool')
    require(0 <= out <= reserved <= total, 'Stock must satisfy 0 <= out <= reserved <= total')
    require(hardware['status'] == 'active' or out == 0, 'Return all checkouts before changing status')
    require(hardware['status'] != 'retired' or reserved == 0, 'Release allocations before retirement')
    require(sum(a['quantity'] for a in allocations) == reserved, 'Allocation totals disagree')
    require(sum(a['out'] for a in allocations) == out, 'Checkout totals disagree')
    for a in allocations:
        require(0 <= a['out'] <= a['quantity'], 'Allocation cannot be below outstanding quantity')


def transition(hardware, allocations, command, data, checkout=None):
    """Return new state without modifying caller values; caller persists atomically."""
    h, aa, c = deepcopy(hardware), deepcopy(allocations), deepcopy(checkout)
    if command == 'edit':
        for k in ('name', 'category', 'description', 'asset_tag', 'serial', 'status'):
            if k in data:
                h[k] = data[k]
        require(hardware['status'] != 'retired', 'Retired hardware is immutable')
    elif command == 'adjust':
        require(h['status'] != 'retired', 'Retired hardware is immutable')
        h['total'] += data['delta']
    elif command == 'retire':
        require(h['status'] != 'retired', 'Already retired')
        h['status'] = 'retired'
    else:
        a = next((x for x in aa if x['project_id'] == data['project_id']), None)
        if command == 'allocate':
            require(h['status'] == 'active' or data['quantity'] == 0, 'Only active stock can be allocated')
            if a is None:
                a = {'_id': h['_id'] + ':' + data['project_id'], 'hardware_id': h['_id'],
                     'project_id': data['project_id'], 'quantity': 0, 'out': 0, 'version': 0}
                aa.append(a)
            h['reserved'] += data['quantity'] - a['quantity']
            a['quantity'] = data['quantity']
            a['version'] += 1
        else:
            require(a is not None, 'Project has no allocation')
            if command == 'checkout':
                require(h['status'] == 'active', 'Hardware is not active')
                require(data['quantity'] <= a['quantity'] - a['out'], 'Insufficient project stock')
                diff = data['quantity']
                c = {k: data[k] for k in ('project_id', 'hardware_id', 'user_id', 'quantity')}
                c.update(returned=0, version=1)
            else:
                require(c is not None, 'Checkout missing')
                returned = c['returned'] + data['quantity'] if command == 'return' else data['returned']
                require(0 <= returned <= c['quantity'], 'Return exceeds outstanding quantity')
                diff = c['returned'] - returned
                require(diff <= 0 or h['status'] == 'active', 'Cannot reopen inactive stock')
                c['returned'] = returned
                c['version'] += 1
            a['out'] += diff
            a['version'] += 1
            h['out'] += diff
    h['version'] += 1
    h['name_key'] = h['name'].casefold()
    h['asset_key'] = h['asset_tag'].casefold()
    validate(h, aa)
    return h, aa, c
