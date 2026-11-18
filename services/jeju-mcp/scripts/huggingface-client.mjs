// Read-only research helper. Run with Node 24 --use-env-proxy when a proxy is required.
import { pathToFileURL } from 'node:url';
import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { StreamableHTTPClientTransport } from '@modelcontextprotocol/sdk/client/streamableHttp.js';
import { z } from 'zod';

export const HF_ENDPOINT = 'https://huggingface.co/mcp?gradio=none';
const ALLOWED_TOOLS = ['hub_repo_search', 'hub_repo_details', 'hf_fs'];
const MAX_RESPONSE_BYTES = 2 * 1024 * 1024;
const MAX_TOTAL_BYTES = 4 * 1024 * 1024;
export const MAX_OUTPUT_BYTES = 64 * 1024;
const MAX_PAGES = 5;
class SafeError extends Error {
  constructor(code) { super(code); this.code = code; }
}
const querySchema = z.string().trim().min(1).max(300).regex(/^[^\x00-\x1f\x7f]+$/u);

function planCommand(argv) {
  if (!Array.isArray(argv) || argv.some(arg => typeof arg !== 'string')) throw new SafeError('INVALID_COMMAND');
  const [command = 'list', value, ...extra] = argv;
  if (extra.length || (command === 'list' && value !== undefined)) throw new SafeError('INVALID_COMMAND');
  if (command === 'list') return { command };
  if (command === 'paper') {
    // Modern arXiv IDs only; paths, URLs and additional operations are not accepted.
    if (!value || value.length > 32 || !/^\d{4}\.\d{4,5}(?:v[1-9]\d*)?$/.test(value)) throw new SafeError('INVALID_PAPER_ID');
    return { command, value, call: { name: 'hf_fs', arguments: {
      operations: [{ cmd: 'cat', args: [`hf://papers/${value}/paper.md`] }],
    } } };
  }
  if (!['models', 'datasets', 'papers'].includes(command)) throw new SafeError('INVALID_COMMAND');
  const checked = querySchema.safeParse(value);
  if (!checked.success || (command === 'papers' && checked.data.startsWith('-'))) throw new SafeError('INVALID_QUERY');
  const query = checked.data;
  return { command, call: command === 'papers'
    ? { name: 'hf_fs', arguments: { operations: [{ cmd: 'search', args: ['hf://papers', query, '--limit', '5'] }] } }
    : { name: 'hub_repo_search', arguments: { query, repo_types: [command === 'models' ? 'model' : 'dataset'], limit: 5 } } };
}

function restrictedFetch(fetchImpl, token, overallSignal, timeoutMs) {
  let totalBytes = 0;
  return async (input, init = {}) => {
    const url = input instanceof Request ? input.url : String(input);
    if (url !== HF_ENDPOINT) throw new SafeError('ENDPOINT_REJECTED');
    const abort = new AbortController();
    const timer = setTimeout(() => abort.abort(), timeoutMs);
    const done = () => clearTimeout(timer);
    const signal = AbortSignal.any([overallSignal, abort.signal, ...(init.signal ? [init.signal] : [])]);
    const headers = new Headers(init.headers);
    headers.delete('authorization');
    if (token) headers.set('authorization', `Bearer ${token}`);
    try {
      const response = await fetchImpl(HF_ENDPOINT, { ...init, headers, signal, redirect: 'error' });
      if (response.status >= 300 && response.status < 400) throw new SafeError('REDIRECT_REJECTED');
      if (Number(response.headers.get('content-length')) > MAX_RESPONSE_BYTES) throw new SafeError('RESPONSE_TOO_LARGE');
      if (!response.body) { done(); return response; }
      const reader = response.body.getReader();
      let bytes = 0;
      const body = new ReadableStream({
        async pull(controller) {
          try {
            const item = await reader.read();
            if (item.done) { done(); controller.close(); return; }
            bytes += item.value.byteLength;
            totalBytes += item.value.byteLength;
            if (bytes > MAX_RESPONSE_BYTES || totalBytes > MAX_TOTAL_BYTES) {
              throw new SafeError('RESPONSE_TOO_LARGE');
            }
            controller.enqueue(item.value);
          } catch (error) {
            done(); abort.abort(); void reader.cancel().catch(() => {});
            controller.error(error instanceof SafeError ? error : new SafeError('REQUEST_FAILED'));
          }
        },
        cancel() { done(); abort.abort(); return reader.cancel().catch(() => {}); },
      });
      return new Response(body, { status: response.status, statusText: response.statusText, headers: response.headers });
    } catch (error) {
      done(); abort.abort();
      throw error instanceof SafeError ? error : new SafeError('REQUEST_FAILED');
    }
  };
}

function redact(value, token) {
  if (typeof value === 'string') {
    const text = token ? value.split(token).join('[REDACTED]') : value;
    return text.replace(/\bhf_[A-Za-z0-9]{8,}\b/g, '[REDACTED]')
      .replace(/Bearer[ \t]+[^"\\\s]+/gi, 'Bearer [REDACTED]');
  }
  if (Array.isArray(value)) return value.map(item => redact(item, token));
  if (value && typeof value === 'object') return Object.fromEntries(Object.entries(value)
    .map(([key, item]) => [redact(key, token), redact(item, token)]));
  return value;
}

function resultReport(plan, result, token) {
  if (result.isError) throw new SafeError('TOOL_REPORTED_ERROR');
  const data = result.structuredContent ?? { text: (result.content || [])
    .filter(item => item.type === 'text').map(item => item.text).join('\n') };
  // hf_fs can report an individual operation error without setting MCP isError.
  if (plan.call.name === 'hf_fs' && data.results?.some(item => item.status === 'error')) {
    throw new SafeError('TOOL_REPORTED_ERROR');
  }
  const serialized = JSON.stringify(data);
  const providerTruncated = /"(?:truncated|output_truncated)":true/.test(serialized);
  const fallback = /full paper is not available in Markdown|fallback document contains metadata and summaries/i.test(serialized);
  const report = { status: providerTruncated || fallback ? 'partial' : 'ok', command: plan.command,
    tool: plan.call.name, readOnly: true, providerTruncated, outputTruncated: false, data };
  if (plan.command === 'paper') report.paper = {
    arxivId: plan.value, documentKind: fallback ? 'summary_or_abstract_fallback'
      : providerTruncated ? 'partial_converted_text' : 'converted_text_unverified',
    fullPaperVerified: false,
    note: '변환 텍스트 한 번의 조회입니다. 초록·요약 대체본이나 잘린 결과일 수 있으며 논문 원문 전체 확인 완료를 의미하지 않습니다.',
  };
  const safe = redact(report, token);
  if (Buffer.byteLength(JSON.stringify(safe)) > MAX_OUTPUT_BYTES - 1) {
    // Preserve flags and valid JSON; the nested preview is explicitly incomplete.
    safe.data = { preview: Buffer.from(JSON.stringify(safe.data)).subarray(0, 16 * 1024).toString('utf8') };
    safe.outputTruncated = true;
    safe.status = 'partial';
  }
  return safe;
}

export async function runHuggingFace({ argv = [], token = process.env.HF_TOKEN,
  fetchImpl = globalThis.fetch, timeoutMs = 15_000 } = {}) {
  const plan = planCommand(argv);
  if (token !== undefined && (typeof token !== 'string' || /\s/u.test(token))) throw new SafeError('INVALID_TOKEN_FORMAT');
  if (!Number.isInteger(timeoutMs) || timeoutMs < 1 || timeoutMs > 30_000) throw new SafeError('INVALID_TIMEOUT');
  const overall = new AbortController();
  const deadline = setTimeout(() => overall.abort(), Math.min(90_000, timeoutMs * (MAX_PAGES + 3)));
  const client = new Client({ name: 'jeju-huggingface-readonly', version: '0.1.0' }, { capabilities: {} });
  const requestOptions = { timeout: timeoutMs, signal: overall.signal };
  const transport = new StreamableHTTPClientTransport(new URL(HF_ENDPOINT), {
    fetch: restrictedFetch(fetchImpl, token, overall.signal, timeoutMs),
    requestInit: { redirect: 'error' }, reconnectionOptions: { maxRetries: 0 },
  });
  try {
    await client.connect(transport, requestOptions);
    const available = new Set(), seenCursors = new Set();
    let cursor;
    for (let page = 0; page < MAX_PAGES; page += 1) {
      const listed = await client.listTools(cursor ? { cursor } : {}, requestOptions);
      for (const tool of listed.tools) {
        if (available.has(tool.name)) throw new SafeError('DUPLICATE_TOOL');
        available.add(tool.name);
        if (available.size > 200) throw new SafeError('TOOL_LIST_TOO_LARGE');
      }
      if (!listed.nextCursor) { cursor = undefined; break; }
      cursor = listed.nextCursor;
      if (cursor.length > 2048 || seenCursors.has(cursor)) throw new SafeError('CURSOR_LOOP');
      seenCursors.add(cursor);
      if (page === MAX_PAGES - 1) throw new SafeError('TOOL_LIST_PAGE_LIMIT');
    }
    if (!plan.call) {
      const tools = ALLOWED_TOOLS.filter(name => available.has(name));
      const missingExpectedTools = ALLOWED_TOOLS.filter(name => !available.has(name));
      if (!tools.length) throw new SafeError('REQUIRED_TOOL_MISSING');
      return { status: missingExpectedTools.length ? 'partial' : 'ok', command: 'list', readOnly: true,
        tools, missingExpectedTools, listScope: 'local_read_only_allowlist' };
    }
    if (!ALLOWED_TOOLS.includes(plan.call.name) || !available.has(plan.call.name)) throw new SafeError('REQUIRED_TOOL_MISSING');
    return resultReport(plan, await client.callTool(plan.call, undefined, requestOptions), token);
  } catch (error) {
    // Never return provider exceptions: they may contain URLs, headers or credentials.
    throw error instanceof SafeError ? error : new SafeError('REQUEST_FAILED');
  } finally {
    clearTimeout(deadline); overall.abort();
    await client.close().catch(() => {});
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  try { console.log(JSON.stringify(await runHuggingFace({ argv: process.argv.slice(2) }))); }
  catch (error) {
    console.error(JSON.stringify({ status: 'failed', code: error instanceof SafeError ? error.code : 'REQUEST_FAILED',
      reason: '명령·인증·네트워크·MCP 도구 계약을 확인하세요. 원격 오류와 자격증명은 출력하지 않습니다.' }));
    process.exitCode = 1;
  }
}
