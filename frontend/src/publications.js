function offerSnapshot(offers) {
  return JSON.stringify(offers
    .map(({ id, share, enabled, removed, pinned }) => [id, share, enabled, removed, pinned])
    .sort(([left], [right]) => left - right))
}

function remoteOfferLinks(offers) {
  return JSON.stringify(offers
    .filter((offer) => !offer.removed)
    .map(({ id, share, enabled }) => [id, share, enabled])
    .sort(([left], [right]) => left - right))
}

export function capturePublication(stream, message = '') {
  const snapshot = offerSnapshot(stream.offers)
  const links = remoteOfferLinks(stream.offers)
  const published = stream.published_offers
  // Fetch обновляет названия из каталога независимо от результата Push.
  const changesOffers = published == null || snapshot !== offerSnapshot(published)
  const changesLinks = published != null && links !== remoteOfferLinks(published)
  return { snapshot, links, changesOffers, changesLinks, version: stream.version, message }
}

function hasDraftSnapshot(pending) {
  return typeof pending.snapshot === 'string' && typeof pending.changesOffers === 'boolean'
    && Number.isSafeInteger(pending.version) && pending.version > 0
}

export function readPendingPublications(campaignId) {
  try {
    const saved = JSON.parse(sessionStorage.getItem('adrobot:publications:' + campaignId) ?? '{}')
    if (!saved || Array.isArray(saved) || typeof saved !== 'object') return {}
    return Object.fromEntries(Object.entries(saved).filter(([id, pending]) => /^\d+$/.test(id)
      && pending && typeof pending.message === 'string'
      && (hasDraftSnapshot(pending) || typeof pending.links === 'string')))
  } catch {
    return {}
  }
}

export function savePendingPublications(campaignId, pending) {
  try {
    sessionStorage.setItem('adrobot:publications:' + campaignId, JSON.stringify(pending))
  } catch {
    // При недоступном хранилище серверная отметка продолжает защищать известные публикации.
  }
}

export function publicationResult(stream, pending) {
  if (stream.publication_pending) return null
  if (stream.missing) return { kind: 'reconciled', message: 'Сверка завершена. Поток отсутствует в Keitaro; черновик сохранён.' }
  if (stream.conflict) return { kind: 'reconciled', message: 'Сверка завершена. Поток изменился в Keitaro; черновик сохранён.' }
  const published = stream.dirty ? stream.published_offers : stream.offers
  const tracksChanges = hasDraftSnapshot(pending) && pending.changesOffers
  if (tracksChanges && stream.version > pending.version && published) {
    const matchesSnapshot = offerSnapshot(published) === pending.snapshot
    const changedLinks = pending.changesLinks === true && typeof pending.links === 'string'
    // Cancel и повторный Pin дают тот же снимок, но занимают две версии вместо одной.
    const singleLocalCommit = !stream.dirty && stream.version === pending.version + 1
    if (matchesSnapshot && (changedLinks || singleLocalCommit)) {
      return stream.dirty
        ? { kind: 'reconciled', message: 'Публикация подтверждена. В потоке уже есть новый черновик.' }
        : { kind: 'published' }
    }
    if (changedLinks && remoteOfferLinks(published) === pending.links) {
      return {
        kind: 'reconciled',
        message: 'Привязки и доли совпадают с отправленными. Текущие закрепления или архив отличаются.',
      }
    }
    if (matchesSnapshot) {
      return {
        kind: 'reconciled',
        message: 'Состояние совпадает с отправленным черновиком. Сохранённых данных недостаточно, чтобы подтвердить публикацию.',
      }
    }
  }
  if (!tracksChanges && !stream.dirty) {
    return { kind: 'reconciled', message: 'Потоки обновлены. Сохранённых данных недостаточно, чтобы подтвердить публикацию.' }
  }
  return {
    kind: 'reconciled',
    message: stream.dirty
      ? 'Ожидаемые изменения не подтверждены. Черновик сохранён.'
      : 'Потоки обновлены. Ожидаемые изменения не найдены в Keitaro.',
  }
}

export function reconcilePublications(previousStreams, current, pendingPublications) {
  const results = {}
  const missing = []
  const remaining = {}
  const tracked = { ...pendingPublications }
  for (const stream of previousStreams ?? []) {
    if (stream.publication_pending && !tracked[stream.id]) tracked[stream.id] = capturePublication(stream)
  }
  for (const [streamId, pending] of Object.entries(tracked)) {
    const stream = current.find((item) => String(item.id) === streamId)
    if (stream) results[streamId] = publicationResult(stream, pending)
    else missing.push('#' + streamId)
    if (stream?.publication_pending) remaining[streamId] = pending
  }
  return { results, missing, remaining }
}
