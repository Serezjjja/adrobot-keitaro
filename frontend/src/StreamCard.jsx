import { useState } from 'react'
import OfferSearch from './OfferSearch.jsx'

export default function StreamCard({ stream, busy, locked, onAction, error, notice }) {
  const [picked, setPicked] = useState(null)
  const blocked = busy || locked || stream.missing || stream.conflict
  const activeOffers = stream.offers.filter((offer) => !offer.removed)
  const total = activeOffers.filter((offer) => offer.enabled).reduce((sum, offer) => sum + offer.share, 0)

  async function addOffer() {
    if (!picked) return
    const succeeded = await onAction(stream, 'add', picked.id)
    if (succeeded) setPicked(null)
  }

  return (
    <article className={`stream${stream.dirty ? ' stream-dirty' : ''}`} aria-labelledby={`stream-${stream.id}`} aria-busy={busy}>
      <header className="stream-header">
        <div>
          <div className="stream-title-line">
            <h2 id={`stream-${stream.id}`}>{stream.name}</h2>
            <span className="muted numeric">#{stream.id}</span>
          </div>
          <p className="stream-state">
            {stream.missing ? 'Поток отсутствует в Keitaro' : stream.dirty ? 'Есть неопубликованные изменения' : 'Изменений нет'}
          </p>
        </div>
        {stream.dirty && (
          <div className="stream-actions">
            <button type="button" onClick={() => onAction(stream, 'cancel')} disabled={busy || locked}>Отменить изменения</button>
            {stream.editable && (
              <button className="primary" type="button" onClick={() => onAction(stream, 'publish')}
                disabled={blocked || Boolean(stream.issue)}>
                {busy ? 'Сохраняем…' : 'Опубликовать в Keitaro'}
              </button>
            )}
          </div>
        )}
      </header>

      {stream.conflict && <p className="notice error" role="alert">{stream.dirty
        ? 'Поток изменён в Keitaro. Черновик сохранён. Отмените изменения и загрузите потоки заново.'
        : 'Поток изменён в Keitaro. Загрузите потоки заново, чтобы принять актуальное состояние.'}</p>}
      {stream.missing && <p className="notice error" role="alert">{stream.dirty
        ? 'Отмените черновик и загрузите потоки заново, чтобы принять актуальное состояние.'
        : 'Загрузите потоки заново, чтобы принять актуальное состояние.'}</p>}
      {stream.issue && <p className="notice warning" role="alert">{stream.issue}</p>}
      {error && <p className="notice error" role="alert">{error}</p>}
      {notice && <p className="notice success" role="status">{notice}</p>}

      {!stream.editable ? <p className="read-only-note">Поток без редактируемых офферов. Маршрутизация настроена в Keitaro.</p> : (
        <>
          <div className="table-wrap">
            <table>
              <thead><tr><th scope="col">Оффер</th><th scope="col">Доля</th><th scope="col">Закрепление</th><th scope="col">Действия</th></tr></thead>
              <tbody>
                {stream.offers.map((offer) => (
                  <tr key={offer.id} className={offer.removed ? 'archived' : ''}>
                    <td>
                      <span className="offer-name">{offer.name}</span>
                      <span className="offer-meta">#{offer.id}{offer.removed ? ' · В архиве' : !offer.enabled ? ' · Выключен в Keitaro' : ''}</span>
                    </td>
                    <td className="share-cell numeric">{offer.removed ? 0 : offer.share}%</td>
                    <td>
                      {!offer.removed && offer.enabled && (
                        <button type="button" className={`text-button${offer.pinned ? ' pinned' : ''}`}
                          aria-label={`${offer.pinned ? 'Снять закрепление' : 'Закрепить долю'} оффера ${offer.name}`}
                          aria-pressed={offer.pinned} onClick={() => onAction(stream, offer.pinned ? 'unpin' : 'pin', offer.id)} disabled={blocked}>
                          {offer.pinned ? 'Закреплено' : 'Закрепить'}
                        </button>
                      )}
                    </td>
                    <td>
                      <button type="button" className="text-button"
                        aria-label={`${offer.removed ? 'Вернуть' : 'Удалить'} оффер ${offer.name}`}
                        onClick={() => onAction(stream, offer.removed ? 'restore' : 'remove', offer.id)} disabled={blocked}>
                        {offer.removed ? 'Вернуть' : 'Удалить'}
                      </button>
                    </td>
                  </tr>
                ))}
                {stream.offers.length === 0 && <tr><td colSpan={4} className="empty-cell">В потоке пока нет офферов.</td></tr>}
              </tbody>
              <tfoot><tr><th scope="row">Итого активные</th><td className={`share-cell numeric${total !== 100 ? ' invalid-total' : ''}`}>{total}%</td><td colSpan={2}></td></tr></tfoot>
            </table>
          </div>
          <div className="add-offer">
            <OfferSearch value={picked} onChange={setPicked} disabled={blocked} excludeIds={activeOffers.map((offer) => offer.id)} />
            <button type="button" onClick={addOffer} disabled={blocked || !picked}>Добавить оффер</button>
          </div>
        </>
      )}
    </article>
  )
}
