"""Runs deploy/agent-block-reasons.sql on a scratch PostgreSQL 16+ cluster; no AWS, network or model calls.

The two Langflow tables the SQL reads (trace, span) are created with the column types, enum labels and keys of a
database that Langflow 1.13.3 migrated itself. The rows are synthetic but shaped like real Langflow 1.13.3 rows
(component messages, tool messages, model call attributes) and cover every outcome, block code, call bucket,
answer shape, answer key split and id check of the SQL. Expected counts come from an independent Python recount of
the same rows, and each synthetic run also declares the labels it is meant to produce.

Skipped when no PostgreSQL 16+ server binaries are installed in /usr/lib/postgresql/<major>/bin, when running as
root without a working `unshare --user` (PostgreSQL refuses to run as root), or when the temporary directory path
is too long for a PostgreSQL socket (set TMPDIR to a shorter directory).
"""
import datetime as dt
import json
import os
from pathlib import Path
import re
import shutil
import string
import subprocess
import tempfile
import unittest
import uuid


ROOT = Path(__file__).resolve().parents[2]
SQL_FILE = ROOT / 'deploy/agent-block-reasons.sql'
FLOW_ID = '7d0c7a52-8f3e-4f0e-9c51-3c3f4d1b2a01'
BLOCKED_MESSAGE = '요청 조건과 장소 정보를 확인하지 못해 추천을 제공할 수 없습니다.'
FINAL = 'Jeju Final Validation'
# Copied from a PostgreSQL 16 database that Langflow 1.13.3 created and migrated (alembic a6b8c0d2e4f7): the two
# tables the SQL reads, their enum types, keys and indexes. The trace.flow_id foreign key is left out (flow is not read).
SCHEMA = """
CREATE TYPE spantype AS ENUM ('chain', 'llm', 'tool', 'retriever', 'embedding', 'parser', 'agent');
CREATE TYPE spanstatus AS ENUM ('unset', 'ok', 'error');
CREATE TYPE spankind AS ENUM ('INTERNAL', 'CLIENT', 'SERVER', 'PRODUCER', 'CONSUMER');
CREATE TABLE trace (
    name character varying NOT NULL, status spanstatus NOT NULL, start_time timestamp with time zone NOT NULL,
    end_time timestamp with time zone, total_latency_ms integer NOT NULL, total_tokens integer NOT NULL,
    flow_id uuid NOT NULL, session_id character varying, id uuid NOT NULL, CONSTRAINT trace_pkey PRIMARY KEY (id));
CREATE INDEX ix_trace_flow_id ON trace USING btree (flow_id);
CREATE INDEX ix_trace_session_id ON trace USING btree (session_id);
CREATE TABLE span (
    name character varying NOT NULL, span_type spantype NOT NULL, status spanstatus NOT NULL,
    start_time timestamp with time zone NOT NULL, end_time timestamp with time zone, latency_ms integer NOT NULL,
    inputs json, outputs json, error text, span_kind spankind NOT NULL, attributes json, id uuid NOT NULL,
    trace_id uuid NOT NULL, parent_span_id uuid, CONSTRAINT span_pkey PRIMARY KEY (id),
    CONSTRAINT span_trace_id_fkey FOREIGN KEY (trace_id) REFERENCES trace (id) ON DELETE CASCADE,
    CONSTRAINT span_parent_span_id_fkey FOREIGN KEY (parent_span_id) REFERENCES span (id));
CREATE INDEX ix_span_parent_span_id ON span USING btree (parent_span_id);
CREATE INDEX ix_span_trace_id ON span USING btree (trace_id);
"""
SECTIONS = ('[0]', '[1]', '[2]', '[3]', '[4]')


# ---------------------------------------------------------------------------------------------------------------
# Synthetic rows shaped like Langflow 1.13.3 rows.

def compact(value):
    """Component message text: the Gateway components dump JSON like this (gateway_components._output)."""
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':'))


def stored(value):
    """A span's inputs/outputs/attributes as Langflow 1.13.3 stores them: json.dumps defaults (ASCII escapes)."""
    return None if value is None else json.dumps(value)


def message(text, session_id, source):
    """A lfx Message as the native tracer serializes it into span inputs/outputs (keys of a real row)."""
    properties = {'text_color': None, 'background_color': None, 'edited': False,
                  'source': {'id': source, 'display_name': source, 'source': source}, 'icon': '',
                  'allow_markdown': False, 'positive_feedback': None, 'state': 'complete', 'targets': [],
                  'usage': {}, 'build_duration': None}
    common = {'sender': 'Machine', 'sender_name': 'AI', 'files': [], 'session_id': session_id, 'context_id': '',
              'run_id': None, 'timestamp': '2026-11-25 01:00:00 UTC', 'flow_id': FLOW_ID, 'error': False,
              'edit': False, 'properties': properties, 'category': 'message', 'content_blocks': [], 'duration': None,
              'session_metadata': None}
    return {'text_key': 'text', 'data': {**common, 'text': text}, 'default_value': '', **common, 'text': text}


def prepared(session_id, *, excluded=(), requirements=({'type': 'indoor_required', 'value': True},),
             query='비 오는 날 실내 위주로 조용한 곳'):
    request = {'schemaVersion': '1.0', 'requestId': session_id, 'query': query, 'timezone': 'Asia/Seoul',
               'filters': {}, 'excludedIds': list(excluded), 'requirements': list(requirements)}
    return {'schemaVersion': '1.0', 'requestSha256': 'a' * 64, 'catalogSha256': 'b' * 64,
            'catalogVersion': 'sha256:' + 'c' * 64, 'policyVersion': 'recommendation-validation-v1',
            'evaluatedAt': '2026-11-25T01:00:00.000Z', 'candidateCount': 12, 'status': 'prepared', 'request': request}


def search(session_id, ids, *, excluded=(), evidence_ids=None):
    """A places_search result as the scoped tool returns it (compact JSON of the MCP result)."""
    items = [{'id': place, 'name': f'시험 장소 {place}', 'category': 'herit', 'kind': None, 'lat': 33.4, 'lon': 126.5,
              'zone': None, 'origin': 'raw_poi', 'web': None, 'themes': {'culture': 0.8}, 'themeRule': 'category:herit',
              'attributes': {'indoor': {'value': True, 'basis': 'inferred', 'rule': 'fixture'}}} for place in ids]
    evidence = [{'placeId': place, 'field': 'attributes.indoor', 'value': True, 'basis': 'inferred'}
                for place in (ids if evidence_ids is None else evidence_ids)]
    return compact({'schemaVersion': '1.0', 'status': 'ok',
                    'data': {'items': items, 'totalMatches': len(ids), 'returned': len(ids), 'query': '',
                             'category': None, 'theme': None, 'zone': None, 'excludedIds': list(excluded),
                             'themeThreshold': 0.5},
                    'dataMode': 'app-export', 'dataVersion': 'sha256:' + 'c' * 64, 'catalogSchemaVersion': '2.0',
                    'evidence': evidence, 'warnings': ['fixture'], 'retrievedAt': '2026-11-25T01:00:00.000Z',
                    'requestId': session_id})


def tool_message(content, number, *, error=False):
    """str(ToolMessage) of langchain-core 1.8, which Langflow 1.13.3 stores as {"output": ...} for a tool span."""
    return (f'content={content!r} name={"places_search"!r} tool_call_id={f"call_{number:04d}"!r}'
            + (" status='error'" if error else ''))


GPT = ('gpt-5.4-mini', 'ChatOpenAI gpt-5.4-mini')
GEMINI = ('gemini-3.8-flash', 'ChatGoogleGenerativeAIGateway gemini-3.8-flash')
DAY1 = dt.datetime(2026, 11, 25, 1, 0, tzinfo=dt.timezone.utc)   # 2026-11-25 KST
DAY2 = dt.datetime(2026, 11, 25, 15, 0, tzinfo=dt.timezone.utc)  # 2026-11-26 00:00 KST


class Fixture:
    def __init__(self):
        self.traces, self.spans, self.declared = [], [], {}
        self.clock = {DAY1: DAY1, DAY2: DAY2}

    def _id(self):
        return str(uuid.uuid4())

    def _span(self, trace, name, kind, start, *, status='ok', inputs=None, outputs=None, attributes=None,
              parent=None, error=None, raw_outputs=None):
        span = {'id': self._id(), 'trace_id': trace['id'], 'parent_span_id': parent, 'name': name, 'span_type': kind,
                'status': status, 'start_time': start, 'end_time': start + dt.timedelta(milliseconds=5),
                'latency_ms': 5, 'span_kind': 'INTERNAL', 'error': error,
                'inputs': stored(inputs),
                'outputs': raw_outputs if raw_outputs is not None else stored(outputs),
                'attributes': stored(attributes)}
        self.spans.append(span)
        return span

    def run(self, key, *, day=DAY1, at=None, prefix='gw-', models=(GPT, GPT), tools=(), final='accepted',
            answer='{"placeIds":["kk-1"]}', request=None, agent_error=False, redacted=False, spans=True,
            duplicate_final=None, nul_elsewhere=False, model_attributes=None, expect=None):
        """One Gateway run. final: accepted|RB|VF|other|None; tools: ('ok', content) | ('dict', value) |
        ('tool_error', content) | ('span_error',) | ('raw', stored text)."""
        if at is None:
            at = self.clock[day]
            self.clock[day] = at + dt.timedelta(seconds=7)
        session = None if prefix is None else prefix + uuid.uuid4().hex
        trace = {'id': self._id(), 'session_id': session, 'start_time': at,
                 'status': 'error' if agent_error else 'ok', 'name': f'flow - {FLOW_ID}'}
        self.traces.append(trace)
        self.declared[trace['id']] = expect or {}
        if not spans:
            return trace
        sid = session or 'no-session'
        req = prepared(sid) if request is None else request
        clock = iter(at + dt.timedelta(milliseconds=10 * step) for step in range(1, 400))
        chat_input = {'input_value': compact(req['request']) + ('\x00' if nul_elsewhere else ''), 'session_id': sid}
        hidden = MARKER if redacted else None
        self._span(trace, 'Chat Input', 'chain', next(clock), inputs=None if redacted else chat_input, raw_outputs=hidden,
                   outputs={'message': message(chat_input['input_value'], sid, 'ChatInput')})
        self._span(trace, 'Jeju Prepare Request', 'chain', next(clock), raw_outputs=hidden,
                   inputs=None if redacted else {'original_request': message('{}', sid, 'ChatInput'),
                                                 'service_token': '**********'},
                   outputs={'prepared_request': message(compact(req), sid, 'JejuPrepareRequest')})
        self._span(trace, 'Jeju Scoped Tools', 'chain', next(clock), inputs=None, raw_outputs=hidden,
                   outputs={'tools': ['places_search', 'places_get', 'constraints_check']})
        agent = self._span(trace, 'Agent', 'agent', next(clock), status='error' if agent_error else 'ok',
                           inputs=None if redacted else {'input_value': '...'}, raw_outputs=hidden,
                           outputs={'error': 'AGENT_EXECUTION_FAILED'} if agent_error else
                           {'response': message(answer if isinstance(answer, str) else '', sid, 'Agent'),
                            'logs': 'x\x00y' if nul_elsewhere else 'ok'})
        for index, (model, name) in enumerate(models):
            attributes = model_attributes if model_attributes is not None else (
                {'gen_ai.response.model': model, 'gen_ai.provider.name': 'openai', 'gen_ai.operation.name': 'chat',
                 'gen_ai.usage.input_tokens': 500 + index, 'gen_ai.usage.output_tokens': 17})
            failed = agent_error and index == len(models) - 1
            self._span(trace, name, 'llm', next(clock), parent=agent['id'], status='error' if failed else 'ok',
                       inputs=None if redacted else {'messages': [['system', '...']]}, raw_outputs=hidden,
                       outputs=None if failed else {'generations': [[{'text': ''}]]},
                       attributes=attributes, error='synthetic provider error' if failed else None)
        for number, tool in enumerate(tools, 1):
            kind = tool[0]
            if kind == 'span_error':
                self._span(trace, 'places_search', 'tool', next(clock), parent=agent['id'], status='error',
                           inputs={'maxResults': 5}, outputs=None, error='GATEWAY_TOOL_FAILED')
            elif kind == 'raw':
                self._span(trace, 'places_search', 'tool', next(clock), parent=agent['id'], inputs={'maxResults': 5},
                           raw_outputs=tool[1])
            else:
                output = tool[1] if kind == 'dict' else tool_message(tool[1], number, error=kind == 'tool_error')
                self._span(trace, 'places_search', 'tool', next(clock), parent=agent['id'],
                           inputs=None if redacted else {'maxResults': 5}, raw_outputs=hidden, outputs={'output': output})
        finals = [] if final is None or agent_error else [(final, answer)]
        if duplicate_final is not None:
            finals.insert(0, duplicate_final)
        for kind, text in finals:
            if redacted:
                outputs = {'redacted': True, 'finalStatus': 'accepted' if kind == 'accepted' else 'blocked'}
                self._span(trace, FINAL, 'chain', next(clock), raw_outputs=compact(outputs))
                continue
            if kind == 'accepted':
                published = compact({'schemaVersion': '1.0', 'requestId': sid, 'action': 'recommend',
                                     'placeIds': ['kk-1'], 'requirementClaims': [], 'confirmationNotice': False,
                                     'noticeTopics': []})
            else:
                code = {'RB': 'RECOMMENDATION_BLOCKED', 'VF': 'VALIDATION_FAILED', 'other': 'SOMETHING_NEW'}[kind]
                published = compact({'status': 'blocked', 'code': code, 'message': BLOCKED_MESSAGE})
            agent_response = message(text, sid, 'Agent')
            if text is MISSING:
                agent_response.pop('text')
                agent_response['data'].pop('text')
            inputs = {'agent_response': agent_response,
                      'prepared_request': message(compact(req), sid, 'JejuPrepareRequest'),
                      'service_token': '**********'}
            self._span(trace, FINAL, 'chain', next(clock), inputs=inputs,
                       outputs={'message': message(published, sid, 'JejuFinalValidation-v2')})
            self._span(trace, 'Chat Output', 'chain', next(clock), inputs=None,
                       outputs={'message': message(published, sid, 'ChatOutput')})
        return trace


MISSING = object()
MARKER = '{"redacted":true}'  # the span marker of deploy/redact-agent-history.py


def build_fixture():
    f = Fixture()
    s = lambda ids, **kw: ('ok', search('gw-fixture', ids, **kw))  # noqa: E731
    five = ['kk-1', 'kk-2', 'kk-3', 'kk-4', 'kk-5']
    # Accepted rule-action answers; 5, 6 and 50 ids sit on the edges of the 1_to_5, 6_to_50 and over_50 shapes.
    f.run('A1', tools=[s(five)], answer=compact({'placeIds': five}),
          expect={'result': 'accepted', 'shape': 'placeIds_1_to_5', 'keys': 'placeIds_only', 'seen': 'all',
                  'requirement': True})
    f.run('A2', models=(GPT,) * 3, tools=[s(five), s(['kk-6', 'kk-7'])],
          answer=compact({'placeIds': [f'kk-{n}' for n in range(1, 8)]}),
          expect={'result': 'accepted', 'shape': 'placeIds_6_to_50', 'seen': 'all'})
    f.run('A4', tools=[s(five), s(['kk-6'])], answer=compact({'placeIds': [f'kk-{n}' for n in range(1, 7)]}),
          expect={'result': 'accepted', 'shape': 'placeIds_6_to_50', 'seen': 'all'})
    f.run('A5', tools=[s(five)], answer=compact({'placeIds': [f'kk-{n}' for n in range(1, 51)]}),
          expect={'result': 'accepted', 'shape': 'placeIds_6_to_50', 'seen': 'some'})
    f.run('A3', tools=[s([])], answer='{"placeIds":[]}', expect={'result': 'accepted', 'shape': 'placeIds_empty'})
    # Blocked: ids the tools never returned, partly or wholly.
    f.run('B1', final='RB', tools=[s(['kk-1', 'kk-2'])], answer='{"placeIds":["invented-1"]}',
          request=prepared('gw-b1', requirements=()),
          expect={'result': 'RECOMMENDATION_BLOCKED', 'shape': 'placeIds_1_to_5', 'seen': 'none', 'requirement': False})
    f.run('B2', final='RB', tools=[s(['kk-1'])], answer='{"placeIds":["kk-1","ghost-2"]}',
          expect={'result': 'RECOMMENDATION_BLOCKED', 'shape': 'placeIds_1_to_5', 'seen': 'some'})
    # kk-9 is only an excluded id and an evidence placeId in the tool output, and an excluded id of the request.
    f.run('B3', final='RB', tools=[s(['kk-1'], excluded=['kk-9'], evidence_ids=['kk-9'])], answer='{"placeIds":["kk-9"]}',
          request=prepared('gw-b3', excluded=['kk-9']),
          expect={'result': 'RECOMMENDATION_BLOCKED', 'seen': 'none', 'excluded': True})
    # One excluded id among others is enough for with_excluded_id.
    f.run('B24', final='RB', tools=[s(['kk-1'], excluded=['kk-9'])], answer='{"placeIds":["kk-9","kk-1"]}',
          request=prepared('gw-b24', excluded=['kk-9']),
          expect={'result': 'RECOMMENDATION_BLOCKED', 'seen': 'some', 'excluded': True})
    # Final validation ran but no model call was traced: the model label is '-' and the run is in model_calls_0.
    f.run('B25', final='RB', models=(), tools=[s(['kk-1'])], answer='{"placeIds":["kk-1"]}',
          expect={'model': '-', 'result': 'RECOMMENDATION_BLOCKED', 'seen': 'all'})
    f.run('B4', final='RB', models=(GPT,), tools=(), answer='{"placeIds":["kk-1"]}',
          expect={'result': 'RECOMMENDATION_BLOCKED', 'seen': 'none', 'no_tool': True})
    f.run('B5', final='RB', tools=[s(five)], answer='{"placeIds":[]}',
          expect={'result': 'RECOMMENDATION_BLOCKED', 'shape': 'placeIds_empty', 'seen': None})
    f.run('B6', final='RB', tools=[s(five)], answer='{"ids":["kk-1"]}',
          expect={'result': 'RECOMMENDATION_BLOCKED', 'shape': 'json_without_placeIds', 'seen': None})
    # Another key next to placeIds: the MCP never reads this as a selection, so [4] keeps it in its own row.
    f.run('B7', final='RB', tools=[s(five)], answer='{"placeIds":["kk-1"],"reason":"조용한 곳"}',
          expect={'result': 'RECOMMENDATION_BLOCKED', 'shape': 'json_with_other_keys', 'keys': 'with_other_keys',
                  'seen': 'all'})
    f.run('B8', final='RB', tools=[s(five)], answer='{"placeIds":"kk-1"}',
          expect={'result': 'RECOMMENDATION_BLOCKED', 'shape': 'placeIds_not_array', 'seen': None})
    f.run('B9', final='RB', tools=[s(five)], answer=compact({'placeIds': [f'kk-{n}' for n in range(51)]}),
          expect={'result': 'RECOMMENDATION_BLOCKED', 'shape': 'placeIds_over_50', 'seen': None})
    f.run('B10', final='RB', tools=[s(five)], answer='{"placeIds":["kk-1",5]}',
          expect={'result': 'RECOMMENDATION_BLOCKED', 'shape': 'non_string_ids', 'seen': None})
    f.run('B11', final='RB', tools=[s(five)], answer='{"placeIds":[" kk-1"]}',
          expect={'result': 'RECOMMENDATION_BLOCKED', 'shape': 'bad_id_strings', 'seen': 'none'})
    # A tool output stored as an object (not a ToolMessage string) is matched with jsonb's ": " spacing.
    f.run('B12', final='RB', tools=[('dict', {'items': [{'id': 'kk-1', 'name': '시험'}]})], answer='{"placeIds":["kk-1"]}',
          expect={'result': 'RECOMMENDATION_BLOCKED', 'seen': 'all'})
    # Stored tool outputs are searched as text, never parsed: a \u0000 or lone surrogate escape elsewhere in them
    # neither aborts the SQL nor hides the ids. repr() in str(ToolMessage) would escape both, so they are written directly.
    raw_nul = json.dumps({'output': "content='" + search('gw-fixture', ['kk-1']) + "\x00' name='places_search'"})
    raw_surrogate = json.dumps({'output': "content='" + search('gw-fixture', ['kk-1']) + "\ud800' name='places_search'"})
    f.run('B13', final='RB', tools=[('raw', raw_nul), ('raw', raw_surrogate)], answer='{"placeIds":["kk-1"]}',
          expect={'result': 'RECOMMENDATION_BLOCKED', 'seen': 'all', 'no_tool': False})
    # Only ids made of letters, digits and ._:- are looked for; a Korean id never counts as seen.
    f.run('B14', final='RB', tools=[s(['osm:12.3', '장소-1'])], answer=compact({'placeIds': ['osm:12.3', '장소-1']}),
          expect={'result': 'RECOMMENDATION_BLOCKED', 'seen': 'some'})
    # An id holding \" would complete a needle in the stored text (\"id\":\"kk-1\",\"name\") without the id pattern.
    f.run('B23', final='RB', tools=[s(['kk-1'])], answer=compact({'placeIds': ['kk-1\\",\\"name']}),
          expect={'result': 'RECOMMENDATION_BLOCKED', 'shape': 'placeIds_1_to_5', 'seen': 'none'})
    f.run('B15', final='RB', tools=[('tool_error', 'GATEWAY_TOOL_FAILED'), s(['kk-1'])], answer='{"placeIds":["kk-1"]}',
          expect={'result': 'RECOMMENDATION_BLOCKED', 'seen': 'all', 'tool_error': True})
    f.run('B16', final='RB', tools=[('span_error',)], answer='{"placeIds":["kk-2"]}',
          expect={'result': 'RECOMMENDATION_BLOCKED', 'seen': 'none', 'no_tool': False, 'tool_error': True})
    # Final validation inputs that PostgreSQL cannot convert: the answer itself holds \u0000 or a lone surrogate.
    f.run('B17', final='RB', tools=[s(five)], answer='{"placeIds":["kk-1\x00"]}',
          expect={'result': 'RECOMMENDATION_BLOCKED', 'shape': 'unreadable', 'seen': None})
    f.run('B18', final='RB', tools=[s(five)], answer='{"placeIds":["kk-1\ud800"]}',
          expect={'result': 'RECOMMENDATION_BLOCKED', 'shape': 'unreadable', 'seen': None})
    f.run('B19', final='RB', tools=[s(five)], answer=None, expect={'result': 'RECOMMENDATION_BLOCKED', 'shape': 'no_answer_text'})
    f.run('B20', final='RB', tools=[s(five)], answer=5, expect={'result': 'RECOMMENDATION_BLOCKED', 'shape': 'no_answer_text'})
    f.run('B21', final='RB', tools=[s(five)], answer=MISSING, expect={'result': 'RECOMMENDATION_BLOCKED', 'shape': 'no_answer_text'})
    # The request JSON itself cannot be converted (the query held a NUL): excluded ids and requirements are unknown.
    f.run('B22', final='RB', tools=[s(five)], answer='{"placeIds":["kk-9"]}',
          request=prepared('gw-b22', excluded=['kk-9'], query='조용한\x00곳'),
          expect={'result': 'RECOMMENDATION_BLOCKED', 'seen': 'none', 'excluded': False, 'requirement': False})
    # Answers that are not JSON at all: VALIDATION_FAILED in the flow.
    f.run('V1', tools=[s(five)], answer='```json\n{"placeIds":["kk-1"]}\n```',
          final='VF', expect={'result': 'VALIDATION_FAILED', 'shape': 'not_json_code_fence'})
    f.run('V11', tools=[s(five)], answer='\n  ```\n{"placeIds":["kk-1"]}\n```', final='VF',
          expect={'result': 'VALIDATION_FAILED', 'shape': 'not_json_code_fence'})
    f.run('V2', tools=[s(five)], answer='추천 결과입니다: {"placeIds":["kk-1"]}', final='VF',
          expect={'result': 'VALIDATION_FAILED', 'shape': 'not_json_mentions_placeIds'})
    f.run('V3', models=(GPT,) * 8, tools=[s(['kk-1'])] * 7, answer='Model call limits exceeded: run limit (8/8)',
          final='VF', expect={'result': 'VALIDATION_FAILED', 'shape': 'not_json_call_limit_notice'})
    f.run('V4', tools=[s(five)], answer='죄송합니다. 조건에 맞는 장소를 찾지 못했습니다.', final='VF',
          expect={'result': 'VALIDATION_FAILED', 'shape': 'not_json'})
    f.run('V5', tools=[s(five)], answer='  \n\t ', final='VF', expect={'result': 'VALIDATION_FAILED', 'shape': 'empty_text'})
    f.run('V6', models=(GPT,), answer='', final='VF', expect={'result': 'VALIDATION_FAILED', 'shape': 'empty_text'})
    f.run('V7', tools=[s(five)], answer='["kk-1"]', final='VF',
          expect={'result': 'VALIDATION_FAILED', 'shape': 'json_not_object', 'seen': None})
    # JSON scalars never reach json_object_keys, which raises an error on a scalar.
    f.run('V12', tools=[s(five)], answer='"추천 없음"', final='VF',
          expect={'result': 'VALIDATION_FAILED', 'shape': 'json_not_object', 'seen': None})
    f.run('V13', tools=[s(five)], answer='null', final='VF',
          expect={'result': 'VALIDATION_FAILED', 'shape': 'json_not_object', 'seen': None})
    # Past 10000 opening brackets an answer is not parsed: deep nesting would stop the SQL with a stack depth error.
    f.run('V14', tools=[s(five)], answer='[' * 30000 + ']' * 30000, final='VF',
          expect={'result': 'VALIDATION_FAILED', 'shape': 'not_json', 'seen': None})
    # The bound counts brackets inside strings too: valid, shallow JSON past it reads as not JSON.
    f.run('B26', tools=[s(five)], answer=compact({'placeIds': ['kk-1'], 'note': '[' * 10001}), final='RB',
          expect={'result': 'RECOMMENDATION_BLOCKED', 'shape': 'not_json_mentions_placeIds', 'seen': None})
    f.run('V8', tools=[s(['kk-2'])], answer='{"placeIds":["kk-1"],"placeIds":["kk-2"]}', final='VF',
          expect={'result': 'VALIDATION_FAILED', 'shape': 'duplicate_keys', 'seen': 'all'})
    f.run('V9', tools=[s(five)], answer='{"placeIds":["kk-1"]} 입니다', final='VF',
          expect={'result': 'VALIDATION_FAILED', 'shape': 'not_json_mentions_placeIds'})
    f.run('V10', tools=[s(five)], answer='NaN', final='VF', expect={'result': 'VALIDATION_FAILED', 'shape': 'not_json'})
    f.run('O1', tools=[s(['kk-1'])], final='other', expect={'result': 'blocked_other', 'seen': 'all'})
    # Runs whose text the redaction tool replaced; spans keep only fixed markers.
    f.run('R1', tools=[s(five)], final='RB', redacted=True, expect={'result': 'blocked_redacted', 'shape': 'redacted'})
    f.run('R2', tools=[s(five)], redacted=True, expect={'result': 'accepted', 'shape': 'redacted'})
    # Runs without final validation.
    f.run('E1', tools=[s(five)], final=None, agent_error=True, expect={'outcome': 'agent_error'})
    f.run('E2', models=(), final=None, expect={'outcome': 'no_final'})
    f.run('E3', spans=False, expect={'outcome': 'no_final'})
    # Two final validation spans in one run: counted once, from the later span.
    f.run('D1', tools=[s(['kk-1'])], final='RB', duplicate_final=('RB', 'not json'),
          expect={'result': 'RECOMMENDATION_BLOCKED', 'shape': 'placeIds_1_to_5', 'seen': 'all'})
    # \u0000 in spans the SQL does not parse (Chat Input, Agent) does not abort it.
    f.run('N1', tools=[s(five)], nul_elsewhere=True, expect={'result': 'accepted', 'seen': 'all'})
    # Model labels: kind-name fallback, a name that fails the pattern, and two models in one run.
    f.run('M1', models=(('', 'ChatOpenAI'),), model_attributes={}, tools=[s(five)], expect={'model': '(ChatOpenAI)'})
    f.run('M2', models=(('gpt\nmini', 'ChatOpenAI'),), tools=[s(five)], final='RB', answer='{"placeIds":[]}',
          expect={'model': '(other)', 'result': 'RECOMMENDATION_BLOCKED'})
    f.run('M3', models=(GPT, GEMINI), tools=[s(five)], expect={'model': 'gemini-3.8-flash+gpt-5.4-mini'})
    # Full v2 answers of the Vertex build on the next KST day.
    full = lambda ids: compact({'schemaVersion': '1.0', 'requestId': 'gw-x', 'action': 'recommend',  # noqa: E731
                                'placeIds': ids, 'requirementClaims': [], 'confirmationNotice': False, 'noticeTopics': []})
    f.run('G1', day=DAY2, models=(GEMINI,) * 4, tools=[s(five)] * 3, answer=full(['kk-1']),
          expect={'model': 'gemini-3.8-flash', 'shape': 'json_with_other_keys', 'keys': 'with_other_keys', 'seen': 'all'})
    f.run('G2', day=DAY2, models=(GEMINI,) * 8, tools=[s(five)] * 7, answer=full(['made-up']), final='RB',
          expect={'model': 'gemini-3.8-flash', 'shape': 'json_with_other_keys', 'keys': 'with_other_keys', 'seen': 'none'})
    # KST day boundary: one microsecond before and exactly at 15:00 UTC.
    f.run('K1', at=DAY2 - dt.timedelta(microseconds=1), tools=[s(five)], expect={'kst_day': '2026-11-25'})
    f.run('K2', at=DAY2 + dt.timedelta(hours=1), tools=[s(five)], expect={'kst_day': '2026-11-26'})
    f.run('K3', at=DAY2, tools=[s(five)], final='RB', answer='{"placeIds":["zz"]}', expect={'kst_day': '2026-11-26'})
    # Sessions that are not Gateway runs: never counted.
    for prefix in ('ot-', 'xgw-', 'GW-', 'gw_', None):
        f.run('X', prefix=prefix, tools=[s(five)], final='RB', answer='{"placeIds":["zz"]}', expect={'counted': False})
    return f


# ---------------------------------------------------------------------------------------------------------------
# Independent recount: Python's json module, with PostgreSQL's jsonb input rules (no \u0000, no lone surrogate).

INVALID = object()
MODEL_CHARACTERS = set(string.ascii_letters + string.digits + ' ._:+()/-')
ID_CHARACTERS = set(string.ascii_letters + string.digits + '._:-')
SPACE = ' \t\n\r\f\v'


def strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for item in value:
            yield from strings(item)
    elif isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from strings(item)


def jsonb(text):
    """The value PostgreSQL's jsonb input would accept for this text, or INVALID."""
    if text is None:
        return INVALID
    def constant(_):
        raise ValueError('constant')
    try:
        value = json.loads(text, parse_constant=constant)
    except (ValueError, RecursionError):
        return INVALID
    if any('\x00' in item or any(0xD800 <= ord(ch) <= 0xDFFF for ch in item) for item in strings(value)):
        return INVALID
    return value


def answer_jsonb(text):
    """jsonb() of an agent answer: the SQL does not parse an answer with more than 10000 opening brackets."""
    if text is not None and text.count('[') + text.count('{') > 10000:
        return INVALID
    return jsonb(text)


def as_text(value):
    """What ->> returns for a JSON value: strings as themselves, null as SQL NULL, others as JSON text."""
    if value is None or isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def field(value, *path):
    for key in path:
        if not isinstance(value, dict) or key not in value:
            return None
        value = value[key]
    return value


def recount(traces, spans):
    by_trace = {}
    for span in spans:
        by_trace.setdefault(span['trace_id'], []).append(span)
    rows = {section: {} for section in SECTIONS[1:]}
    labels = {}
    for trace in traces:
        if not (trace['session_id'] or '').startswith('gw-'):
            continue
        own = by_trace.get(trace['id'], [])
        top = [span for span in own if span['parent_span_id'] is None]
        finals = [span for span in top if span['name'] == FINAL]
        # Recorded model label.
        names = set()
        for span in own:
            if span['span_type'] == 'llm':
                attributes = json.loads(span['attributes']) if span['attributes'] else {}
                recorded = as_text(field(attributes, 'gen_ai.response.model'))
                names.add(recorded if recorded else f"({span['name']})")
        model = '+'.join(sorted(names)) if names else '-'
        if names and not (1 <= len(model) <= 120 and set(model) <= MODEL_CHARACTERS):
            model = '(other)'
        # Outcome and block code from the final validation component span(s).
        def published(span):
            return as_text(field(json.loads(span['outputs']), 'message', 'text')) if span['outputs'] else None
        has_final = any((span['outputs'] or '').startswith('{"redacted":true,"finalStatus":"') or published(span) is not None
                        for span in finals)
        blocked = any(span['outputs'] == '{"redacted":true,"finalStatus":"blocked"}'
                      or (published(span) or '').startswith('{"status":"blocked"') for span in finals)
        if has_final:
            outcome = 'blocked' if blocked else 'accepted'
        else:
            outcome = 'agent_error' if any(span['status'] == 'error' for span in top) else 'no_final'
        if any(span['outputs'] == '{"redacted":true,"finalStatus":"blocked"}' for span in finals):
            code = 'blocked_redacted'
        elif any((published(span) or '').startswith('{"status":"blocked","code":"RECOMMENDATION_BLOCKED",') for span in finals):
            code = 'RECOMMENDATION_BLOCKED'
        elif any((published(span) or '').startswith('{"status":"blocked","code":"VALIDATION_FAILED",') for span in finals):
            code = 'VALIDATION_FAILED'
        else:
            code = 'blocked_other'
        result = code if outcome == 'blocked' else outcome
        kst_day = (trace['start_time'] + dt.timedelta(hours=9)).date().isoformat()
        label = {'counted': True, 'kst_day': kst_day, 'model': model, 'outcome': outcome, 'result': result}
        labels[trace['id']] = label
        one = rows['[1]'].setdefault((kst_day, model), [0] * 9)
        one[0] += 1
        for index, flag in enumerate((outcome == 'accepted', outcome == 'blocked', result == 'RECOMMENDATION_BLOCKED',
                                      result == 'VALIDATION_FAILED', result == 'blocked_redacted',
                                      result == 'blocked_other', outcome == 'agent_error', outcome == 'no_final'), 1):
            one[index] += flag
        if outcome not in ('accepted', 'blocked'):
            continue
        # [2] calls.
        tools = [span for span in own if span['span_type'] == 'tool']
        model_calls = sum(span['span_type'] == 'llm' for span in own)
        tool_error = any(span['status'] == 'error' or " status='error'" in (span['outputs'] or '') for span in tools)
        label.update(tool_error=tool_error)
        two = rows['[2]'].setdefault((kst_day, model, result), [0] * 11)
        two[0] += 1
        two[1 + (0 if model_calls == 0 else 1 if model_calls == 1 else 2 if model_calls == 2 else 3 if model_calls < 8 else 4)] += 1
        two[6 + min(len(tools), 3)] += 1
        two[10] += tool_error
        # [3] answer shape from the latest final validation span.
        span = sorted(finals, key=lambda item: (-item['start_time'].timestamp(), item['id']))[0]
        redacted = (span['outputs'] or '').startswith('{"redacted":true')
        doc = jsonb(span['inputs'])
        answer = field(doc, 'agent_response', 'text') if doc is not INVALID else None
        answer = answer if isinstance(answer, str) else None
        request_text = field(doc, 'prepared_request', 'text') if doc is not INVALID else None
        request_text = request_text if isinstance(request_text, str) else None
        value = answer_jsonb(answer)
        if redacted:
            shape = 'redacted'
        elif doc is INVALID:
            shape = 'unreadable'
        elif answer is None:
            shape = 'no_answer_text'
        elif all(ch in SPACE for ch in answer):
            shape = 'empty_text'
        elif value is INVALID:
            shape = ('not_json_code_fence' if answer.lstrip(SPACE).startswith('```')
                     else 'not_json_call_limit_notice' if 'Model call limits exceeded' in answer
                     else 'not_json_mentions_placeIds' if 'placeIds' in answer else 'not_json')
        elif not isinstance(value, dict):
            shape = 'json_not_object'
        elif len(json.loads(answer, object_pairs_hook=lambda pairs: pairs)) != len(value):
            shape = 'duplicate_keys'
        elif 'placeIds' not in value:
            shape = 'json_without_placeIds'
        elif len(value) > 1:
            shape = 'json_with_other_keys'
        elif not isinstance(value['placeIds'], list):
            shape = 'placeIds_not_array'
        else:
            ids = value['placeIds']
            shape = ('placeIds_empty' if not ids else 'placeIds_over_50' if len(ids) > 50
                     else 'non_string_ids' if any(not isinstance(item, str) for item in ids)
                     else 'bad_id_strings' if any(item == '' or len(item) > 160 or item[0] in SPACE or item[-1] in SPACE
                                                  for item in ids)
                     else 'placeIds_1_to_5' if len(ids) <= 5 else 'placeIds_6_to_50')
        label['shape'] = shape
        rows['[3]'][(kst_day, model, result, shape)] = rows['[3]'].get((kst_day, model, result, shape), 0) + 1
        # [4] whether the chosen ids appeared in this run's tool outputs.
        chosen = field(value, 'placeIds') if value is not INVALID else None
        if not (isinstance(chosen, list) and 1 <= len(chosen) <= 50 and all(isinstance(item, str) for item in chosen)):
            label['seen'] = None
            continue
        request = jsonb(request_text)
        request = field(request, 'request') if request is not INVALID else None
        excluded_ids = field(request, 'excludedIds')
        requirements = field(request, 'requirements')
        keys = 'placeIds_only' if len(value) == 1 else 'with_other_keys'
        # The stored text of each tool span: a ToolMessage string inside JSON escapes its quotes, an object does not.
        outputs = [span['outputs'] for span in tools if span['outputs'] is not None]
        needles = ('\\"id\\":\\"{}\\"', '\\"id\\": \\"{}\\"', '"id":"{}"', '"id": "{}"')
        distinct = set(chosen)
        seen = sum(1 <= len(item) <= 160 and set(item) <= ID_CHARACTERS
                   and any(needle.format(item) in text for needle in needles for text in outputs) for item in distinct)
        label.update(keys=keys, seen='all' if seen == len(distinct) else 'some' if seen else 'none', no_tool=not tools,
                     excluded=isinstance(excluded_ids, list) and any(item in excluded_ids for item in distinct),
                     requirement=isinstance(requirements, list) and len(requirements) > 0)
        four = rows['[4]'].setdefault((kst_day, model, result, keys), [0] * 7)
        for index, flag in enumerate((True, seen == len(distinct), 0 < seen < len(distinct), seen == 0,
                                      seen == 0 and not tools, label['excluded'], label['requirement'])):
            four[index] += flag
    table = {'[1]': sorted([*key, *map(str, values)] for key, values in rows['[1]'].items()),
             '[2]': sorted([*key, *map(str, values)] for key, values in rows['[2]'].items()),
             '[3]': sorted([*key, str(value)] for key, value in rows['[3]'].items()),
             '[4]': sorted([*key, *map(str, values)] for key, values in rows['[4]'].items())}
    return table, labels


# ---------------------------------------------------------------------------------------------------------------
# Scratch cluster.

def server_binaries():
    found = []
    for directory in Path('/usr/lib/postgresql').glob('*/bin'):
        major = directory.parent.name
        if major.isdigit() and int(major) >= 16 and all((directory / name).is_file()
                                                        for name in ('initdb', 'pg_ctl', 'postgres', 'psql')):
            found.append((int(major), directory))
    return max(found)[1] if found else None


def literal(value):
    if value is None:
        return 'NULL'
    if isinstance(value, dt.datetime):
        value = value.isoformat()
    return "'" + str(value).replace("'", "''") + "'"


def insert_script(traces, spans):
    lines = []
    for trace in traces:
        lines.append('INSERT INTO trace (name, status, start_time, end_time, total_latency_ms, total_tokens, flow_id, '
                     f"session_id, id) VALUES ({literal(trace['name'])}, {literal(trace['status'])}, "
                     f"{literal(trace['start_time'])}, {literal(trace['start_time'] + dt.timedelta(seconds=2))}, 2000, 0, "
                     f"{literal(FLOW_ID)}, {literal(trace['session_id'])}, {literal(trace['id'])});")
    for span in spans:
        lines.append('INSERT INTO span (name, span_type, status, start_time, end_time, latency_ms, inputs, outputs, '
                     'error, span_kind, attributes, id, trace_id, parent_span_id) VALUES ('
                     + ', '.join(literal(span[key]) for key in ('name', 'span_type', 'status', 'start_time', 'end_time',
                                                                 'latency_ms', 'inputs', 'outputs', 'error', 'span_kind',
                                                                 'attributes', 'id', 'trace_id', 'parent_span_id'))
                     + ');')
    return '\n'.join(lines) + '\n'


def sections(output):
    found, current = {}, None
    for line in output.splitlines():
        match = re.match(r'^(\[[0-4]\]) ', line)
        if match:
            current = match.group(1)
            found[current] = []
        elif line:
            found[current].append(line.split('|'))
    return found


class BlockReasonsSqlRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        binaries = server_binaries()
        if binaries is None:
            raise unittest.SkipTest('no PostgreSQL 16+ server binaries in /usr/lib/postgresql/*/bin')
        cls.bin = binaries
        cls.wrap = []
        if os.geteuid() == 0:
            if shutil.which('unshare') is None:
                raise unittest.SkipTest('PostgreSQL refuses root and unshare is not installed')
            cls.wrap = ['unshare', '--user', '--map-user=1000', '--map-group=1000']
        cls.base = tempfile.mkdtemp(prefix='br-')
        if len(os.path.join(cls.base, '.s.PGSQL.5432').encode()) > 100:
            shutil.rmtree(cls.base)
            raise unittest.SkipTest('temporary directory path too long for a PostgreSQL socket; set a shorter TMPDIR')
        data = os.path.join(cls.base, 'data')
        initialized = subprocess.run(cls.wrap + [str(binaries / 'initdb'), '-D', data, '-U', 'postgres', '-A', 'trust',
                                                 '-E', 'UTF8', '--locale=C', '-N'], capture_output=True, text=True)
        if initialized.returncode != 0:
            shutil.rmtree(cls.base)
            raise unittest.SkipTest('initdb failed: ' + initialized.stderr[-300:])
        with open(os.path.join(data, 'postgresql.conf'), 'a', encoding='utf-8') as conf:
            # A non-UTC server default proves that the file's own SET TIME ZONE decides kst_day.
            conf.write(f"listen_addresses = ''\nunix_socket_directories = '{cls.base}'\nport = 5432\nfsync = off\n"
                       "timezone = 'Asia/Seoul'\n")
        started = subprocess.run(cls.wrap + [str(binaries / 'pg_ctl'), '-D', data, '-l', os.path.join(cls.base, 'log'),
                                             '-w', '-t', '60', 'start'], capture_output=True, text=True)
        if started.returncode != 0:
            shutil.rmtree(cls.base)
            raise unittest.SkipTest('PostgreSQL did not start: ' + started.stderr[-300:])
        cls.data = data
        try:
            cls.fixture = build_fixture()
            cls.psql('postgres', 'postgres', input='CREATE ROLE langflow_app LOGIN;\n'
                                                   'CREATE DATABASE langflow OWNER langflow_app;\n')
            cls.psql('langflow_app', 'langflow', input=SCHEMA + insert_script(cls.fixture.traces, cls.fixture.spans))
        except BaseException:
            cls.tearDownClass()
            raise

    @classmethod
    def tearDownClass(cls):
        subprocess.run(cls.wrap + [str(cls.bin / 'pg_ctl'), '-D', cls.data, '-m', 'immediate', '-w', 'stop'],
                       capture_output=True, text=True)
        shutil.rmtree(cls.base, ignore_errors=True)

    @classmethod
    def psql(cls, user, database, *, input, extra=(), env=None, check=True):
        clean = {key: value for key, value in os.environ.items() if not key.startswith('PG')}
        result = subprocess.run([str(cls.bin / 'psql'), '-X', '-h', cls.base, '-p', '5432', '-U', user, '-d', database,
                                 '-v', 'ON_ERROR_STOP=1', *extra], input=input, capture_output=True, text=True,
                                env={**clean, **(env or {})})
        if check and result.returncode != 0:
            raise AssertionError(result.stderr[-2000:])
        return result

    def run_sql(self, env=None):
        return self.psql('langflow_app', 'langflow', input=SQL_FILE.read_text(encoding='utf-8'),
                         extra=('-q', '-A', '-t'), env=env).stdout

    def test_declared_labels_match_the_recount(self):
        _, labels = recount(self.fixture.traces, self.fixture.spans)
        for trace in self.fixture.traces:
            declared = self.fixture.declared[trace['id']]
            label = labels.get(trace['id'], {'counted': False})
            for key, value in declared.items():
                self.assertEqual(label.get(key), value, (key, declared, label))
        # The fixture reaches every label of the SQL.
        shapes = {label.get('shape') for label in labels.values()}
        self.assertEqual(shapes - {None}, {
            'redacted', 'unreadable', 'no_answer_text', 'empty_text', 'not_json_code_fence', 'not_json_call_limit_notice',
            'not_json_mentions_placeIds', 'not_json', 'json_not_object', 'duplicate_keys', 'json_without_placeIds',
            'json_with_other_keys', 'placeIds_not_array', 'placeIds_empty', 'placeIds_over_50', 'non_string_ids',
            'bad_id_strings', 'placeIds_1_to_5', 'placeIds_6_to_50'})
        self.assertEqual({label['result'] for label in labels.values()}, {
            'accepted', 'RECOMMENDATION_BLOCKED', 'VALIDATION_FAILED', 'blocked_redacted', 'blocked_other',
            'agent_error', 'no_final'})
        self.assertEqual({label.get('seen') for label in labels.values()}, {None, 'all', 'some', 'none'})
        self.assertEqual({label.get('keys') for label in labels.values()}, {None, 'placeIds_only', 'with_other_keys'})
        # Every count column of [1], [2] and [4] is reached by at least one run (the column after runs onward).
        expected, _ = recount(self.fixture.traces, self.fixture.spans)
        for section, runs in (('[1]', 2), ('[2]', 3), ('[4]', 4)):
            totals = [sum(int(row[index]) for row in expected[section]) for index in range(runs + 1, len(expected[section][0]))]
            self.assertTrue(all(totals), (section, totals))

    def test_sql_output_equals_the_independent_recount(self):
        expected, _ = recount(self.fixture.traces, self.fixture.spans)
        found = sections(self.run_sql())
        self.assertEqual(list(found), list(SECTIONS))
        self.assertEqual(found['[0]'], [['UTC']])
        for section in SECTIONS[1:]:
            self.assertEqual(sorted(found[section]), expected[section], section)
        # Every row of [2], [3] and [4] belongs to an accepted or blocked run; [3] covers each such run once.
        accepted_blocked = sum(int(row[3]) + int(row[4]) for row in found['[1]'])
        self.assertEqual(sum(int(row[3]) for row in found['[2]']), accepted_blocked)
        self.assertEqual(sum(int(row[4]) for row in found['[3]']), accepted_blocked)
        # Outputs hold fixed labels, dates and integers only: never a session id, place id or answer text.
        allowed = re.compile(r'\d+|\d{4}-\d{2}-\d{2}|[A-Za-z0-9 ._:+()/-]{1,120}')
        for section in SECTIONS[1:]:
            for row in found[section]:
                for value in row:
                    self.assertIsNotNone(allowed.fullmatch(value), value)
                    self.assertNotIn('gw-', value)
                    self.assertNotIn('kk-', value)

    def test_session_time_zone_does_not_change_the_output(self):
        default = self.run_sql()
        for zone in ('Asia/Seoul', 'America/Los_Angeles', 'Pacific/Chatham'):
            self.assertEqual(self.run_sql(env={'PGTZ': zone}), default, zone)

    def test_aligned_output_of_the_documented_command_runs(self):
        result = self.psql('langflow_app', 'langflow', input=SQL_FILE.read_text(encoding='utf-8'), extra=('-P', 'pager=off'))
        for label in SECTIONS:
            self.assertEqual(result.stdout.count('\n' + label + ' ') + result.stdout.startswith(label + ' '), 1, label)

    def test_session_stays_read_only_after_the_file(self):
        before = self.psql('langflow_app', 'langflow', input='SELECT count(*) FROM span;', extra=('-A', '-t')).stdout
        refused = self.psql('langflow_app', 'langflow', check=False, extra=('-q', '-A', '-t'),
                            input=SQL_FILE.read_text(encoding='utf-8') + "SHOW statement_timeout;\nDELETE FROM span;\n")
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn('read-only transaction', refused.stderr)
        self.assertTrue(refused.stdout.rstrip().endswith('15s'), refused.stdout[-200:])
        after = self.psql('langflow_app', 'langflow', input='SELECT count(*) FROM span;', extra=('-A', '-t')).stdout
        self.assertEqual(before, after)


if __name__ == '__main__':
    unittest.main()
