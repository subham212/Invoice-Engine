export type SseEvent = {
  event: string
  data: Record<string, unknown>
}

export type ProcessResponse = {
  run_id: string
  status: string
  invoice?: Record<string, unknown> | null
  decision?: Record<string, unknown> | null
  trace?: Record<string, unknown> | null
  extracted_fields?: Record<string, unknown> | null
  extraction_metadata?: ExtractionMetadata | null
  human_decision?: 'APPROVED_OVERRIDE' | 'REJECTED_OVERRIDE' | null
  reviewer_name?: string | null
  reviewer_note?: string | null
  reviewed_at?: string | null
  display_id?: string | null
  vendor_display?: string | null
  total_amount?: number | null
}

export type ExtractionMetadata = {
  extraction_method: 'cf_workers_ai_vision' | 'cf_workers_ai_text' | 'pdfplumber_fallback'
  model_used?: string | null
  extraction_latency_ms: number
  is_scanned_document: boolean
}

const configuredApiUrl = (import.meta.env.VITE_API_BASE_URL as string | undefined)?.replace(/\/$/, '')
const isLoopbackApiUrl = configuredApiUrl && /^https?:\/\/(localhost|127\.0\.0\.1)(:\d+)?$/i.test(configuredApiUrl)
const API_BASE_URL = import.meta.env.PROD
  ? configuredApiUrl && !isLoopbackApiUrl ? configuredApiUrl : 'https://invoice-engine-api.onrender.com'
  : configuredApiUrl ?? 'http://localhost:8000'

export const apiBaseUrl = API_BASE_URL

export type DashboardStats = {
  total_runs: number
  approved: number
  approved_with_warning: number
  manual_review: number
  rejected: number
  total_amount: number
  total_by_currency?: Record<string, number>
  average_confidence?: number | null
  by_status: Record<string, number>
  as_of?: string | null
}

export type HistoryRun = ProcessResponse & {
  id?: string | null
  scenario_key?: string | null
  created_at?: string | null
}

export type PurchaseOrder = {
  id: string
  po_number: string
  vendor_id: string
  total_amount: number
  amount_invoiced: number
  currency: string
  status: string
  created_at?: string | null
  line_items?: unknown[]
}

async function apiGet<T>(path: string): Promise<T> {
  let response: Response
  try {
    response = await fetch(`${API_BASE_URL}${path}`)
  } catch {
    throw new Error('The API is unavailable. Check your connection and try again.')
  }
  if (!response.ok) throw new Error(`Request failed (${response.status})`)
  return response.json() as Promise<T>
}

export const getStats = () => apiGet<DashboardStats>('/api/stats')
export const getHistory = () => apiGet<HistoryRun[]>('/api/history?limit=200')
export const getPurchaseOrders = () => apiGet<PurchaseOrder[]>('/api/purchase-orders')
export async function resetDatabase() {
  const response = await fetch(`${API_BASE_URL}/api/reset`, { method: 'POST' })
  if (!response.ok) throw new Error(`Reset failed (${response.status})`)
}

export async function submitReview(runId: string, action: 'APPROVE' | 'REJECT', reviewerName: string, reviewerNote: string) {
  let response: Response
  try {
    response = await fetch(`${API_BASE_URL}/api/runs/${encodeURIComponent(runId)}/review`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action, reviewer_name: reviewerName, reviewer_note: reviewerNote }),
    })
  } catch {
    throw new Error('The API is unavailable. Check your connection and try again.')
  }
  if (!response.ok) {
    const body = await response.json().catch(() => ({})) as { detail?: string }
    throw new Error(body.detail ?? `Review could not be saved (${response.status})`)
  }
  return response.json() as Promise<ProcessResponse>
}

export function formatCurrency(amount: unknown, currency = 'USD') {
  try {
    return new Intl.NumberFormat(undefined, { style: 'currency', currency, maximumFractionDigits: 2 }).format(Number(amount ?? 0))
  } catch {
    return `${currency} ${Number(amount ?? 0).toLocaleString(undefined, { maximumFractionDigits: 2 })}`
  }
}

export function formatDate(value: string | null | undefined) {
  if (!value) return '—'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric', year: 'numeric', timeZone: 'UTC' }).format(date)
}

export async function streamInvoice(file: File | null, scenarioKey: string, onEvent: (event: SseEvent) => void, signal: AbortSignal) {
  const form = new FormData()
  if (file) form.append('file', file)
  if (!file) form.append('scenario', scenarioKey)
  let response: Response
  try {
    response = await fetch(`${API_BASE_URL}/api/process`, { method: 'POST', body: form, signal })
  } catch {
    throw new Error('The API is unavailable. Check your connection and try again.')
  }
  if (!response.ok || !response.body) throw new Error(`Run could not start (${response.status})`)

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  try {
    while (true) {
      const { done, value } = await reader.read()
      buffer += decoder.decode(value ?? new Uint8Array(), { stream: !done })
      const frames = buffer.split('\n\n')
      buffer = frames.pop() ?? ''
      for (const frame of frames) {
        const lines = frame.split('\n')
        const event = lines.find((line) => line.startsWith('event:'))?.slice(6).trim() ?? 'message'
        const payload = lines.filter((line) => line.startsWith('data:')).map((line) => line.slice(5).trim()).join('\n')
        if (payload) {
          const data = JSON.parse(payload) as Record<string, unknown>
          onEvent({ event, data })
        }
      }
      if (done) break
    }
  } finally {
    reader.releaseLock()
  }
}
