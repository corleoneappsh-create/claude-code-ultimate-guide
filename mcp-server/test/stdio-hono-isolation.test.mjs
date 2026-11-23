import assert from 'node:assert/strict'
import { spawnSync } from 'node:child_process'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import test from 'node:test'
import { Client } from '@modelcontextprotocol/sdk/client/index.js'
import { StdioClientTransport } from '@modelcontextprotocol/sdk/client/stdio.js'

const packageRoot = resolve(fileURLToPath(new URL('.', import.meta.url)), '..')
const repositoryRoot = resolve(packageRoot, '..')
const esmGuard = resolve(packageRoot, 'test/fixtures/deny-hono-stdio-loader.mjs')
const cjsGuard = resolve(packageRoot, 'test/fixtures/deny-hono-stdio-require.cjs')
const guardedArgs = ['--no-warnings', '--require', cjsGuard, '--experimental-loader', esmGuard]

test('the stdio HTTP guard detects attempted imports via ESM and CJS', () => {
  for (const requestedImport of [
    ['--input-type=module', '-e', "await import('@modelcontextprotocol/sdk/server/streamableHttp.js')"],
    ['-e', "require('@modelcontextprotocol/sdk/server/streamableHttp.js')"],
    ['--input-type=module', '-e', "await import('@hono/node-server/serve-static')"],
    ['-e', "require('@hono/node-server/serve-static')"],
  ]) {
    const outcome = spawnSync(process.execPath, [...guardedArgs, ...requestedImport], {
      cwd: packageRoot,
      encoding: 'utf8',
      timeout: 10_000,
      env: { ...process.env, NODE_OPTIONS: '' },
    })
    assert.notEqual(outcome.status, 0, 'deliberately blocked import must fail')
    assert.match(outcome.stderr ?? '', /STDIO_HTTP_IMPORT_FORBIDDEN/,
      'the custom guard, rather than a missing package, must reject the import')
  }
})

test('the real stdio MCP handles all 17 tool paths without an HTTP adapter', { timeout: 40_000 }, async () => {
  const home = mkdtempSync(resolve(tmpdir(), 'ccguide-stdio-hono-'))
  const transport = new StdioClientTransport({
    command: process.execPath,
    args: [...guardedArgs, resolve(packageRoot, 'dist/index.js')],
    cwd: packageRoot,
    env: {
      HOME: home,
      GUIDE_ROOT: repositoryRoot,
      PATH: process.env.PATH ?? '/usr/bin:/bin',
      LANG: 'C',
      NODE_OPTIONS: '',
      HTTP_PROXY: 'http://127.0.0.1:9',
      HTTPS_PROXY: 'http://127.0.0.1:9',
      NO_PROXY: '',
    },
    stderr: 'pipe',
  })
  const client = new Client({ name: 'ccguide-stdio-http-isolation', version: '1' })
  try {
    await client.connect(transport)
    const [tools, resources, prompts] = await Promise.all([
      client.listTools(), client.listResources(), client.listPrompts(),
    ])
    assert.equal(tools.tools.length, 17)
    assert.equal(resources.resources.length, 7)
    assert.equal(prompts.prompts.length, 1)

    const cases = [
      ['list_topics', {}],
      ['search_guide', { query: 'hooks', limit: 3 }],
      ['read_section', { path: 'guide/core/context-engineering.md', limit: 12 }],
      ['get_cheatsheet', {}],
      ['list_examples', {}],
      ['search_examples', { query: 'code reviewer' }],
      ['get_example', { name: 'code-reviewer' }],
      ['get_release', { count: 1 }],
      ['compare_versions', { from: '2.1.0', to: '2.1.1' }],
      ['get_changelog', { count: 1 }],
      ['get_digest', { period: 'week' }],
      ['list_threats', {}],
      ['get_threat', { id: 'T001' }],
      ['init_official_docs', {}],
      ['refresh_official_docs', {}],
      ['diff_official_docs', {}],
      ['search_official_docs', { query: 'MCP_STDIO_DOCS_CANARY' }],
    ]
    assert.deepEqual(new Set(cases.map(([name]) => name)),
      new Set(tools.tools.map(({ name }) => name)))
    for (const [name, args] of cases) {
      const result = await client.callTool({ name, arguments: args })
      assert.notEqual(result.isError, true, name + ' must work without an HTTP adapter')
      assert.ok((result.content ?? []).some((part) => typeof part.text === 'string' && part.text.length > 0),
        name + ' must return text')
    }
  } finally {
    await client.close()
    rmSync(home, { recursive: true, force: true })
  }
})
