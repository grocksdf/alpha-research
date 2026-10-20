"""Check status and numeric correlation are distinct pieces of evidence."""
from __future__ import annotations

STATES = frozenset(('PASS', 'FAIL', 'PENDING', 'MISSING'))


def check_state(value):
    value = value.strip().upper() if isinstance(value, str) else 'MISSING'
    return value if value in STATES else 'FAIL' if value == 'WARNING' else 'MISSING'


def summarize_checks(rows, required=()):
    states, invalid = {}, []
    if not isinstance(rows, list):
        rows = []
        invalid.append('invalid_check_list')
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get('name'), str) or not row['name'].strip():
            invalid.append('invalid_check_row')
            continue
        name = row['name']
        state = check_state(row.get('result'))
        if name in states:
            invalid.append('duplicate_check:' + name)
            state = 'FAIL' if 'FAIL' in (state, states[name]) else 'MISSING'
        states[name] = state
    for name in set(required) | {'SELF_CORRELATION'}:
        states.setdefault(name, 'MISSING')
    groups = {s: sorted(k for k, v in states.items() if v == s) for s in STATES}
    overall = next((s for s in ('FAIL', 'MISSING', 'PENDING') if groups[s]), 'PASS')
    if invalid and overall != 'FAIL':
        overall = 'MISSING'
    return {'status': overall, 'by_name': states, 'groups': groups, 'errors': invalid,
            'submission_checks_complete': overall == 'PASS'}
