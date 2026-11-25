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
const editor = await server.ssrLoadModule('/src/publications.js')
const { default: StreamCard } = await server.ssrLoadModule('/src/StreamCard.jsx')
const { default: ResourceSearch } = await server.ssrLoadModule('/src/ResourceSearch.jsx')

test('pending publications survive editor remounts and malformed storage safely', () => {
  let stored
  globalThis.sessionStorage = { getItem: () => stored, setItem: (_, value) => { stored = value } }
  const pending = {
    12: editor.capturePublication({ ...stream, version: 3 }, 'Network interrupted'),
    13: { links: '[[1,100,true]]', message: 'Network interrupted' },
  }
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

test('reconciliation does not confirm an unchanged draft that only pins a share', () => {
  const saved = { ...offer, share: 100 }
  const pinned = { ...saved, pinned: true }
  const pending = editor.capturePublication({
    ...stream, version: 4, offers: [pinned], published_offers: [saved],
  })
  const result = editor.publicationResult({
    ...stream, version: 5, offers: [pinned], published_offers: [saved],
  }, pending)
  assert.equal(result.kind, 'reconciled')
  assert.match(result.message, /Ожидаемые изменения не подтверждены/)
})

test('reconciliation does not confirm a clean snapshot from the original draft version', () => {
  const saved = { ...offer, share: 100, pinned: true }
  const pending = editor.capturePublication({ ...stream, version: 4, offers: [saved] })
  const result = editor.publicationResult({
    ...stream, version: 4, dirty: false, offers: [saved],
  }, pending)
  assert.equal(result.kind, 'reconciled')
})

test('legacy session markers do not confirm by links alone', () => {
  const saved = { ...offer, share: 100 }
  const result = editor.publicationResult({
    ...stream, version: 5, dirty: false, offers: [saved],
  }, { links: '[[1,100,true]]' })
  assert.equal(result.kind, 'reconciled')
  assert.match(result.message, /недостаточно/)
})

test('reconciliation confirms a published snapshot when another edit is already in progress', () => {
  const savedOffer = { ...offer, share: 100, pinned: true }
  const pending = editor.capturePublication({ ...stream, version: 4, offers: [savedOffer] })
  const result = editor.publicationResult({
    ...stream, version: 6,
    offers: [{ ...offer, share: 50 }],
    published_offers: [savedOffer],
  }, pending)
  assert.equal(result.kind, 'reconciled')
  assert.match(result.message, /Публикация подтверждена/)
  assert.doesNotMatch(result.message, /повторить/)
})

test('a catalog rename does not hide a successfully published allocation', () => {
  const saved = [{ ...offer, share: 50 }, { ...offer, id: 2, share: 50 }]
  const draft = [{ ...saved[0], share: 70, pinned: true }, { ...saved[1], share: 30 }]
  const pending = editor.capturePublication({
    ...stream, version: 4, offers: draft, published_offers: saved,
  })
  assert.deepEqual(editor.publicationResult({
    ...stream, version: 6, dirty: false, offers: [{ ...draft[0], name: 'Renamed offer' }, draft[1]],
  }, pending), { kind: 'published' })
})

test('Fetch does not confirm a cancelled name-only draft when the catalog imports the same name', () => {
  const saved = { ...offer, share: 100 }
  const renamed = { ...saved, name: 'Renamed offer' }
  const pending = editor.capturePublication({
    ...stream, version: 4, offers: [renamed], published_offers: [saved],
  })
  const result = editor.publicationResult({
    ...stream, version: 6, dirty: false, offers: [renamed],
  }, pending)
  assert.equal(result.kind, 'reconciled')
  assert.match(result.message, /недостаточно/)
})

test('Fetch recognizes unchanged remote publication when another tab unpins the offer', () => {
  const saved = { ...offer, share: 25 }
  const target = { ...saved, share: 40, pinned: true }
  const pending = editor.capturePublication({
    ...stream, version: 2, offers: [target], published_offers: [saved],
  })
  const result = editor.publicationResult({
    ...stream, version: 5, dirty: false, offers: [{ ...target, pinned: false }],
  }, pending)
  assert.equal(result.kind, 'reconciled')
  assert.match(result.message, /Привязки и доли совпадают/)
})

test('Fetch distinguishes a single local commit from Cancel and Pin in another tab', () => {
  const saved = { ...offer, share: 100 }
  const target = { ...saved, pinned: true }
  const pending = editor.capturePublication({
    ...stream, version: 2, offers: [target], published_offers: [saved],
  })
  const published = { ...stream, dirty: false, offers: [target] }
  assert.deepEqual(editor.publicationResult({ ...published, version: 3 }, pending), { kind: 'published' })
  const result = editor.publicationResult({ ...published, version: 4 }, pending)
  assert.equal(result.kind, 'reconciled')
  assert.match(result.message, /недостаточно/)
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
