import { useEffect, useState } from 'react'
import { request } from './api.js'
import CampaignEditor from './CampaignEditor.jsx'
import CreateCampaign from './CreateCampaign.jsx'

export default function App() {
  const [view, setView] = useState('editor')
  const [campaigns, setCampaigns] = useState([])
  const [selectedId, setSelectedId] = useState('')
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [reload, setReload] = useState(0)
  const [created, setCreated] = useState('')

  useEffect(() => {
    const controller = new AbortController()
    setLoading(true)
    setError('')
    request('/campaigns', { signal: controller.signal }).then((data) => {
      setCampaigns(data)
      setSelectedId((current) => data.some((campaign) => String(campaign.id) === current) ? current : '')
    }).catch((error) => {
      if (error.name !== 'AbortError') setError(error.message)
    }).finally(() => {
      if (!controller.signal.aborted) setLoading(false)
    })
    return () => controller.abort()
  }, [reload])

  function openCreated(campaign) {
    setCampaigns((current) => [campaign, ...current.filter((item) => item.id !== campaign.id)])
    setSelectedId(String(campaign.id))
    setCreated(`Кампания ${campaign.name} создана.`)
    setView('editor')
  }

  const selected = campaigns.find((campaign) => String(campaign.id) === selectedId)

  return (
    <>
      <header className="app-header">
        <a className="app-name" href="#" onClick={(event) => { event.preventDefault(); if (!busy) setView('editor') }}>Adrobot</a>
        <nav aria-label="Разделы">
          <button type="button" className={view === 'editor' ? 'nav-active' : ''} aria-current={view === 'editor' ? 'page' : undefined}
            onClick={() => setView('editor')} disabled={busy}>Кампании</button>
          <button type="button" className={view === 'create' ? 'nav-active' : ''} aria-current={view === 'create' ? 'page' : undefined}
            onClick={() => { setView('create'); setCreated('') }} disabled={busy}>Новая кампания</button>
        </nav>
        <span className="header-context">Keitaro</span>
      </header>
      <main>
        {view === 'create' ? <CreateCampaign onCreated={openCreated} onBusyChange={setBusy} /> : (
          <>
            <div className="campaign-picker">
              <div className="field">
                <label htmlFor="selected-campaign">Кампания</label>
                <select id="selected-campaign" value={selectedId} disabled={loading || busy}
                  onChange={(event) => { setSelectedId(event.target.value); setCreated('') }}>
                  <option value="">{loading ? 'Загружаем кампании…' : 'Выберите кампанию'}</option>
                  {campaigns.map((campaign) => <option key={campaign.id} value={campaign.id}>{campaign.name} · #{campaign.id}</option>)}
                </select>
              </div>
              <button type="button" onClick={() => setReload((value) => value + 1)} disabled={loading || busy}>Обновить список</button>
            </div>
            {error && <div className="notice error" role="alert">{error}</div>}
            {created && <div className="notice success" role="status">{created}</div>}
            {selected ? <CampaignEditor key={selected.id} campaign={selected} onBusyChange={setBusy} /> : (
              <div className="empty-state">
                <h1>Кампании</h1>
                <p>{!loading && campaigns.length === 0 && !error ? 'В Keitaro пока нет доступных кампаний. Создайте первую.' : 'Выберите кампанию для редактирования потоков.'}</p>
                <button type="button" className="primary" onClick={() => setView('create')} disabled={busy}>Создать кампанию</button>
              </div>
            )}
          </>
        )}
      </main>
    </>
  )
}
