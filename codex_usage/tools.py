"""Small transcript metadata index; never retain tool arguments or result bodies."""
from bisect import bisect_right
from collections import defaultdict

from .messages import visible_text

SCHEMA = """
CREATE TABLE IF NOT EXISTS tool_events (
 path TEXT NOT NULL, byte_offset INTEGER NOT NULL, kind TEXT NOT NULL,
 call_id TEXT NOT NULL, name TEXT NOT NULL DEFAULT '',
 output_chars INTEGER NOT NULL DEFAULT 0, output_bytes INTEGER NOT NULL DEFAULT 0,
 PRIMARY KEY(path, byte_offset)
);
CREATE TABLE IF NOT EXISTS usage_markers (
 path TEXT NOT NULL, byte_offset INTEGER NOT NULL, request_id TEXT NOT NULL,
 PRIMARY KEY(path, byte_offset)
);
"""


def marker(con, path, offset, request_id):
    con.execute('INSERT OR REPLACE INTO usage_markers VALUES (?,?,?)',
                (str(path), offset, request_id))


def index_event(con, d, path, offset):
    p = d.get('payload', {})
    if not isinstance(p, dict):
        return
    if d.get('type') == 'token_usage_record' and p.get('response_id'):
        marker(con, path, offset, 'response:' + p['response_id'])
    if d.get('type') != 'response_item':
        return
    kind = p.get('type')
    if kind in ('function_call', 'custom_tool_call'):
        values = ('call', str(p.get('call_id') or ''), str(p.get('name') or 'unknown'), 0, 0)
    elif kind in ('function_call_output', 'custom_tool_call_output') or (kind == 'message' and p.get('role') == 'tool'):
        text = visible_text(p.get('output', p.get('content', '')))
        values = ('result', str(p.get('call_id') or ''), '', len(text), len(text.encode('utf-8')))
    else:
        return
    con.execute('INSERT OR REPLACE INTO tool_events VALUES (?,?,?,?,?,?,?)', (str(path), offset, *values))


def combine(summaries):
    totals = dict(calls=0, output_chars=0, output_bytes=0, missing_results=0, unavailable_requests=0)
    by_name = {}
    for summary in summaries:
        for key in totals:
            totals[key] += summary.get(key, 0)
        for tool in summary.get('tools', []):
            target = by_name.setdefault(tool['name'], dict(name=tool['name'], calls=0,
                output_chars=0, output_bytes=0, missing_results=0))
            for key in ('calls', 'output_chars', 'output_bytes', 'missing_results'):
                target[key] += tool[key]
    return dict(totals, tools=sorted(by_name.values(), key=lambda t: (-t['output_chars'], t['name'])))


def attach(con, rows):
    """Associate indexed calls with their next usage marker, once per response.

    Results use call IDs, including results emitted after the request's marker.
    Read a file's metadata once for an entire session, with no transcript reads.
    """
    by_path = defaultdict(list)
    for row in rows:
        row['tools'] = combine([])
        by_path[row['path']].append(row)
    for path, requests in by_path.items():
        markers = con.execute('SELECT m.byte_offset,m.request_id FROM usage_markers m '
                              'JOIN usage u ON u.id=m.request_id WHERE m.path=? ORDER BY m.byte_offset', (path,)).fetchall()
        offsets = [r['byte_offset'] for r in markers]
        positions = {r['byte_offset']: r['request_id'] for r in markers}
        targets = {}
        for row in requests:
            if positions.get(row['byte_offset']) == row['id']:
                targets[row['id']] = row
            else:
                row['tools']['unavailable_requests'] = 1
        if not targets:
            continue
        events = con.execute('SELECT * FROM tool_events WHERE path=? ORDER BY byte_offset', (path,)).fetchall()
        calls, results, call_positions = [], defaultdict(list), defaultdict(list)
        for e in events:
            if e['kind'] == 'call':
                calls.append(e)
                call_positions[e['call_id']].append(e['byte_offset'])
            elif e['call_id']:
                results[e['call_id']].append(e)
        seen = set()
        pieces = defaultdict(list)
        for e in calls:
            index = bisect_right(offsets, e['byte_offset'])
            if index == len(markers):
                continue  # A call without a usage record is not yet attributable.
            request_id = markers[index]['request_id']
            key = (request_id, e['call_id'] or e['byte_offset'])
            if request_id not in targets or key in seen:
                continue
            seen.add(key)
            positions_for_call = call_positions[e['call_id']]
            next_index = bisect_right(positions_for_call, e['byte_offset'])
            end = positions_for_call[next_index] if next_index < len(positions_for_call) else float('inf')
            returned = [r for r in results[e['call_id']] if e['byte_offset'] < r['byte_offset'] < end]
            tool = dict(name=e['name'], calls=1, output_chars=sum(r['output_chars'] for r in returned),
                        output_bytes=sum(r['output_bytes'] for r in returned), missing_results=int(not returned))
            pieces[request_id].append(dict(tool, tools=[tool]))
        for rid, row in targets.items():
            row['tools'] = combine(pieces[rid])
