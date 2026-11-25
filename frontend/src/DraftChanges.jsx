import SelectionIcon from './SelectionIcon.jsx'

export default function DraftChanges({ published, offers, total, previousTotal }) {
  const previous = new Map(published.map((offer) => [offer.id, offer]))
  const current = new Map(offers.map((offer) => [offer.id, offer]))
  const changes = []
  for (const id of new Set([...previous.keys(), ...current.keys()])) {
    const before = previous.get(id)
    const after = current.get(id)
    const name = after?.name ?? before.name
    const notes = []
    if ((!after || after.removed) && before && !before.removed) {
      notes.push('Будет убран', before.share + '% → 0%')
    } else if (after && !after.removed) {
      if (!before || before.removed) {
        notes.push(before ? 'Будет возвращён' : 'Будет добавлен', 'Доля ' + after.share + '%')
      } else if (before.share !== after.share) {
        notes.push(before.share + '% → ' + after.share + '%')
      }
      if (before && !before.removed && before.enabled !== after.enabled) {
        notes.push(after.enabled ? 'Будет включён' : 'Будет выключен')
      }
      if (Boolean(before?.pinned) !== after.pinned) {
        notes.push(after.pinned ? 'Доля закреплена' : 'Закрепление снято')
      }
    }
    if (notes.length) changes.push({ id, name, text: notes.join(' · ') })
  }
  if (!changes.length) return null

  return (
    <details className="draft-changes">
      <summary>
        <span>Посмотреть изменения</span>
        <span className="disclosure-chevron"><SelectionIcon kind="chevron" /></span>
      </summary>
      <div className="disclosure-content">
        <ul>
          {changes.map(({ id, name, text }) => (
            <li key={id}><strong>#{id} {name}</strong><span>{text}</span></li>
          ))}
        </ul>
        {previousTotal !== total && <p className="field-status">Сумма активных долей · {previousTotal}% → {total}%</p>}
      </div>
    </details>
  )
}
