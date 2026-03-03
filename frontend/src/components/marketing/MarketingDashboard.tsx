import { cva } from 'class-variance-authority'
import { MessageSquare, Phone } from 'lucide-react'
import { useState } from 'react'
import { useAnalytics } from '../../hooks/useAnalytics'
import { useContacts } from '../../hooks/useContacts'
import { useMarketing } from '../../hooks/useMarketing'
import { cn } from '../../lib/utils'
import type { CampaignChannel } from '../../types/marketing'
import { CampaignDetail } from '../analytics/CampaignDetail'
import { CampaignTable } from '../analytics/CampaignTable'
import { NavBar } from '../layout/NavBar'

const channelToggleVariants = cva(
  'rounded-full border px-3 py-1 font-semibold text-[0.65rem] uppercase tracking-[0.15em] transition-colors',
  {
    variants: {
      active: {
        true: 'border-primary/40 bg-primary/15 text-primary',
        false: 'border-border/60 bg-card/30 text-muted-foreground hover:text-foreground',
      },
    },
    defaultVariants: { active: false },
  },
)

const contactRowVariants = cva(
  'contact-row flex items-center gap-3 rounded-lg px-3 py-2 transition-colors',
  {
    variants: {
      selected: {
        true: 'contact-row-selected bg-primary/10',
        false: 'hover:bg-card/50',
      },
    },
    defaultVariants: { selected: false },
  },
)

export function MarketingDashboard() {
  const [tenantId] = useState('public')
  const [companyId] = useState('ekaette-electronics')
  const [channel, setChannel] = useState<CampaignChannel>('sms')
  const [campaignName, setCampaignName] = useState('')
  const [message, setMessage] = useState('')
  const [feedback, setFeedback] = useState<{ type: 'ok' | 'error'; text: string } | null>(null)

  const {
    contacts,
    loading,
    error,
    selected,
    selectedContacts,
    toggle,
    selectAll,
    deselectAll,
    refetch,
  } = useContacts({ tenantId, companyId })

  const { sending, sendCampaign, quickSms, quickCall } = useMarketing()

  const {
    campaigns,
    selectedCampaign,
    loading: _campaignsLoading,
    selectCampaign,
    clearSelection,
  } = useAnalytics({ tenantId, companyId })

  const canSend = selectedContacts.length > 0 && message.trim().length > 0 && !sending

  async function handleSendCampaign() {
    if (!canSend) return
    setFeedback(null)
    try {
      await sendCampaign({
        channel,
        recipients: selectedContacts.map(c => c.phone),
        message: message.trim(),
        campaignName: campaignName.trim() || `${channel.toUpperCase()} Campaign`,
        tenantId,
        companyId,
      })
      setFeedback({ type: 'ok', text: 'Campaign sent successfully' })
      setMessage('')
      setCampaignName('')
      deselectAll()
      refetch()
    } catch (err) {
      setFeedback({ type: 'error', text: err instanceof Error ? err.message : 'Send failed' })
    }
  }

  async function handleQuickSms(phone: string) {
    try {
      await quickSms({
        to: phone,
        message: 'Hi! Following up on your recent interaction with us. How can we help?',
        tenantId,
        companyId,
      })
    } catch {
      // silent for quick actions
    }
  }

  async function handleQuickCall(phone: string) {
    try {
      await quickCall({ to: phone, tenantId, companyId })
    } catch {
      // silent for quick actions
    }
  }

  return (
    <main className="app-shell min-h-screen">
      <NavBar activePage="marketing" />

      <div className="mx-auto flex max-w-6xl flex-col gap-5 px-4 py-6">
        {/* Header */}
        <div>
          <p className="text-[0.65rem] text-primary uppercase tracking-[0.25em]">Marketing</p>
          <h1 className="font-display text-2xl text-foreground sm:text-3xl">Marketing Campaigns</h1>
        </div>

        {/* Loading state */}
        {loading && (
          <div className="panel-glass py-12 text-center text-muted-foreground">
            Loading contacts…
          </div>
        )}

        {/* Error state */}
        {error && !loading && (
          <div className="panel-glass border-destructive/30 py-8 text-center text-destructive">
            {error}
          </div>
        )}

        {/* Main content: contacts + composer */}
        {!loading && !error && (
          <div className="grid gap-4 lg:grid-cols-2">
            {/* Known Contacts panel */}
            <div className="panel-glass flex flex-col gap-3 p-4">
              <div className="flex items-center justify-between">
                <span className="font-semibold text-[0.65rem] text-muted-foreground uppercase tracking-[0.18em]">
                  Known Contacts
                </span>
                <div className="flex gap-1.5">
                  <button
                    type="button"
                    aria-label="Select All"
                    onClick={selectAll}
                    className="rounded border border-border/60 px-2 py-0.5 font-semibold text-[0.6rem] text-muted-foreground uppercase tracking-wider transition-colors hover:text-foreground"
                  >
                    Select All
                  </button>
                  <button
                    type="button"
                    aria-label="Clear"
                    onClick={deselectAll}
                    className="rounded border border-border/60 px-2 py-0.5 font-semibold text-[0.6rem] text-muted-foreground uppercase tracking-wider transition-colors hover:text-foreground"
                  >
                    Clear
                  </button>
                </div>
              </div>

              {contacts.length === 0 ? (
                <p className="py-8 text-center text-muted-foreground text-sm">
                  No contacts yet. Send a campaign first.
                </p>
              ) : (
                <div className="contacts-list-body flex max-h-72 flex-col gap-1 overflow-y-auto">
                  {contacts.map(contact => (
                    <div
                      key={contact.phone}
                      data-contact-row
                      className={cn(contactRowVariants({ selected: selected.has(contact.phone) }))}
                    >
                      <input
                        type="checkbox"
                        checked={selected.has(contact.phone)}
                        onChange={() => toggle(contact.phone)}
                        aria-label={`Select contact ${contact.phone}`}
                        className="accent-primary"
                      />
                      <span className="flex-1 font-mono text-foreground text-sm">
                        {contact.phone}
                      </span>
                      <span className="channel-badge rounded-full border border-border/60 px-2 py-0.5 font-bold text-[0.55rem] text-muted-foreground uppercase tracking-wider">
                        {contact.channel}
                      </span>
                      <button
                        type="button"
                        aria-label="SMS"
                        onClick={() => handleQuickSms(contact.phone)}
                        className="quick-action-btn rounded p-1 text-muted-foreground transition-colors hover:bg-primary/10 hover:text-primary"
                      >
                        <MessageSquare className="size-3.5" />
                      </button>
                      <button
                        type="button"
                        aria-label="Call"
                        onClick={() => handleQuickCall(contact.phone)}
                        className="quick-action-btn rounded p-1 text-muted-foreground transition-colors hover:bg-primary/10 hover:text-primary"
                      >
                        <Phone className="size-3.5" />
                      </button>
                    </div>
                  ))}
                </div>
              )}
            </div>

            {/* New Campaign composer */}
            <div className="panel-glass flex flex-col gap-3 p-4">
              <span className="font-semibold text-[0.65rem] text-muted-foreground uppercase tracking-[0.18em]">
                New Campaign
              </span>

              <input
                type="text"
                placeholder="Campaign name"
                value={campaignName}
                onChange={e => setCampaignName(e.target.value)}
                className="composer-input rounded-lg border border-border/60 bg-card/30 px-3 py-2 text-foreground text-sm placeholder:text-muted-foreground focus:border-primary/50 focus:outline-none"
              />

              {/* Channel toggle */}
              <div className="flex gap-1.5">
                <button
                  type="button"
                  onClick={() => setChannel('sms')}
                  className={cn(channelToggleVariants({ active: channel === 'sms' }))}
                >
                  SMS
                </button>
                <button
                  type="button"
                  onClick={() => setChannel('voice')}
                  className={cn(channelToggleVariants({ active: channel === 'voice' }))}
                >
                  Voice
                </button>
              </div>

              <textarea
                aria-label="Campaign message"
                placeholder="Message"
                rows={3}
                value={message}
                onChange={e => setMessage(e.target.value)}
                className="composer-input rounded-lg border border-border/60 bg-card/30 px-3 py-2 text-foreground text-sm placeholder:text-muted-foreground focus:border-primary/50 focus:outline-none"
              />

              <p className="text-[0.65rem] text-muted-foreground">
                {selectedContacts.length} recipient{selectedContacts.length !== 1 ? 's' : ''}{' '}
                selected
              </p>

              <button
                type="button"
                disabled={!canSend}
                onClick={handleSendCampaign}
                className="rounded-lg bg-primary px-4 py-2 font-semibold text-primary-foreground text-sm transition-opacity disabled:opacity-40"
              >
                Send Campaign
              </button>

              {feedback && (
                <div
                  className={cn(
                    'mkt-feedback rounded-lg px-3 py-2 text-sm',
                    feedback.type === 'ok'
                      ? 'bg-primary/10 text-primary'
                      : 'bg-destructive/10 text-destructive',
                  )}
                >
                  {feedback.type === 'ok' ? '✓ ' : '✗ '}
                  {feedback.text}
                </div>
              )}
            </div>
          </div>
        )}

        {/* Active Campaigns (reused from analytics) */}
        {!loading && campaigns.length > 0 && (
          <>
            <div className="mt-2">
              <span className="font-semibold text-[0.65rem] text-muted-foreground uppercase tracking-[0.18em]">
                Active Campaigns
              </span>
            </div>
            <CampaignTable
              campaigns={campaigns}
              selectedId={selectedCampaign?.campaign_id}
              onSelect={selectCampaign}
            />
          </>
        )}

        {selectedCampaign && (
          <CampaignDetail campaign={selectedCampaign} onClose={clearSelection} />
        )}
      </div>
    </main>
  )
}
