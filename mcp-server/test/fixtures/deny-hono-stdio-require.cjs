// CJS guard and deterministic synthetic Anthropic response for the isolated MCP child.
const Module = require('node:module')
const originalLoad = Module._load
Module._load = function (request, parent, isMain) {
  const s = String(request).replaceAll('\\', '/')
  if (s === '@hono/node-server'
    || s.startsWith('@hono/node-server/')
    || s.includes('/node_modules/@hono/node-server/')
    || s === '@modelcontextprotocol/sdk/server/streamableHttp.js'
    || /\/server\/streamableHttp\.js$/.test(s)) {
    throw new Error('STDIO_HTTP_IMPORT_FORBIDDEN: ' + request)
  }
  return originalLoad.apply(this, arguments)
}
const synthetic = '# Synthetic stdio test documentation\n'
  + 'Source: https://code.claude.com/docs/en/synthetic-stdio-test\n'
  + '\nMCP_STDIO_DOCS_CANARY\n'
  + 'Local-only integration fixture text.\n'.repeat(18000)
globalThis.fetch = async (url) => {
  if (String(url) === 'https://code.claude.com/docs/llms-full.txt') {
    return new Response(synthetic, { status: 200 })
  }
  throw new Error('STDIO_TEST_NETWORK_DENIED: ' + String(url))
}
