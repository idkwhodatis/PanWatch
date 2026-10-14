import { useEffect, useRef, useState } from 'react'
import { fetchAPI } from '@panwatch/api'
import { Switch } from '@panwatch/base-ui/components/ui/switch'
import { Button } from '@panwatch/base-ui/components/ui/button'
import { useTranslation } from 'react-i18next'

interface Extension {
  id: string
  enabled: boolean
  available: boolean
  exchanges: string[]
  capabilities: Record<string, boolean>
}

export default function MarketExtensionsSection() {
  const { t } = useTranslation('configuration')
  const [items, setItems] = useState<Extension[]>([])
  const [busy, setBusy] = useState(true)
  const [failed, setFailed] = useState(false)
  const generation = useRef(0)

  const load = async () => {
    const request = ++generation.current
    setBusy(true)
    setFailed(false)
    try {
      const result = await fetchAPI<Extension[]>('/market-extensions')
      if (request === generation.current) setItems(result)
    } catch {
      if (request === generation.current) setFailed(true)
    } finally {
      if (request === generation.current) setBusy(false)
    }
  }
  useEffect(() => { void load(); return () => { generation.current++ } }, [])

  const update = async (item: Extension, enabled: boolean) => {
    if (busy) return
    const request = ++generation.current
    setBusy(true)
    setFailed(false)
    try {
      const result = await fetchAPI<Extension>(`/market-extensions/${item.id}`, {
        method: 'PATCH', body: JSON.stringify({ enabled }),
      })
      if (request === generation.current) setItems(previous => previous.map(row => row.id === item.id ? result : row))
    } catch {
      if (request === generation.current) setFailed(true)
    } finally {
      if (request === generation.current) setBusy(false)
    }
  }

  return <section className="lg:col-span-12 rounded-lg border p-4 space-y-3">
    <h2 className="font-semibold">{t('marketExtensions.title')}</h2>
    <p className="text-sm text-muted-foreground">{t('marketExtensions.description')}</p>
    {failed && <div role="alert">
      <p>{t('marketExtensions.failed')}</p>
      <Button variant="outline" disabled={busy} onClick={() => void load()}>{t('marketExtensions.retry')}</Button>
    </div>}
    {items.map(item => <div key={item.id} className="space-y-2">
      <div className="flex items-center justify-between gap-3">
        <span>{t(`marketExtensions.names.${item.id}`, { defaultValue: item.id })} ({item.exchanges.join(' / ')})</span>
        <Switch aria-label={t(`marketExtensions.names.${item.id}`, { defaultValue: item.id })} checked={item.enabled}
          disabled={busy} onCheckedChange={enabled => void update(item, enabled)} />
      </div>
      <p className="text-sm">{t(item.available ? 'marketExtensions.installed' : 'marketExtensions.notInstalled')}</p>
      <ul className="flex flex-wrap gap-x-4 gap-y-1 text-sm text-muted-foreground">
        {Object.entries(item.capabilities).map(([name, available]) => <li key={name}>
          {t(`marketExtensions.capabilities.${name}`, { defaultValue: name })}: {t(available ? 'marketExtensions.available' : 'marketExtensions.unavailable')}
        </li>)}
      </ul>
    </div>)}
  </section>
}
