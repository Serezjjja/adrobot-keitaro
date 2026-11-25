import assert from 'node:assert/strict'
import { afterEach, test } from 'node:test'
import { ApiError, request } from './api.js'

const originalFetch = globalThis.fetch
afterEach(() => { globalThis.fetch = originalFetch })

test('sends a JSON mutation to the same-origin API', async () => {
  globalThis.fetch = async (url, options) => {
    assert.equal(url, '/api/streams/12/publish')
    assert.equal(options.method, 'POST')
    assert.equal(options.headers['Content-Type'], 'application/json')
    assert.deepEqual(JSON.parse(options.body), { version: 3 })
    return Response.json({ id: 12, dirty: false })
  }
  assert.deepEqual(await request('/streams/12/publish', { method: 'POST', body: { version: 3 } }), { id: 12, dirty: false })
})

test('keeps the HTTP conflict status and the server message', async () => {
  globalThis.fetch = async () => Response.json({ detail: 'Поток изменён в Keitaro.' }, { status: 409 })
  await assert.rejects(request('/streams/12/publish', { method: 'POST', body: { version: 3 } }), (error) => {
    assert.ok(error instanceof ApiError)
    assert.equal(error.status, 409)
    assert.equal(error.message, 'Поток изменён в Keitaro.')
    return true
  })
})

test('formats field validation errors from FastAPI', async () => {
  globalThis.fetch = async () => Response.json({ detail: [{ loc: ['body', 'country'], msg: 'Invalid country' }] }, { status: 422 })
  await assert.rejects(request('/campaigns'), { message: 'country — Invalid country' })
})

test('reports unexpected non-JSON responses without exposing their body', async () => {
  globalThis.fetch = async () => new Response('<html>proxy failure</html>', { status: 502 })
  await assert.rejects(request('/campaigns'), (error) => {
    assert.equal(error.status, 502)
    assert.equal(error.message.includes('<html>'), false)
    return true
  })
})

test('preserves cancellation so stale searches stay silent', async () => {
  const abort = new DOMException('The request was aborted', 'AbortError')
  globalThis.fetch = async () => { throw abort }
  await assert.rejects(request('/offers?q=old'), (error) => error === abort)
})

test('network errors explain that an operation needs verification before retrying', async () => {
  globalThis.fetch = async () => { throw new TypeError('fetch failed') }
  await assert.rejects(request('/streams/12/publish', { method: 'POST', body: { version: 3 } }), (error) => {
    assert.equal(error.status, 0)
    assert.match(error.message, /Проверьте состояние перед повтором/)
    return true
  })
})

test('preserves cancellation while reading the response body', async () => {
  const abort = new DOMException('The request was aborted', 'AbortError')
  globalThis.fetch = async () => ({
    ok: true, status: 200, json: async () => { throw abort },
  })
  await assert.rejects(request('/offers?q=old'), (error) => error === abort)
})

for (const failure of ['network', 'body', 'abort-body', 502, 503, 504]) {
  test(`marks a publication outcome uncertain after ${failure}`, async () => {
    globalThis.fetch = async () => {
      if (failure === 'network') throw new TypeError('network')
      if (typeof failure === 'number') return Response.json({ detail: 'Gateway failure' }, { status: failure })
      return { ok: true, status: 200, json: async () => {
        throw failure === 'abort-body' ? new DOMException('aborted', 'AbortError') : new TypeError('interrupted')
      } }
    }
    await assert.rejects(request('/streams/12/publish', { method: 'POST' }), (error) => {
      assert.equal(error.uncertain, true)
      return true
    })
  })
}

test('an explicit rejected publication is safe to retry', async () => {
  globalThis.fetch = async () => Response.json({ detail: 'Conflict' }, { status: 409 })
  await assert.rejects(request('/streams/12/publish', { method: 'POST' }), (error) => {
    assert.equal(error.uncertain, false)
    return true
  })
})
