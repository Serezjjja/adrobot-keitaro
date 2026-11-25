import { useEffect, useState } from 'react'
import { request } from './api.js'
import CampaignEditor from './CampaignEditor.jsx'
import CreateCampaign from './CreateCampaign.jsx'
import ResourceSearch from './ResourceSearch.jsx'
import './campaignForms.css'

const campaignLabel = (campaign) => campaign.name + ' · #' + campaign.id

export default function App() {
  const [view, setView] = useState('editor')
  const [campaigns, setCampaigns] = useState(null)
  const [selectedId, setSelectedId] = useState(null)
  const [query, setQuery] = useState('')
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [reload, setReload] = useState(0)
  const [created, setCreated] = useState(null)

  useEffect(() => {
    if (view !== 'editor') return
    const controller = new AbortController()
    request('/campaigns', { signal: controller.signal }).then((data) => {
      if (controller.signal.aborted) return
      setCampaigns(data)
      setError('')
    }).catch((error) => {
      if (!controller.signal.aborted) setError(error.message)
    }).finally(() => {
      if (!controller.signal.aborted) setLoading(false)
    })
    return () => controller.abort()
  }, [reload, view])

  function showCampaigns() {
    if (view === 'editor') return
    setLoading(true)
    setError('')
    setView('editor')
  }

  function openCampaignPicker() {
    setSelectedId(null)
    setQuery('')
    setCreated(null)
    showCampaigns()
  }

  function openCreated(campaign) {
    setSelectedId(campaign.id)
    setQuery(campaignLabel(campaign))
    setCreated(campaign)
    showCampaigns()
  }

  function openCreate() {
    setView('create')
    setCreated(null)
  }

  function selectCampaign(campaign) {
    if (campaign.id !== selectedId) setCreated(null)
    setSelectedId(campaign.id)
    setQuery(campaignLabel(campaign))
  }

  const knownCampaigns = campaigns ?? []
  const available = created && !knownCampaigns.some((campaign) => campaign.id === created.id)
    ? [created, ...knownCampaigns] : knownCampaigns
  const selected = available.find((campaign) => campaign.id === selectedId)
  const term = selected && query === campaignLabel(selected) ? '' : query.trim().toLowerCase()
  const exactId = /^#?\d+$/.test(term) ? term.replace(/^#/, '') : null
  const matches = available.filter((campaign) => campaignLabel(campaign).toLowerCase().includes(term))
    .sort((left, right) => Number(String(right.id) === exactId) - Number(String(left.id) === exactId))

  function retryCampaigns() {
    setLoading(true)
    setReload((value) => value + 1)
  }

  const campaignSearch = (
    <ResourceSearch label="Кампания по названию или ID" visuallyHiddenLabel={Boolean(selected)}
      placeholder={selected ? 'Кампания по названию или ID' : 'Например, #123'}
      statusInPopup value={selected} onChange={selectCampaign} disabled={busy}
      query={query} onQueryChange={setQuery} options={matches}
      onClose={() => { if (selected) setQuery(campaignLabel(selected)) }}
      status={campaigns && term && !matches.length ? 'Кампании не найдены.' : ''}
      refreshing={loading}
      error={error} retryLabel="Повторить загрузку" onRefresh={retryCampaigns} />
  )

  return (
    <>
      <header className="app-header">
        <div className="header-inner">
          <button className="app-name" type="button" onClick={openCampaignPicker} disabled={busy}
            aria-label="Adrobot — кампании">Adrobot</button>
          {view === 'editor' && selected && <div className="header-campaign">{campaignSearch}</div>}
          {(view === 'create' || selected) && (
            <nav className="header-actions" aria-label="Разделы">
              {view === 'create'
                ? <button type="button" onClick={showCampaigns} disabled={busy}>Кампании</button>
                : <button type="button" onClick={openCreate} disabled={busy}>Новая кампания</button>}
            </nav>
          )}
        </div>
      </header>
      <main>
        {view === 'create' ? <CreateCampaign onCreated={openCreated} busy={busy} onBusyChange={setBusy} /> : (
          <>
            {selected ? (
              <CampaignEditor key={selected.id} campaign={selected}
                created={created?.id === selected.id} onBusyChange={setBusy} />
            ) : (
              <section className="empty-state" aria-labelledby="campaigns-heading" aria-busy={loading}>
                <h1 id="campaigns-heading">Кампании</h1>
                <div className="campaign-picker">{campaignSearch}</div>
                <button className="primary campaign-create-action" type="button" onClick={openCreate} disabled={busy}>Новая кампания</button>
                {error ? (
                  <>
                    <p className="notice error" role="alert">{error}</p>
                    <button type="button" onClick={retryCampaigns} disabled={loading}>Повторить загрузку</button>
                  </>
                ) : !available.length && (
                  <p role="status">{loading ? 'Загружаем кампании…' : 'В Keitaro пока нет доступных кампаний.'}</p>
                )}
              </section>
            )}
          </>
        )}
      </main>
    </>
  )
}
