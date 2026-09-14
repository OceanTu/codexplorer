"""Read visible conversation messages on demand; never persist message bodies.

Rollouts are transcripts, not wire request captures. Inputs are newly observed
user/tool messages; cached history and internal instructions are not rebuilt.
"""
import hashlib
import json
import re
from pathlib import Path

CHUNK_CHARS = 12000
MAX_RANGE_BYTES = 32 * 1024 * 1024
MAX_LINE_BYTES = 16 * 1024 * 1024
MAX_ITEMS = 200


def hidden_oversized_record(line):
    """Classify only confirmed envelope fields from a bounded JSON prefix.

    Never search arbitrary nested text for 'type': tool output can quote any
    record type. Unknown/incomplete headers keep the visible-content warning.
    """
    text = line[:4096].decode('utf-8', errors='replace')
    decoder = json.JSONDecoder()

    def fields(at, payload=False):
        values = {}
        try:
            while text[at].isspace():
                at += 1
            if text[at] != '{':
                return values, at
            at += 1
            while True:
                while text[at].isspace():
                    at += 1
                if text[at] == '}':
                    return values, at + 1
                key, at = decoder.raw_decode(text, at)
                while text[at].isspace():
                    at += 1
                if text[at] != ':':
                    return values, len(text)
                at += 1
                while text[at].isspace():
                    at += 1
                if key == 'payload' and not payload:
                    values[key], at = fields(at, payload=True)
                else:
                    value, at = decoder.raw_decode(text, at)
                    if key in ('type', 'role') and isinstance(value, str):
                        values[key] = value
                while text[at].isspace():
                    at += 1
                if text[at] == '}':
                    return values, at + 1
                if text[at] != ',':
                    break
                at += 1
        except (ValueError, IndexError):
            pass
        return values, len(text)

    envelope, _ = fields(0)
    kind = envelope.get('type')
    if kind is None:
        return False
    if kind not in ('response_item', 'event_msg'):
        # Includes compacted snapshots, metadata and world state. These are not
        # displayed as message bodies, regardless of their serialized size.
        return True
    payload = envelope.get('payload', {})
    subtype = payload.get('type')
    if subtype is None:
        return False
    if kind == 'event_msg':
        return subtype not in ('user_message', 'agent_message')
    if subtype == 'message':
        role = payload.get('role')
        return role is not None and role not in ('user', 'assistant', 'tool')
    return subtype not in ('function_call', 'custom_tool_call', 'function_call_output', 'custom_tool_call_output')


class MessagesUnavailable(Exception):
    def __init__(self, message, code='source_unavailable'):
        super().__init__(message)
        self.code = code


def user_text(text):
    """Remove Codex runtime envelopes only at the user-message boundary."""
    original = text
    if '## My request:' in text:
        text = text.split('## My request:', 1)[1]
    text = re.sub(r'<(recommended_plugins|environment_context|in-app-browser-context|user_instructions)\b[^>]*>.*?</\1>',
                  '', text, flags=re.S)
    text = re.sub(r'^# AGENTS\.md instructions[^\n]*\n\s*<INSTRUCTIONS>.*?</INSTRUCTIONS>',
                  '', text, flags=re.M | re.S)
    # Preserve ordinary user whitespace (including long-message chunk boundaries).
    return text.strip() if text != original else text


def visible_text(value):
    if isinstance(value, str):
        # Tool results can contain structured multimodal content as a JSON string.
        if value.lstrip().startswith(('{', '[')):
            try:
                parsed = json.loads(value)
                if isinstance(parsed, list) or isinstance(parsed, dict) and 'content' in parsed:
                    return visible_text(parsed)
            except ValueError:
                pass
        return re.sub(r'data:(?:image|audio|video)/[^;,\s]+;base64,[A-Za-z0-9+/=]+',
                      '[多媒体数据已省略]', value)
    if isinstance(value, list):
        return '\n\n'.join(filter(None, (visible_text(v) for v in value)))
    if isinstance(value, dict):
        kind = str(value.get('type', ''))
        if any(part in kind for part in ('image', 'audio', 'video')):
            return '[图片 / 音视频附件，文本视图不加载媒体]'
        if kind in ('reasoning', 'reasoning_text', 'encrypted_content'):
            return ''
        if isinstance(value.get('text'), str):
            return visible_text(value['text'])
        if 'content' in value:
            return visible_text(value['content'])
        # Unknown structured tool output is still useful as plain text.
        return visible_text(json.dumps(value, ensure_ascii=False, indent=2)) if kind else json.dumps(value, ensure_ascii=False, indent=2)
    return '' if value is None else str(value)


class RequestMessages:
    def __init__(self, con, codex_home):
        self.con = con
        home = Path(codex_home).expanduser().resolve()
        self.roots = [(home / folder).resolve() for folder in ('sessions', 'archived_sessions')]

    def path(self, value):
        path = Path(value).resolve()
        if not any(path.is_relative_to(root) for root in self.roots):
            raise MessagesUnavailable('原始日志路径不在 Codex 历史目录内。')
        return path

    def marker(self, record):
        path = self.path(record['path'])
        try:
            with path.open('rb') as f:
                offset = record['byte_offset']
                if not isinstance(offset, int) or offset < 0:
                    raise MessagesUnavailable('日志文件存在，但索引偏移无效；请刷新数据以重新扫描。', 'stale_offset')
                # A JSON substring in the middle of a line is not a valid marker.
                if offset:
                    f.seek(offset - 1)
                    if f.read(1) != b'\n':
                        raise MessagesUnavailable('日志文件存在，但索引偏移已失效；请刷新数据以重新扫描。', 'stale_offset')
                f.seek(offset)
                line = f.readline(MAX_LINE_BYTES + 1)
                if not line.endswith(b'\n') or len(line) > MAX_LINE_BYTES:
                    raise MessagesUnavailable('日志文件存在，但索引偏移处没有完整的用量记录；请刷新数据。', 'stale_offset')
                try:
                    d = json.loads(line)
                except (ValueError, UnicodeError) as e:
                    raise MessagesUnavailable('日志文件存在，但索引偏移处的 JSON 无法解析；请刷新数据。', 'stale_offset') from e
                if not isinstance(d, dict) or not isinstance(d.get('payload'), dict):
                    raise MessagesUnavailable('日志文件存在，但索引偏移处不是用量记录；请刷新数据。', 'stale_offset')
                p = d.get('payload', {})
                if record['kind'] == 'exact':
                    if d.get('type') != 'token_usage_record':
                        raise MessagesUnavailable('日志文件存在，但索引偏移处不是用量记录；请刷新数据。', 'stale_offset')
                    if p.get('response_id') != record['response_id']:
                        raise MessagesUnavailable('偏移位置的 response_id 与目标请求不匹配；请刷新数据以修复索引。', 'response_mismatch')
                    valid = True
                else:
                    from .importer import stamp
                    valid = d.get('type') == 'event_msg' and p.get('type') == 'token_count' and abs(stamp(d.get('timestamp'))-record['occurred_at']) < .001
                if not valid:
                    raise MessagesUnavailable('日志文件存在，但索引偏移处的旧版用量记录不匹配；请刷新数据。', 'stale_offset')
                return path, f.tell()
        except OSError as e:
            raise MessagesUnavailable('原始日志文件不存在或不可访问；请检查路径与读取权限，用量统计仍保留。') from e

    def neighbor(self, record, warnings, forward=False):
        comparison, order = ('>', 'ASC') if forward else ('<', 'DESC')
        candidates = self.con.execute('SELECT * FROM usage WHERE path=? AND byte_offset' + comparison +
                                      '? ORDER BY byte_offset ' + order,
                                      (record['path'], record['byte_offset']))
        for candidate in candidates:
            try:
                _, end = self.marker(candidate)
                if not forward and end > record['byte_offset']:
                    raise MessagesUnavailable('相邻记录的区间重叠。', 'stale_offset')
                return candidate, end
            except MessagesUnavailable:
                warnings.add('已跳过定位失效的相邻用量记录，按最近有效边界恢复消息；区间可能包含多次请求，请刷新数据修复索引。')
        return None, None

    def region(self, path, start, end, warnings):
        """Bounded byte-range read. Very large spans fail visibly, not silently."""
        if end - start > MAX_RANGE_BYTES:
            warnings.add('消息区间超过 32 MB，仅展示靠近本次请求的部分。')
            start = end - MAX_RANGE_BYTES
            skip_partial = True
        else:
            skip_partial = False
        events = []
        with path.open('rb') as f:
            f.seek(start)
            if skip_partial:
                while f.tell() < end:
                    line = f.readline(min(65536, end-f.tell()))
                    if not line or line.endswith(b'\n'):
                        break
            while f.tell() < end:
                offset = f.tell()
                line = f.readline(min(MAX_LINE_BYTES+1, end-offset))
                if len(line) > MAX_LINE_BYTES:
                    if not hidden_oversized_record(line):
                        warnings.add('一条对话来源的原始 JSON 超过 16 MB（可能含内嵌图片），已省略该条；其余消息继续展示。')
                    while line and not line.endswith(b'\n') and f.tell() < end:
                        line = f.readline(min(65536, end-f.tell()))
                    continue
                if not line.endswith(b'\n'):
                    warnings.add('消息区间尚未完整写入或读取期间发生改写；请刷新数据后重试。')
                    break
                # Exclude world state, reasoning, and internal instruction bodies.
                try:
                    d = json.loads(line)
                    if isinstance(d, dict):
                        events.append((offset, d))
                except ValueError:
                    warnings.add('部分消息记录无法解析，已跳过。')
        return events

    def before(self, record, warnings):
        path, marker_end = self.marker(record)
        previous, previous_end = self.neighbor(record, warnings)
        start = previous_end if previous else 0
        return path, self.region(path, start, record['byte_offset'], warnings), previous, marker_end

    def messages(self, path, events):
        primary, fallback = [], []
        for offset, d in events:
            p = d.get('payload', {})
            if not isinstance(p, dict):
                continue
            kind, role, text, name, call = p.get('type'), p.get('role'), '', '', p.get('call_id', '')
            target = primary
            if d.get('type') == 'response_item':
                if kind == 'message' and role in ('user', 'assistant', 'tool'):
                    text = visible_text(p.get('content', []))
                elif kind in ('function_call', 'custom_tool_call'):
                    role, name = 'assistant', p.get('name', '')
                    arguments = p.get('arguments', p.get('input', ''))
                    if isinstance(arguments, str):
                        try:
                            arguments = json.loads(arguments)
                        except ValueError:
                            pass
                    text = visible_text(arguments)
                elif kind in ('function_call_output', 'custom_tool_call_output'):
                    role = 'tool'
                    text = visible_text(p.get('output', ''))
                else:
                    continue
            elif d.get('type') == 'event_msg' and kind in ('user_message', 'agent_message'):
                role = 'user' if kind == 'user_message' else 'assistant'
                text = visible_text(p.get('message', ''))
                target = fallback
                kind = 'message'
            else:
                continue
            if role == 'user':
                text = user_text(text)
            if not text and not name:
                continue
            mid = hashlib.sha256((str(path)+':'+str(offset)).encode()).hexdigest()[:24]
            target.append(dict(id=mid, role=role, kind=kind, name=name, call_id=call,
                               timestamp=d.get('timestamp'), text=text, byte_offset=offset))
        # Some old versions only have event_msg, while newer ones mirror both.
        primary_roles = {p['role'] for p in primary if p['kind'] == 'message'}
        for item in fallback:
            if item['role'] not in primary_roles:
                primary.append(item)
        return sorted(primary, key=lambda m: m['byte_offset'])

    def read(self, request_id, message_id=None, offset=0):
        try:
            return self._read(request_id, message_id, offset)
        except OSError as e:
            raise MessagesUnavailable('原始日志文件不存在或不可访问；请检查路径与读取权限，用量统计仍保留。') from e

    def _read(self, request_id, message_id=None, offset=0):
        record = self.con.execute('SELECT * FROM usage WHERE id=?', (request_id,)).fetchone()
        if not record:
            raise KeyError('用量记录不存在。')
        warnings = set()
        path, events, previous, marker_end = self.before(record, warnings)
        messages = self.messages(path, events)
        outputs = [m for m in messages if m['role'] == 'assistant']
        calls = {m['call_id']: m['name'] for m in outputs if m['call_id']}
        inputs = [m for m in messages if m['role'] != 'assistant' and m['call_id'] not in calls]
        results = [m for m in messages if m['role'] == 'tool' and m['call_id'] in calls]
        # Old token_count snapshots can be emitted AFTER a tool has returned.
        # Carry that result into the next request rather than assigning it as
        # input to the request that generated the call.
        if previous and previous['kind'] != 'exact':
            pp, pe, _, _ = self.before(previous, warnings)
            previous_messages = self.messages(pp, pe)
            previous_calls = {m['call_id']: m['name'] for m in previous_messages if m['role'] == 'assistant' and m['call_id']}
            carry = [m for m in previous_messages if m['role'] == 'tool' and m['call_id'] in previous_calls]
            for m in carry:
                m['name'] = previous_calls[m['call_id']]
            inputs = carry + inputs
        following, _ = self.neighbor(record, warnings, forward=True)
        after = self.region(path, marker_end, following['byte_offset'] if following else path.stat().st_size, warnings)
        # Results belong to the tool call and are NOT part of model output tokens.
        results += [m for m in self.messages(path, after) if m['role'] == 'tool' and m['call_id'] in calls]
        for m in results:
            m['name'] = calls[m['call_id']]
        is_compaction = any(d.get('type') == 'compacted' and d.get('payload', {}).get('compaction_response_id') == record['response_id'] for _, d in after)

        context = []
        if not any(m['role'] == 'user' for m in inputs):
            first = self.con.execute('SELECT * FROM usage WHERE thread_id=? AND turn_id=? ORDER BY occurred_at,byte_offset LIMIT 1',
                                     (record['thread_id'], record['turn_id'])).fetchone()
            if first and first['id'] != request_id:
                try:
                    cp, ce, _, _ = self.before(first, warnings)
                    context = [m for m in self.messages(cp, ce) if m['role'] == 'user']
                except MessagesUnavailable as e:
                    warnings.add('本轮最初用户消息无法读取：' + str(e))

        # Do not silently present late steering/tool input as an exact wire body.
        if outputs and any(m['byte_offset'] > outputs[0]['byte_offset'] for m in inputs if m not in context):
            warnings.add('有输入在回复生成期间写入；是否被该次请求读取无法从日志确认。')
        if record['kind'] != 'exact':
            warnings.add('旧版用量采用累计差分，消息按日志区间关联，可能覆盖多次请求。')
        if is_compaction:
            warnings.add('这是上下文压缩记录，日志没有保留完整的压缩请求和响应报文。')

        groups = {'context': context, 'input': inputs, 'output': outputs, 'tool_results': results}
        if message_id:
            item = next((m for items in groups.values() for m in items if m['id'] == message_id), None)
            if item is None:
                raise KeyError('消息不属于这条用量记录，或原始日志已经变化。')
            if offset < 0 or offset > len(item['text']):
                raise ValueError('无效的消息位置。')
            return self.chunk(item, offset)
        total = sum(len(items) for items in groups.values())
        if total > MAX_ITEMS:
            warnings.add('消息数量较多，每个分组最多展示 50 条；可在原始日志中查看剩余内容。')
            groups = {k: v[:50] for k, v in groups.items()}
        return dict(request_id=request_id, association='transcript_interval', is_compaction=is_compaction,
                    note='按相邻用量记录关联可见消息。上行仅展示新增输入，不包含完整历史缓存、系统上下文或内部推理。',
                    warnings=sorted(warnings), **{k: [self.chunk(m) for m in v] for k, v in groups.items()})

    @staticmethod
    def chunk(item, offset=0):
        text = item['text']
        end = min(len(text), offset + CHUNK_CHARS)
        return dict(item, text=text[offset:end], offset=offset, total_chars=len(text),
                    next_offset=end if end < len(text) else None)
