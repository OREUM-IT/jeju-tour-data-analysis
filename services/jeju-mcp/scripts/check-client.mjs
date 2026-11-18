// Real MCP protocol check; does not call any LLM or modify a course.
import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { StreamableHTTPClientTransport } from '@modelcontextprotocol/sdk/client/streamableHttp.js';

const expected = ['constraints.check', 'places.get', 'places.search'];
let client;
try {
  const endpoint = new URL(process.env.MCP_SERVER_URL || 'http://127.0.0.1:8788/mcp');
  const local = ['127.0.0.1', 'localhost', '[::1]'].includes(endpoint.hostname);
  if ((!local && endpoint.protocol !== 'https:') || !['http:', 'https:'].includes(endpoint.protocol)
    || endpoint.username || endpoint.password || endpoint.search || endpoint.hash || endpoint.pathname !== '/mcp') {
    throw new Error('Invalid MCP endpoint.');
  }
  const token = process.env.MCP_SERVICE_TOKEN;
  if (!token || token.length < 32) throw new Error('Missing service credential.');
  const headers = { Authorization: `Bearer ${token}` };
  const cfId = process.env.CF_ACCESS_CLIENT_ID;
  const cfSecret = process.env.CF_ACCESS_CLIENT_SECRET;
  if (Boolean(cfId) !== Boolean(cfSecret)) throw new Error('Both Cloudflare service-token fields are required.');
  if (cfId) {
    headers['CF-Access-Client-Id'] = cfId;
    headers['CF-Access-Client-Secret'] = cfSecret;
  }
  client = new Client({ name: 'jeju-tour-readonly-check', version: '0.1.0' }, { capabilities: {} });
  const transport = new StreamableHTTPClientTransport(endpoint, {
    requestInit: { headers, redirect: 'error' },
  });
  await client.connect(transport, { timeout: 10_000 });
  const listed = await client.listTools({}, { timeout: 10_000 });
  if (JSON.stringify(listed.tools.map(tool => tool.name).sort()) !== JSON.stringify(expected)) {
    throw new Error('Unexpected tool contract.');
  }
  const search = await client.callTool({ name: 'places.search', arguments: { query: '', maxResults: 2 } }, undefined, { timeout: 10_000 });
  const result = search.structuredContent;
  if (search.isError || !result || !Array.isArray(result.data?.items) || !result.data.items.length) {
    throw new Error('Search contract failed or catalog is empty.');
  }
  const ids = result.data.items.map(place => place.id);
  const get = await client.callTool({ name: 'places.get', arguments: { ids } }, undefined, { timeout: 10_000 });
  const check = await client.callTool({ name: 'constraints.check', arguments: {
    ids, requirements: { wheelchairRequired: true },
  } }, undefined, { timeout: 10_000 });
  if (get.isError || check.isError || !get.structuredContent || !check.structuredContent) {
    throw new Error('Tool result contract failed.');
  }
  console.log(JSON.stringify({ status: 'passed', check: 'mcp-initialize-list-and-three-tools',
    tools: expected, dataMode: result.dataMode, dataVersion: result.dataVersion,
    checkedPlaceCount: ids.length, constraintsStatus: check.structuredContent.status,
    productionReady: false }, null, 2));
} catch (error) {
  console.error(JSON.stringify({ status: 'failed',
    reason: 'Check the endpoint, authentication, catalog and SDK contract. No credential values were printed.',
    code: typeof error.code === 'number' ? error.code : null }));
  process.exitCode = 1;
} finally {
  if (client) await client.close().catch(() => {});
}
