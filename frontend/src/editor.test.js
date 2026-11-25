import assert from 'node:assert/strict'
import { after, test } from 'node:test'
import { createServer } from 'vite'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'

const server = await createServer({
  server: { middlewareMode: true },
  optimizeDeps: { noDiscovery: true },
  appType: 'custom',
})
after(() => server.close())
const editor = await server.ssrLoadModule('/src/CampaignEditor.jsx')
const { default: StreamCard } = await server.ssrLoadModule('/src/StreamCard.jsx')
const { default: ResourceSearch } = await server.ssrLoadModule('/src/ResourceSearch.jsx')

test('pending publications survive editor remounts and malformed storage safely', () => {
  let stored
  globalThis.sessionStorage = { getItem: () => stored, setItem: (_, value) => { stored = value } }
  const pending = { 12: { links: '[[1,100,true]]', message: 'Network interrupted' } }
  editor.savePendingPublications(5, pending)
  assert.deepEqual(editor.readPendingPublications(5), pending)
  for (stored of ['null', '[]', '{"12":null}', '{"12":{"message":42}}', 'broken']) {
    assert.deepEqual(editor.readPendingPublications(5), {})
  }
  delete globalThis.sessionStorage
})

const offer = { id: 1, name: 'Offer', share: 0, enabled: true, removed: false, pinned: false }
const stream = { id: 12, name: 'Stream', editable: true, dirty: true, offers: [offer], published_offers: [{ ...offer, enabled: false }] }

test('preview includes enabled-state changes even when shares stay zero', () => {
  const html = renderToStaticMarkup(React.createElement(StreamCard, { stream }))
  assert.match(html, /Посмотреть изменения/)
  assert.match(html, /Будет включён/)
})

test('server pending marker blocks cancellation and mutations without browser state', () => {
  const html = renderToStaticMarkup(React.createElement(StreamCard, { stream: { ...stream, publication_pending: true } }))
  assert.match(html, /Публикация не подтверждена/)
  assert.match(html, /disabled="">Отменить изменения/)
})

test('exact ID is the default keyboard target before prefix matches', () => {
  const html = renderToStaticMarkup(React.createElement(ResourceSearch, {
    label: 'Campaign', query: '#12', options: [{ id: 120, name: 'Prefix' }, { id: 12, name: 'Exact' }],
  }))
  assert.match(html, /class="combo-option is-active"><span class="combo-option-name">Exact/)
})

test('reconciliation retains tracking while server publication is pending', () => {
  assert.equal(editor.publicationResult({ ...stream, publication_pending: true }, { links: '[]' }), null)
})

test('reconciliation confirms a published snapshot when another edit is already in progress', () => {
  const savedOffer = { ...offer, share: 100 }
  const result = editor.publicationResult({
    ...stream,
    offers: [{ ...offer, share: 50 }],
    published_offers: [savedOffer],
  }, { links: '[[1,100,true]]' })
  assert.equal(result.kind, 'reconciled')
  assert.match(result.message, /Публикация подтверждена/)
  assert.doesNotMatch(result.message, /повторить/)
})

for (const state of ['missing', 'conflict']) {
  test(`successful Fetch resolves browser uncertainty for a ${state} stream`, () => {
    const result = editor.publicationResult({ ...stream, [state]: true }, { links: '[]' })
    assert.equal(result.kind, 'reconciled')
    assert.match(result.message, /черновик сохранён/)
  })
}

test('in-flight publication shows progress without a premature failure warning', () => {
  const html = renderToStaticMarkup(React.createElement(StreamCard, {
    stream, operation: 'publish', pendingPublication: { links: '[]', message: 'Awaiting response' },
  }))
  assert.match(html, /Публикуем/)
  assert.doesNotMatch(html, /Публикация не подтверждена/)
  assert.doesNotMatch(html, /Ожидает сверки с Keitaro/)
})

test('a clean pinned stream stays neutral until another draft edit', () => {
  const html = renderToStaticMarkup(React.createElement(StreamCard, {
    stream: { ...stream, dirty: false, offers: [{ ...offer, pinned: true }] },
  }))
  assert.match(html, /Закреплено/)
  assert.doesNotMatch(html, /draft-chip|Посмотреть изменения|Сейчас 0%/)
})
