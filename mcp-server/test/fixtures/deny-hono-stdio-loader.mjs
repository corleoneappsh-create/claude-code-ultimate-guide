// ESM import guard for a stdio-only regression test. Not shipped in the MCP.
function forbidden(specifier) {
  const s = String(specifier).replaceAll('\\', '/')
  return s === '@hono/node-server'
    || s.startsWith('@hono/node-server/')
    || s.includes('/node_modules/@hono/node-server/')
    || s === '@modelcontextprotocol/sdk/server/streamableHttp.js'
    || /\/server\/streamableHttp\.js$/.test(s)
}
export function resolve(specifier, context, nextResolve) {
  if (forbidden(specifier)) throw new Error('STDIO_HTTP_IMPORT_FORBIDDEN: ' + specifier)
  return nextResolve(specifier, context)
}
