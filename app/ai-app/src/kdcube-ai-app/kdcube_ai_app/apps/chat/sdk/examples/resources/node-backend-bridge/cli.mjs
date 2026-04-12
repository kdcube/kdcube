import express from 'express'

import { registerSampleRoutes } from './sample_routes.js'

const ALLOWED_PREFIXES = ['/api/projects']
const SUPPORTED_METHODS = new Set(['GET', 'POST', 'PUT', 'PATCH', 'DELETE'])
const HOST = process.env.HOST || '127.0.0.1'

async function readPayload() {
  const chunks = []
  for await (const chunk of process.stdin) {
    chunks.push(chunk)
  }
  const raw = Buffer.concat(chunks).toString('utf-8').trim()
  return raw ? JSON.parse(raw) : {}
}

function validateRequest(method, requestPath) {
  if (!SUPPORTED_METHODS.has(method)) {
    throw new Error(`Unsupported backend method: ${method}`)
  }
  if (!requestPath || typeof requestPath !== 'string') {
    throw new Error('path is required')
  }
  if (!ALLOWED_PREFIXES.some((prefix) => requestPath.startsWith(prefix))) {
    throw new Error(`Unsupported backend path: ${requestPath}`)
  }
}

function buildBackendApp(payload) {
  const app = express()
  app.use(express.json({ limit: '10mb' }))
  registerSampleRoutes(app, {
    projectRoot: String(payload.projectRoot || ''),
  })
  app.use((_req, res) => {
    res.status(404).json({ error: 'Route not found' })
  })
  app.use((error, _req, res, _next) => {
    res.status(500).json({ error: error?.message || 'Node backend bridge failed' })
  })
  return app
}

async function withTransientServer(payload, callback) {
  const app = buildBackendApp(payload)
  const server = await new Promise((resolve) => {
    const instance = app.listen(0, HOST, () => resolve(instance))
  })

  try {
    const address = server.address()
    if (!address || typeof address === 'string') {
      throw new Error('Failed to resolve backend bridge listen address')
    }
    return await callback(address.port)
  } finally {
    await new Promise((resolve, reject) => {
      server.close((error) => {
        if (error) reject(error)
        else resolve(null)
      })
    })
  }
}

async function invokeRequest(payload) {
  const method = String(payload.method || 'GET').toUpperCase()
  const requestPath = String(payload.path || '')
  validateRequest(method, requestPath)
  const hasRequestBody =
    !['GET', 'HEAD'].includes(method) &&
    payload.body !== undefined &&
    payload.body !== null

  return withTransientServer(payload, async (port) => {
    const response = await fetch(`http://${HOST}:${port}${requestPath}`, {
      method,
      headers: hasRequestBody ? { 'Content-Type': 'application/json' } : undefined,
      body: hasRequestBody ? JSON.stringify(payload.body) : undefined,
    })

    const text = await response.text()
    let data = null
    if (text) {
      try {
        data = JSON.parse(text)
      } catch {
        data = { raw: text }
      }
    }

    return {
      ok: response.ok,
      status: response.status,
      data,
    }
  })
}

const payload = await readPayload()

try {
  const result = await invokeRequest(payload)
  process.stdout.write(`${JSON.stringify(result)}\n`)
} catch (error) {
  process.stdout.write(
    `${JSON.stringify({
      ok: false,
      status: 500,
      error: error?.message || 'Node backend bridge failed',
    })}\n`,
  )
  process.exitCode = 1
}
