import { useEffect, useRef, useState } from 'react'
import { BrowserRouter, Link, NavLink, Route, Routes, useLocation, useNavigate } from 'react-router-dom'
import { Check, ChevronRight, CircleDot, FileText, LayoutDashboard, Menu, Play, ShieldCheck, Upload, X, AlertTriangle, LoaderCircle, Square, Download, ChevronLeft, RefreshCw, Database, PanelLeftClose, PanelLeftOpen } from 'lucide-react'
import { getDocument, GlobalWorkerOptions } from 'pdfjs-dist'
import { apiBaseUrl, formatCurrency, formatDate, getHistory, getPurchaseOrders, getStats, resetDatabase, streamInvoice, submitReview, type DashboardStats, type HistoryRun, type ProcessResponse, type PurchaseOrder, type SseEvent } from './services'
import 'pdfjs-dist/web/pdf_viewer.css'

GlobalWorkerOptions.workerSrc = new URL('pdfjs-dist/build/pdf.worker.min.mjs', import.meta.url).toString()

const scenarios = [
  { key: 'happy_path', name: 'Happy path', description: 'Clean invoice against its purchase order.', tone: 'teal' },
  { key: 'partial_billing', name: 'Partial billing', description: 'Invoice covering part of an open PO.', tone: 'blue' },
  { key: 'price_creep', name: 'Price creep', description: 'Surface a unit price variance.', tone: 'amber' },
  { key: 'duplicate', name: 'Duplicate invoice', description: 'Identify a repeated invoice in the ledger.', tone: 'rose' },
  { key: 'missing_po', name: 'Missing PO', description: 'Route an invoice without a resolvable PO.', tone: 'violet' },
  { key: 'scanned_invoice', name: 'Scanned PDF', description: 'Use vision extraction on a scanned invoice image.', tone: 'blue' },
]
const stages = ['Intake', 'Extraction', 'Vendor Guard', 'PO Matching', 'Financial Validation', 'Decision']
type StageState = 'pending' | 'running' | 'passed' | 'warning' | 'failed'
type Stage = { state: StageState; progress: number; logs: string[] }
const initialStages = (): Record<string, Stage> => Object.fromEntries(stages.map((stage) => [stage, { state: 'pending', progress: 0, logs: [] }]))

type AnyRecord = Record<string, any>
function Navigation({ open, setOpen, collapsed, setCollapsed }: { open: boolean; setOpen: (open: boolean) => void; collapsed: boolean; setCollapsed: (collapsed: boolean) => void }) {
  const links = [{ to: '/', label: 'Live Run', icon: Play }, { to: '/dashboard', label: 'Dashboard', icon: LayoutDashboard }]
  return <aside className={`fixed inset-y-0 left-0 z-30 ${collapsed ? 'w-[76px]' : 'w-[220px]'} border-r border-[#E6E7E2] bg-[#F6F7F3] px-3 py-4 transition-[width,transform] duration-200 lg:translate-x-0 ${open ? 'translate-x-0' : '-translate-x-full'}`}>
    <div className="flex h-12 items-center justify-between px-2">
      <Link to="/" className={`flex min-w-0 items-center gap-2.5 rounded-md text-[#111312] ${collapsed ? 'justify-center' : ''}`} title="Zamp AP">
        <img src="/favicon.svg" alt="Zamp AP" className="size-7 shrink-0 rounded-md" />
        {!collapsed && <span className="truncate text-base font-semibold tracking-[-.02em]">Zamp AP</span>}
      </Link>
      <div className="flex items-center">
        <button className="hidden rounded-md p-2 text-[#7A7E78] hover:bg-[#EEF0E9] hover:text-[#111312] lg:block" onClick={() => setCollapsed(!collapsed)} aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'} title={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}>{collapsed ? <PanelLeftOpen className="size-[18px]" /> : <PanelLeftClose className="size-[18px]" />}</button>
        <button className="rounded-md p-2 text-[#7A7E78] hover:bg-[#EEF0E9] hover:text-[#111312] lg:hidden" onClick={() => setOpen(false)} aria-label="Close navigation"><X className="size-[18px]" /></button>
      </div>
    </div>
    <nav aria-label="Main navigation" className="mt-5 space-y-1">
      {links.map(({ to, label, icon: Icon }) => <NavLink key={to} to={to} end title={collapsed ? label : undefined} className={({ isActive }) => `flex h-10 items-center ${collapsed ? 'justify-center px-0' : 'gap-3 px-3'} rounded-[10px] text-sm font-medium transition-colors ${isActive ? 'bg-[#C5F084] text-[#111312]' : 'text-[#303630] hover:bg-[#EEF0E9]'}`}>
        <Icon className="size-[18px] shrink-0" strokeWidth={1.8} />{!collapsed && label}
      </NavLink>)}
    </nav>
    {!collapsed && <div className="absolute bottom-4 left-4 right-4 border-t border-[#E6E7E2] pt-4"><div className="flex items-center gap-2.5 px-2 text-xs text-[#7A7E78]"><ShieldCheck className="size-4" />Traceable by default</div></div>}
  </aside>
}
function Shell() {
  const [healthy, setHealthy] = useState<boolean | null>(null)
  const [navOpen, setNavOpen] = useState(false)
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false)
  const location = useLocation()
  const navigate = useNavigate()
  useEffect(() => { fetch(`${apiBaseUrl}/api/health`).then((r) => setHealthy(r.ok)).catch(() => setHealthy(false)) }, [])
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.target instanceof HTMLInputElement || event.target instanceof HTMLTextAreaElement || event.target instanceof HTMLSelectElement) return
      if (event.key.toLowerCase() === 'd') navigate('/dashboard')
      if (event.key.toLowerCase() === 'r') navigate('/')
      const shortcut = Number(event.key)
      if (shortcut >= 1 && shortcut <= scenarios.length) { navigate('/'); window.dispatchEvent(new CustomEvent('scenario-shortcut', { detail: shortcut - 1 })) }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [navigate])
  return <div className="min-h-screen bg-[#F3F4EF] text-[#111312]">
    <Navigation open={navOpen} setOpen={setNavOpen} collapsed={sidebarCollapsed} setCollapsed={setSidebarCollapsed} />
    <button className="fixed left-4 top-4 z-20 rounded-lg border border-[#E6E7E2] bg-[#F6F7F3] p-2 text-[#4B4F4A] lg:hidden" onClick={() => setNavOpen(true)} aria-label="Open navigation"><Menu className="size-5" /></button>
    <main className={`min-h-screen bg-[#F3F4EF] transition-[margin] duration-200 ${sidebarCollapsed ? 'lg:ml-[76px]' : 'lg:ml-[220px]'}`}>
      <header className="flex h-16 items-center justify-between border-b border-[#E6E7E2] bg-[#F6F7F3] px-6 pl-16 sm:px-8 lg:px-10">
        <div className="text-sm text-[#7A7E78]">Accounts payable <ChevronRight className="mx-1 inline size-3 text-[#a5aaa2]" /> <span className="text-[#303630]">{location.pathname === '/dashboard' ? 'Dashboard' : 'Live Run'}</span></div>
        <div className="flex items-center gap-2 text-xs text-[#7A7E78]">API {healthy === null ? 'checking' : healthy ? 'operational' : 'offline'} <span className={`size-2 rounded-full ${healthy ? 'bg-[#62ad62]' : healthy === false ? 'bg-[#c76d65]' : 'bg-[#d2a047]'}`} /></div>
      </header>
      <div className="mx-auto max-w-[1440px] px-6 py-8 sm:px-8 lg:px-10"><Routes><Route path="/" element={<LiveRun />} /><Route path="/dashboard" element={<Dashboard />} /></Routes></div>
    </main>
  </div>
}

function PdfPreview({ source, title, file }: { source: string | null; title: string; file?: File | null }) {
  const canvasRef = useRef<HTMLCanvasElement>(null); const [page, setPage] = useState(1); const [pages, setPages] = useState(0); const [state, setState] = useState<'idle' | 'loading' | 'ready' | 'error'>('idle')
  const isImage = Boolean(file?.type.startsWith('image/') || /\.(png|jpe?g|webp)$/i.test(title))
  useEffect(() => { let cancelled = false; if (!source || isImage) { setState(source ? 'ready' : 'idle'); setPages(0); return }; setPage(1); setState('loading'); getDocument({ url: source }).promise.then(async (pdf) => { if (cancelled) return; setPages(pdf.numPages); const current = await pdf.getPage(1); const viewport = current.getViewport({ scale: 1.25 }); const canvas = canvasRef.current; if (!canvas) return; canvas.width = viewport.width; canvas.height = viewport.height; await current.render({ canvas, canvasContext: canvas.getContext('2d')!, viewport }).promise; if (!cancelled) setState('ready') }).catch(() => { if (!cancelled) setState('error') }); return () => { cancelled = true } }, [source, isImage])
  useEffect(() => { if (!source || isImage || page === 1 || !canvasRef.current) return; getDocument({ url: source }).promise.then(async (pdf) => { const current = await pdf.getPage(page); const viewport = current.getViewport({ scale: 1.25 }); const canvas = canvasRef.current!; canvas.width = viewport.width; canvas.height = viewport.height; await current.render({ canvas, canvasContext: canvas.getContext('2d')!, viewport }).promise }).catch(() => setState('error')) }, [page, source, isImage])
  return <section className="rounded-2xl border border-white/10 bg-white/[.025] p-5"><div className="flex items-center justify-between"><div><p className="text-[10px] uppercase tracking-[.2em] text-teal-300">Document preview</p><h2 className="mt-2 font-medium text-white">{title}</h2></div><FileText className="size-5 text-slate-500" /></div><div className="mt-4 flex min-h-80 items-center justify-center overflow-auto rounded-xl bg-slate-950/70 p-3">{state === 'loading' && <LoaderCircle className="size-7 animate-spin text-teal-300" />}{state === 'error' && <p role="alert" className="text-sm text-rose-300">This document could not be previewed in the browser.</p>}{!source && state === 'idle' && <p className="text-sm text-slate-600">Select a scenario or upload an invoice to preview it.</p>}{source && isImage && <img src={source} alt={`Preview of ${title}`} className="h-auto max-w-full shadow-2xl" />}{!isImage && <canvas ref={canvasRef} className={state === 'ready' ? 'max-w-full shadow-2xl' : 'hidden'} aria-label={`Page ${page} of ${pages}`} />}</div>{!isImage && pages > 0 && <div className="mt-3 flex items-center justify-between text-xs text-slate-400"><button disabled={page <= 1} onClick={() => setPage((value) => value - 1)} className="flex items-center gap-1 rounded-lg border border-white/10 px-2 py-1 disabled:opacity-30"><ChevronLeft className="size-3" /> Previous</button><span>Page {page} of {pages}</span><button disabled={page >= pages} onClick={() => setPage((value) => value + 1)} className="flex items-center gap-1 rounded-lg border border-white/10 px-2 py-1 disabled:opacity-30">Next <ChevronRight className="size-3" /></button></div>}</section>
}

function Field({ label, field }: { label: string; field: AnyRecord | undefined }) {
  const missing = !field || field.value === null || field.value === undefined || field.value === ''
  const confidence = Number(field?.confidence ?? 0)
  const confidenceStyle = confidence >= 85 ? 'bg-[#DCFCE7] text-[#15803D]' : confidence >= 60 ? 'bg-[#FEF3C7] text-[#805f1e]' : 'bg-[#FEE2E2] text-[#B91C1C]'
  return <div className="min-w-0 rounded-xl border border-[#EAECE6] bg-[#F6F7F4] p-3.5">
    <div className="flex items-center justify-between gap-2"><span className="text-[11px] font-medium text-[#7A7E78]">{label}</span>{!missing && <span className={`shrink-0 rounded-md px-1.5 py-0.5 text-[10px] font-semibold tabular-nums ${confidenceStyle}`}>{confidence.toFixed(0)}%</span>}</div>
    <p className={`mt-2 break-words text-sm font-medium ${missing ? 'text-[#92978f]' : 'text-[#111312]'}`}>{missing ? '—' : label.toLowerCase().includes('date') ? formatDate(String(field.value)) : String(field.value)}</p>
  </div>
}

function ReviewPanel({ runId, onReviewed }: { runId: string; onReviewed: (run: ProcessResponse) => void }) {
  const [name, setName] = useState('AP Manager'); const [note, setNote] = useState(''); const [busy, setBusy] = useState(false); const [error, setError] = useState('')
  const review = async (action: 'APPROVE' | 'REJECT') => { setBusy(true); setError(''); try { onReviewed(await submitReview(runId, action, name, note)) } catch (cause) { setError((cause as Error).message) } finally { setBusy(false) } }
  return <div className="rounded-[14px] border border-[#e8d96f] bg-[#FBF07B] p-4"><div><h3 className="font-semibold text-[#111312]">AP manager review</h3><p className="mt-1 text-sm text-[#5C5518]">Record the reason for an approval override or rejection</p></div><div className="mt-4 grid gap-3 sm:grid-cols-2"><label className="text-xs font-medium text-[#514b1b]">Reviewer name<input value={name} onChange={(event) => setName(event.target.value)} className="mt-1 w-full rounded-lg border border-[#e3d978] bg-white px-3 py-2 text-sm text-[#111312]" /></label><label className="text-xs font-medium text-[#514b1b]">Audit note<input value={note} onChange={(event) => setNote(event.target.value)} required className="mt-1 w-full rounded-lg border border-[#e3d978] bg-white px-3 py-2 text-sm text-[#111312]" placeholder="Why is this override appropriate?" /></label></div>{error && <p role="alert" className="mt-3 text-sm text-[#B91C1C]">{error}</p>}<div className="mt-4 flex flex-wrap gap-2"><button disabled={busy || !name.trim() || !note.trim()} onClick={() => void review('APPROVE')} className="rounded-lg bg-[#C5F084] px-3 py-2 text-sm font-semibold text-[#111312] hover:bg-[#B7E86E] disabled:opacity-40">Approve with override</button><button disabled={busy || !name.trim() || !note.trim()} onClick={() => void review('REJECT')} className="rounded-lg border border-[#e0a1a1] bg-[#FEE2E2] px-3 py-2 text-sm font-medium text-[#B91C1C] disabled:opacity-40">Reject</button></div></div>
}
function RunResult({ result, onReviewed }: { result: ProcessResponse; onReviewed: (run: ProcessResponse) => void }) {
  const invoice = result.invoice as AnyRecord | undefined; const extracted = result.extracted_fields as AnyRecord | undefined; const decision = result.decision as AnyRecord | undefined; const trace = result.trace as AnyRecord | undefined; const metadata = result.extraction_metadata
  const lines = (invoice?.line_items ?? extracted?.line_items ?? []) as AnyRecord[]; const reconciliation = ((trace?.stages ?? []) as AnyRecord[]).find((stage) => stage.stage === 'reconciliation')?.output?.lines ?? []
  const verdict = result.human_decision === 'APPROVED_OVERRIDE' ? 'APPROVED' : result.human_decision === 'REJECTED_OVERRIDE' ? 'REJECTED' : String(decision?.decision ?? 'MANUAL_REVIEW')
  const currency = invoice?.totals?.currency ?? extracted?.currency ?? 'USD'; const confidences = (extracted?.field_confidences ?? {}) as AnyRecord
  const field = (key: string, value: unknown) => ({ value, confidence: Number(confidences[key] ?? 1) * 100 })
  const download = () => {
    const audit = {
      run_id: result.run_id,
      extraction_method: metadata?.extraction_method ?? null,
      model_used: metadata?.model_used ?? null,
      extraction_latency_ms: metadata?.extraction_latency_ms ?? null,
      is_scanned_document: metadata?.is_scanned_document ?? false,
      currency,
      extracted_invoice: invoice ?? extracted ?? null,
      checks: decision?.validation_checks ?? [],
      decision: verdict,
      executive_summary: executiveSummary,
      human_review: trace?.human_review ?? null,
      trace,
    }
    const blob = new Blob([JSON.stringify(audit, null, 2)], { type: 'application/json' }); const url = URL.createObjectURL(blob); const anchor = document.createElement('a'); anchor.href = url; anchor.download = `invoice-run-${result.run_id}.json`; anchor.click(); URL.revokeObjectURL(url)
  }
  const executiveSummary = decision?.executive_summary ?? decision?.reason ?? 'No explanation returned.'
  const verdictStyle = verdict === 'APPROVED' ? 'bg-[#DCFCE7] text-[#15803D]' : verdict === 'REJECTED' ? 'bg-[#FEE2E2] text-[#B91C1C]' : verdict === 'MANUAL_REVIEW' ? 'bg-[#F5B169] text-[#30220f]' : verdict.includes('WARNING') ? 'bg-[#DBEAFE] text-[#244466]' : 'bg-[#EFE38A] text-[#3f3a15]'
  const checks = (decision?.validation_checks ?? []) as AnyRecord[]
  const checkTone = (status: string) => status === 'pass' ? 'bg-[#DCFCE7] text-[#15803D]' : status === 'warn' ? 'bg-[#DBEAFE] text-[#244466]' : 'bg-[#FEE2E2] text-[#B91C1C]'
  return <section className="run-result space-y-4">
    <div className="called-card flex flex-wrap items-start justify-between gap-4 p-5">
      <div className="min-w-0 flex-1"><div className="flex flex-wrap items-center gap-2"><span className="text-xs font-medium text-[#7A7E78]">Decision</span><span className={`rounded-md px-2.5 py-1 text-xs font-semibold ${verdictStyle}`}>{verdict.replaceAll('_', ' ')}</span>{result.human_decision && <span className="rounded-md bg-[#EFE38A] px-2 py-1 text-[11px] font-medium text-[#3f3a15]">Human override</span>}</div><p className="mt-3 max-w-2xl text-sm leading-6 text-[#4B4F4A]">{String(executiveSummary)}</p>{result.reviewer_name && <p className="mt-2 text-xs text-[#7A7E78]">Reviewed by {result.reviewer_name}{result.reviewed_at ? ` · ${formatDate(result.reviewed_at)}` : ''}{result.reviewer_note ? ` · ${result.reviewer_note}` : ''}</p>}</div>
      <button onClick={download} className="flex shrink-0 items-center gap-2 rounded-lg border border-[#E6E7E2] bg-white px-3 py-2 text-sm font-medium text-[#303630] hover:bg-[#F6F7F4]"><Download className="size-4" />Export audit JSON</button>
    </div>
    {metadata && <div className="flex flex-wrap items-center gap-2"><span className="rounded-md border border-[#E6E7E2] bg-white px-3 py-1.5 text-xs text-[#4B4F4A]">{metadata.extraction_method === 'cf_workers_ai_vision' ? 'Cloudflare Vision AI' : metadata.extraction_method === 'cf_workers_ai_text' ? 'Cloudflare AI' : 'PDF text fallback'}{metadata.model_used ? ` · ${metadata.model_used.split('/').at(-1)}` : ''} · {(metadata.extraction_latency_ms / 1000).toFixed(1)}s</span>{metadata.is_scanned_document && <span className="rounded-md bg-[#FCE1F8] px-2.5 py-1.5 text-xs text-[#644560]">Scanned image</span>}</div>}
    {result.human_decision ? <div className="rounded-xl border border-[#EAECE6] bg-[#F6F7F4] p-4 text-sm text-[#4B4F4A]">Review recorded · {result.reviewer_name} · {result.reviewer_note}</div> : verdict === 'MANUAL_REVIEW' && <ReviewPanel runId={result.run_id} onReviewed={onReviewed} />}
    <div className="grid items-start gap-4 xl:grid-cols-2">
      <section className="called-card p-5"><div className="flex items-baseline justify-between gap-3"><div><h3 className="text-base font-bold text-[#111312]">Extracted invoice</h3><p className="mt-1 text-xs text-[#7A7E78]">Field values and extraction confidence</p></div><span className="text-xs tabular-nums text-[#7A7E78]">{(decision?.confidence ?? '—')}% match confidence</span></div>
        <div className="mt-4 grid grid-cols-2 gap-2.5"><Field label="Invoice number" field={field('invoice_number', invoice?.invoice_number ?? extracted?.invoice_number)} /><Field label="Vendor" field={field('vendor_name', invoice?.vendor_name ?? extracted?.vendor_name)} /><Field label="Invoice date" field={field('invoice_date', invoice?.invoice_date ?? extracted?.invoice_date)} /><Field label="Due date" field={field('due_date', invoice?.due_date ?? extracted?.due_date)} /><Field label="PO number" field={field('po_number', invoice?.purchase_order_number ?? extracted?.po_number)} /><Field label="Tax ID" field={field('vendor_tax_id', invoice?.vendor_tax_id ?? extracted?.vendor_tax_id)} /></div>
        <div className="mt-4 grid grid-cols-3 gap-2 border-t border-[#EFEFEB] pt-4 text-xs"><div><p className="text-[#7A7E78]">Subtotal</p><p className="mt-1 font-semibold tabular-nums text-[#303630]">{invoice?.totals?.subtotal == null ? '—' : formatCurrency(invoice.totals.subtotal, currency)}</p></div><div><p className="text-[#7A7E78]">Tax</p><p className="mt-1 font-semibold tabular-nums text-[#303630]">{invoice?.totals?.tax == null ? '—' : formatCurrency(invoice.totals.tax, currency)}</p></div><div><p className="text-[#7A7E78]">Grand total</p><p className="mt-1 font-bold tabular-nums text-[#111312]">{invoice?.totals?.total == null ? '—' : formatCurrency(invoice.totals.total, currency)}</p></div></div>
      </section>
      <section className="called-card p-5"><div className="flex items-baseline justify-between gap-3"><div><h3 className="text-base font-bold text-[#111312]">Validation checks</h3><p className="mt-1 text-xs text-[#7A7E78]">Why this invoice received its decision</p></div><span className="text-xs text-[#7A7E78]">{checks.length} checks</span></div>
        <div className="mt-4 space-y-2">{checks.length ? checks.map((check, index) => <div key={`${check.name}-${index}`} className="flex items-start gap-3 rounded-xl bg-[#F6F7F4] px-3.5 py-3"><span className={`mt-0.5 rounded-md px-1.5 py-0.5 text-[10px] font-semibold ${checkTone(String(check.status))}`}>{String(check.status).toUpperCase()}</span><div className="min-w-0"><p className="text-[13px] font-semibold text-[#303630]">{String(check.name).replaceAll('_', ' ')}</p><p className="mt-1 text-xs leading-5 text-[#737870]">{String(check.message)}</p></div></div>) : <p className="rounded-xl bg-[#F6F7F4] p-4 text-sm text-[#7A7E78]">No validation checks were returned</p>}</div>
      </section>
    </div>
    <section className="called-card p-5"><div className="flex flex-wrap items-baseline justify-between gap-2"><div><h3 className="text-base font-bold text-[#111312]">Line-item reconciliation</h3><p className="mt-1 text-xs text-[#7A7E78]">Invoice lines compared with purchase order lines</p></div><span className="text-xs text-[#7A7E78]">{lines.length} line items</span></div>
      <div className="mt-4 overflow-x-auto"><table className="w-full min-w-[600px] text-left text-sm"><thead className="text-[11px] text-[#7A7E78]"><tr><th className="pb-3 font-medium">Description</th><th className="pb-3 text-right font-medium">Qty</th><th className="pb-3 text-right font-medium">Unit price</th><th className="pb-3 text-right font-medium">Amount</th><th className="pb-3 pl-5 font-medium">Status</th></tr></thead><tbody>{lines.length ? lines.map((line, index) => { const rec = reconciliation[index] as AnyRecord | undefined; const state = String(rec?.status ?? 'not matched'); const statusClass = state === 'pass' ? 'bg-[#DCFCE7] text-[#15803D]' : state === 'warn' ? 'bg-[#DBEAFE] text-[#244466]' : state === 'fail' ? 'bg-[#FEE2E2] text-[#B91C1C]' : 'bg-[#F6F7F4] text-[#7A7E78]'; return <tr key={index} className="border-t border-[#EFEFEB]"><td className="py-3 font-medium text-[#303630]">{line.description}</td><td className="py-3 text-right tabular-nums text-[#4B4F4A]">{line.quantity}</td><td className="py-3 text-right tabular-nums text-[#4B4F4A]">{formatCurrency(line.unit_price, currency)}</td><td className="py-3 text-right tabular-nums text-[#4B4F4A]">{line.amount == null ? '—' : formatCurrency(line.amount, currency)}</td><td className="py-3 pl-5"><span className={`rounded-md px-2 py-1 text-[10px] font-medium ${statusClass}`}>{state.replaceAll('_', ' ')}</span></td></tr>}) : <tr><td colSpan={5} className="py-7 text-center text-sm text-[#7A7E78]">No line items were extracted</td></tr>}</tbody></table></div>
    </section>
  </section>
}
function LiveRunScreen({ selected, setSelected, file, setFile, preview, run, error, active, setActive, stageMap, result, setResult, start, cancel }: {
  selected: string; setSelected: (key: string) => void; file: File | null; setFile: (file: File | null) => void; preview: string | null; run: boolean; error: string
  active: string; setActive: (stage: string) => void; stageMap: Record<string, Stage>; result: ProcessResponse | null; setResult: (result: ProcessResponse) => void
  start: () => void; cancel: () => void
}) {
  const metadata = result?.extraction_metadata
  return <div className="live-run-page space-y-5">
    <section className="called-card p-5 sm:p-6">
      <div className="flex flex-wrap items-center justify-between gap-3"><div><h1 className="text-2xl font-bold tracking-[-.03em] text-[#111312]">Live Run</h1><p className="mt-1 text-sm text-[#7A7E78]">Process an invoice and review each decision</p></div><div className="flex flex-wrap gap-2">{metadata?.model_used && <span className="rounded-md bg-[#DBEAFE] px-2.5 py-1.5 text-xs text-[#244466]">{metadata.model_used.split('/').at(-1)}</span>}{metadata?.is_scanned_document && <span className="rounded-md bg-[#FCE1F8] px-2.5 py-1.5 text-xs text-[#644560]">Scanned document</span>}</div></div>
      <div className="mt-4 flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap gap-1 rounded-xl bg-[#F3F4EF] p-1">{scenarios.map((scenario) => <button key={scenario.key} disabled={run} onClick={() => { setSelected(scenario.key); setFile(null) }} className={`rounded-lg border px-3 py-2 text-sm transition-colors disabled:opacity-50 ${selected === scenario.key && !file ? 'border-[#E6E7E2] bg-white font-semibold text-[#111312]' : 'border-transparent text-[#4B4F4A] hover:bg-white/70'}`}>{scenario.name}</button>)}</div>
        <div className="flex items-center gap-2"><label className={`flex h-10 cursor-pointer items-center gap-2 rounded-lg border bg-white px-3 text-sm ${file ? 'border-[#8bbd50] text-[#303630]' : 'border-[#E6E7E2] text-[#4B4F4A]'}`}><Upload className="size-4" />{file ? file.name : 'Upload invoice'}<input className="sr-only" type="file" accept="application/pdf,.pdf,image/png,.png,image/jpeg,.jpg,.jpeg,image/webp,.webp" disabled={run} onChange={(event) => { setFile(event.target.files?.[0] ?? null); if (event.target.files?.[0]) setSelected('custom') }} /></label><button onClick={run ? cancel : start} className={`flex h-10 items-center gap-2 rounded-lg px-4 text-sm font-semibold text-[#111312] ${run ? 'border border-[#E6E7E2] bg-white' : 'bg-[#C5F084] hover:bg-[#B7E86E]'}`}>{run ? <><Square className="size-3" />Cancel</> : <><Play className="size-4" />Run invoice</>}</button></div>
      </div>
      <p className="mt-3 text-xs text-[#7A7E78]">{file ? `Selected document · ${file.type || 'Invoice file'}` : scenarios.find((scenario) => scenario.key === selected)?.description}</p>
    </section>
    {error && <div role="alert" className="flex items-center gap-2 rounded-xl border border-[#FCA5A5] bg-[#FEE2E2] p-3 text-sm text-[#B91C1C]"><AlertTriangle className="size-4" />{error}</div>}
    <section className="called-card p-5 sm:p-6">
      <div className="flex flex-wrap items-center justify-between gap-3"><div><h2 className="text-[19px] font-bold tracking-[-.02em] text-[#111312]">Processing pipeline</h2><p className="mt-1 text-xs text-[#7A7E78]">Six checks from intake through decision</p></div><span className={`rounded-md px-2.5 py-1.5 text-xs font-medium ${run ? 'bg-[#DBEAFE] text-[#244466]' : result ? 'bg-[#DCFCE7] text-[#15803D]' : 'bg-[#F6F7F4] text-[#4B4F4A]'}`}>{run ? `Running · ${active}` : result ? 'Completed' : 'Ready'}</span></div>
      <div className="mt-5 grid grid-cols-2 gap-2 sm:grid-cols-3 xl:grid-cols-6">{stages.map((stage, index) => { const item = stageMap[stage]; const activeStage = active === stage; const stateTone = item.state === 'passed' ? 'text-[#15803D]' : item.state === 'warning' ? 'text-[#8b671d]' : item.state === 'failed' ? 'text-[#B91C1C]' : 'text-[#7A7E78]'; return <button key={stage} onClick={() => setActive(stage)} aria-current={activeStage ? 'step' : undefined} className={`relative min-h-[94px] rounded-xl border p-3 text-left transition-colors ${activeStage ? 'border-[#A9CF70] bg-[#F6FBEF]' : 'border-[#E6E7E2] bg-[#F6F7F4] hover:bg-[#EEF0E9]'}`}><span className="flex items-center justify-between"><span className="text-[11px] font-semibold text-[#111312]">{index + 1}. {stage}</span>{item.state === 'running' ? <LoaderCircle className="size-4 animate-spin text-[#78a93e]" /> : item.state === 'passed' ? <Check className="size-4 text-[#27824b]" /> : item.state === 'warning' ? <AlertTriangle className="size-4 text-[#a87820]" /> : item.state === 'failed' ? <X className="size-4 text-[#b94444]" /> : <CircleDot className="size-4 text-[#a4a9a1]" />}</span><span className={`mt-2 block truncate text-[11px] ${stateTone}`}>{item.logs.at(-1) ?? (item.state === 'pending' ? 'Waiting' : item.state)}</span><span className="absolute inset-x-3 bottom-2 h-1 overflow-hidden rounded-full bg-[#E6E7E2]"><span className={`block h-full ${item.state === 'failed' ? 'bg-[#c76d65]' : item.state === 'warning' ? 'bg-[#d9ad54]' : 'bg-[#88bd50]'}`} style={{ width: `${item.progress}%` }} /></span></button>})}</div>
      <div className="mt-4 flex items-start gap-3 border-t border-[#EFEFEB] pt-4"><FileText className="mt-0.5 size-4 text-[#7A7E78]" /><div aria-live="polite"><p className="text-xs font-semibold text-[#303630]">{active}</p>{stageMap[active].logs.length ? stageMap[active].logs.map((log, index) => <p key={`${log}-${index}`} className="mt-1 text-xs text-[#7A7E78]">{log}</p>) : <p className="mt-1 text-xs text-[#7A7E78]">Stage details will appear here as the run progresses</p>}</div></div>
    </section>
    <div className="grid items-start gap-4 lg:grid-cols-2"><PdfPreview source={preview} file={file} title={file?.name ?? `${scenarios.find((scenario) => scenario.key === selected)?.name ?? 'Invoice'} sample`} />{result ? <RunResult result={result} onReviewed={(updated) => setResult(updated)} /> : <section className="called-card flex min-h-[220px] items-center justify-center p-8 text-center"><div><FileText className="mx-auto size-7 text-[#a1a69d]" /><p className="mt-3 text-sm font-medium text-[#303630]">Invoice details will appear here</p><p className="mt-1 text-xs text-[#7A7E78]">Choose a scenario or upload a file, then run the checks</p></div></section>}</div>
  </div>
}

function LiveRun() { const [selected, setSelected] = useState<string>('happy_path'); const [file, setFile] = useState<File | null>(null); const [preview, setPreview] = useState<string | null>(null); const [run, setRun] = useState(false); const [error, setError] = useState(''); const [active, setActive] = useState('Intake'); const [stageMap, setStageMap] = useState(initialStages); const [result, setResult] = useState<ProcessResponse | null>(null); const abortRef = useRef<AbortController | null>(null)
  useEffect(() => { let url: string | null = null; if (file) { url = URL.createObjectURL(file); setPreview(url) } else if (selected !== 'custom') { fetch(`${apiBaseUrl}/api/scenarios/${selected}/pdf`).then((response) => response.ok ? response.blob() : Promise.reject()).then((blob) => { url = URL.createObjectURL(blob); setPreview(url) }).catch(() => setPreview(null)) } return () => { if (url) URL.revokeObjectURL(url) } }, [file, selected])
  useEffect(() => { const onShortcut = (event: Event) => { const index = (event as CustomEvent<number>).detail; if (!run && scenarios[index]) { setSelected(scenarios[index].key); setFile(null) } }; window.addEventListener('scenario-shortcut', onShortcut); return () => window.removeEventListener('scenario-shortcut', onShortcut) }, [run])
  const update = (name: string, state: StageState, progress: number, log: string) => { setStageMap((current) => ({ ...current, [name]: { state, progress, logs: log ? [...current[name].logs, log] : current[name].logs } })); setActive(name) }
  const handleEvent = (event: SseEvent) => { const data = event.data; const stage = String(data.stage ?? ''); const mapped = stage === 'intake' ? 'Intake' : stage === 'extraction' ? 'Extraction' : stage === 'vendor_guard' ? 'Vendor Guard' : stage === 'po_matching' ? 'PO Matching' : stage === 'financial_validation' ? 'Financial Validation' : stage === 'decision' ? 'Decision' : ''; if (mapped) update(mapped, String(data.status ?? 'running') as StageState, Number(data.progress ?? 100), String(data.message ?? 'Processing stage')); if (event.event === 'complete') { setResult(data as unknown as ProcessResponse); setRun(false); update('Decision', String((data.decision as AnyRecord)?.decision ?? '').includes('WARNING') ? 'warning' : 'passed', 100, 'Run complete') } if (event.event === 'error') { setRun(false); setError(String(data.detail ?? 'Processing failed')); update(active, 'failed', 100, String(data.detail ?? 'Processing failed')) } }
  const start = async () => { setError(''); setResult(null); setStageMap(initialStages()); setRun(true); const controller = new AbortController(); abortRef.current = controller; try { await streamInvoice(file, selected, handleEvent, controller.signal) } catch (cause) { if ((cause as Error).name !== 'AbortError') { setError((cause as Error).message); update(active, 'failed', 100, (cause as Error).message) } setRun(false) } }
  const cancel = () => { abortRef.current?.abort(); setRun(false); update(active, 'failed', 0, 'Run cancelled') }
  return <LiveRunScreen selected={selected} setSelected={setSelected} file={file} setFile={setFile} preview={preview} run={run} error={error} active={active} setActive={setActive} stageMap={stageMap} result={result} setResult={setResult} start={start} cancel={cancel} /> }
function Dashboard() {
  const [stats, setStats] = useState<DashboardStats | null>(null)
  const [history, setHistory] = useState<HistoryRun[]>([])
  const [orders, setOrders] = useState<PurchaseOrder[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [decision, setDecision] = useState('all')
  const [vendor, setVendor] = useState('')
  const [query, setQuery] = useState('')
  const navigate = useNavigate()
  const load = async () => {
    setLoading(true)
    setError('')
    try {
      const [nextStats, nextHistory, nextOrders] = await Promise.all([getStats(), getHistory(), getPurchaseOrders()])
      setStats(nextStats); setHistory(nextHistory); setOrders(nextOrders)
    } catch (cause) { setError((cause as Error).message) } finally { setLoading(false) }
  }
  useEffect(() => { void load() }, [])
  const filtered = history.filter((run) => {
    const item = run.invoice as AnyRecord | undefined
    const runDecision = String((run.decision as AnyRecord | undefined)?.decision ?? '')
    const humanDecision = String(run.human_decision ?? '')
    const runVendor = String(run.vendor_display ?? item?.vendor_name ?? '')
    const runInvoice = String(run.display_id ?? item?.invoice_number ?? run.run_id)
    return (decision === 'all' || runDecision === decision || humanDecision === decision)
      && (!vendor || runVendor.toLowerCase().includes(vendor.toLowerCase()))
      && (!query || `${runInvoice} ${runVendor}`.toLowerCase().includes(query.toLowerCase()))
  })
  const money = (value: unknown, currency = 'USD') => formatCurrency(value, currency)
  const totals = stats?.total_by_currency ?? {}
  const approvedRate = stats?.total_runs ? Math.round((stats.approved / stats.total_runs) * 100) : 0
  const recent = history.slice(0, 4)
  const watch = history.filter((run) => (run.decision as AnyRecord | undefined)?.decision === 'MANUAL_REVIEW').slice(0, 2)
  const decisionStyle = (verdict: string) => verdict === 'APPROVED' ? 'bg-[#DCFCE7] text-[#15803D]' : verdict === 'REJECTED' ? 'bg-[#FEE2E2] text-[#B91C1C]' : verdict === 'MANUAL_REVIEW' ? 'bg-[#F5B169] text-[#30220f]' : verdict.includes('WARNING') ? 'bg-[#DBEAFE] text-[#1e3a5f]' : 'bg-[#EFE38A] text-[#3f3a15]'
  const launch = (index: number) => { navigate('/'); window.setTimeout(() => window.dispatchEvent(new CustomEvent('scenario-shortcut', { detail: index })), 0) }
  const overview = [
    { label: 'Auto-approval rate', value: `${approvedRate}%`, tag: 'Live', tagStyle: 'bg-[#DCFCE7] text-[#15803D]' },
    { label: 'Total processed runs', value: String(stats?.total_runs ?? 0), tag: 'D1 synced', tagStyle: 'bg-[#DBEAFE] text-[#244466]' },
    { label: 'Approved invoices', value: String(stats?.approved ?? 0), tag: 'Approved', tagStyle: 'bg-[#DCFCE7] text-[#15803D]' },
    { label: 'Needs review / rejected', value: String((stats?.manual_review ?? 0) + (stats?.rejected ?? 0)), tag: `${stats?.manual_review ?? 0} review · ${stats?.rejected ?? 0} rejected`, tagStyle: 'bg-[#FEE2E2] text-[#9f3131]' },
  ]
  return <div className="dashboard-page space-y-4">
    <div className="flex flex-wrap items-end justify-between gap-4 px-1 pb-1">
        <div><h1 className="text-[28px] font-bold tracking-[-.03em] text-[#111312]">Dashboard</h1><p className="mt-1 text-[13px] text-[#7A7E78]">Autonomous AP operations overview</p></div>
      <div className="flex items-center gap-5 pb-1">
        {Object.entries(totals).map(([currency, amount], index) => <div key={currency} className={index ? 'border-l border-[#E2E4DE] pl-5' : ''}><p className="text-xs text-[#7A7E78]">Processed ({currency})</p><p className="mt-1 text-sm font-bold tabular-nums text-[#111312]">{money(amount, currency)}</p></div>)}
        <button onClick={() => void load()} disabled={loading} title="Refresh dashboard" aria-label="Refresh dashboard" className="rounded-lg border border-[#E6E7E2] bg-white p-2 text-[#4B4F4A] hover:bg-[#F6F7F4]"><RefreshCw className={`size-4 ${loading ? 'animate-spin' : ''}`} /></button>
        <button onClick={async () => { if (!window.confirm('Reset test runs and restore seeded PO balances? The duplicate reference is retained. This cannot be undone.')) return; try { await resetDatabase(); await load() } catch (cause) { setError((cause as Error).message) } }} title="Reset test data" aria-label="Reset test data" className="rounded-lg border border-[#E6E7E2] bg-white p-2 text-[#7A7E78] hover:bg-[#FEE2E2] hover:text-[#B91C1C]"><Database className="size-4" /></button>
      </div>
    </div>
    {error && <p role="alert" className="rounded-lg border border-[#FCA5A5] bg-[#FEE2E2] p-3 text-sm text-[#B91C1C]">{error}</p>}
    {loading && !stats ? <div className="rounded-[20px] border border-[#EAECE6] bg-white p-10 text-center text-sm text-[#7A7E78]"><LoaderCircle className="mx-auto mb-3 size-6 animate-spin" />Loading AP telemetry</div> : <>
      <section className="called-card p-6">
        <div><h2 className="text-[19px] font-bold tracking-[-.02em] text-[#111312]">AP Performance Overview</h2><p className="mt-1 text-xs text-[#7A7E78]">Live Cloudflare D1 telemetry · {formatDate(stats?.as_of)}</p></div>
        <div className="mt-5 grid grid-cols-2 divide-x divide-y divide-[#EFEFEB] border-t border-[#E6E7E2] lg:grid-cols-4 lg:divide-y-0">
          {overview.map((item) => <div key={item.label} className="min-h-[88px] px-4 py-4 first:pl-0"><p className="text-xs text-[#7A7E78]">{item.label}</p><div className="mt-2 flex flex-wrap items-center justify-between gap-2"><span className="text-[23px] font-bold leading-none tabular-nums tracking-[-.02em] text-[#111312]">{item.value}</span><span className={`rounded-md px-2 py-1 text-[11px] font-medium ${item.tagStyle}`}>{item.tag}</span></div></div>)}
        </div>
      </section>
      <div className="grid grid-cols-1 items-stretch gap-4 lg:grid-cols-12">
        <section className="called-card p-5 lg:col-span-7">
          <div className="flex items-start justify-between"><div><h2 className="text-[19px] font-bold tracking-[-.02em] text-[#111312]">Invoices to Watch</h2><p className="mt-1 text-xs text-[#7A7E78]">Latest processed invoices and overrides</p></div><button onClick={() => document.getElementById('audit-ledger')?.scrollIntoView({ behavior: 'smooth' })} className="text-xs font-semibold text-[#4B4F4A] underline decoration-[#c8cbc3] underline-offset-4 hover:text-[#111312]">View all</button></div>
          <div className="mt-4 space-y-2.5">{recent.length ? recent.map((run) => {
            const item = run.invoice as AnyRecord | undefined
            const verdict = String((run.decision as AnyRecord | undefined)?.decision ?? 'MANUAL_REVIEW')
            const invoice = String(run.display_id ?? item?.invoice_number ?? run.run_id)
            const currency = String(item?.totals?.currency ?? 'USD')
            return <div key={run.run_id} className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-3 rounded-xl bg-[#F6F7F4] px-4 py-3.5 transition-colors hover:bg-[#EEF0E9] sm:grid-cols-[minmax(0,1fr)_110px_112px]">
              <div className="min-w-0"><p className="truncate text-sm font-semibold text-[#111312]">{String(run.vendor_display ?? item?.vendor_name ?? 'Unknown vendor')}</p><p className="mt-1 truncate font-mono text-[11px] text-[#7A7E78]">{invoice} · {run.extraction_metadata?.model_used?.split('/').at(-1) ?? 'PDF text'}</p></div>
              <div className="hidden sm:block"><p className="text-[10px] text-[#7A7E78]">Amount</p><p className="mt-1 text-[13px] font-semibold tabular-nums text-[#111312]">{money(item?.totals?.total ?? run.total_amount, currency)}</p></div>
              <div className="justify-self-end sm:justify-self-start"><p className="hidden text-[10px] text-[#7A7E78] sm:block">Decision</p><span className={`mt-1 inline-flex rounded-md px-2 py-1 text-[11px] font-medium ${decisionStyle(verdict)}`}>{verdict.replaceAll('_', ' ')}</span></div>
            </div>
          }) : <div className="rounded-xl bg-[#F6F7F4] p-5 text-sm text-[#7A7E78]">No invoices yet. Start with a sample invoice to see it here.<button onClick={() => { window.location.href = '/' }} className="ml-2 font-semibold text-[#111312] underline underline-offset-4">Go to Live Run</button></div>}</div>
        </section>
        <section className="called-card p-5 lg:col-span-5">
          <div className="flex items-start justify-between"><div><h2 className="text-[19px] font-bold tracking-[-.02em] text-[#111312]">PO Health &amp; Queue</h2><p className="mt-1 text-xs text-[#7A7E78]">Utilization and work needing attention</p></div><button onClick={() => void load()} title="Refresh purchase orders" aria-label="Refresh purchase orders" className="rounded-lg border border-[#E6E7E2] bg-white p-2 text-[#4B4F4A] hover:bg-[#F6F7F4]"><RefreshCw className="size-4" /></button></div>
          <div className="mt-5 grid grid-cols-5 gap-1 border-b border-[#EFEFEB] pb-4">{orders.slice(0, 5).map((order) => { const pct = order.total_amount ? Math.min(100, order.amount_invoiced / order.total_amount * 100) : 0; return <div key={order.id} className="text-center" title={`${money(order.amount_invoiced, order.currency)} / ${money(order.total_amount, order.currency)}`}><p className="truncate text-[10px] text-[#7A7E78]">{order.po_number}</p><p className={`mx-auto mt-1 w-fit rounded-full px-1.5 py-1 text-[11px] font-semibold tabular-nums ${pct ? 'bg-[#F3E9A2] text-[#423b16]' : 'text-[#4B4F4A]'}`}>{pct.toFixed(0)}%</p></div>})}</div>
          <div className="my-4 flex items-center gap-3 text-xs text-[#7A7E78]"><span className="whitespace-nowrap">Action queue</span><span className="w-full border-t border-dashed border-[#c8cbc3]" /></div>
          <div className="space-y-2.5">
            {watch.length ? watch.map((run) => { const item = run.invoice as AnyRecord | undefined; return <div key={run.run_id} className="flex items-center justify-between gap-3 rounded-[14px] bg-[#FBF07B] px-4 py-3"><div className="min-w-0"><p className="truncate text-[13px] font-semibold text-[#111312]">Review {String(run.display_id ?? item?.invoice_number ?? run.run_id)} · {String(run.vendor_display ?? item?.vendor_name ?? 'Unknown vendor')}</p><p className="mt-1 truncate text-[11px] text-[#5C5518]">AP review is required before payment</p></div><AlertTriangle className="size-4 shrink-0 text-[#6e6415]" /></div>}) : <button onClick={() => launch(2)} className="flex w-full items-center justify-between gap-3 rounded-[14px] bg-[#FBF07B] px-4 py-3 text-left"><span><span className="block text-[13px] font-semibold text-[#111312]">Test price variance</span><span className="mt-1 block text-[11px] text-[#5C5518]">Launch the Price Creep scenario</span></span><ChevronRight className="size-4 text-[#6e6415]" /></button>}
            <button onClick={() => launch(5)} className="flex w-full items-center justify-between gap-3 rounded-[14px] bg-[#FCE1F8] px-4 py-3 text-left"><span><span className="block text-[13px] font-semibold text-[#111312]">Test scanned invoice</span><span className="mt-1 block text-[11px] text-[#644560]">Run vision extraction on a scanned PDF</span></span><ChevronRight className="size-4 text-[#644560]" /></button>
          </div>
        </section>
      </div>
      <section id="audit-ledger" className="called-card p-5">
        <div className="flex flex-wrap items-end justify-between gap-3"><div><h2 className="text-[19px] font-bold tracking-[-.02em] text-[#111312]">Run History &amp; Audit Ledger</h2><p className="mt-1 text-xs text-[#7A7E78]">{filtered.length} of {history.length} recorded runs</p></div><div className="flex flex-wrap gap-2"><input aria-label="Search invoice history" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search invoices" className="w-40 rounded-lg border border-[#E6E7E2] bg-white px-3 py-2 text-xs text-[#111312] placeholder:text-[#969a94]"/><input aria-label="Filter by vendor" value={vendor} onChange={(e) => setVendor(e.target.value)} placeholder="Vendor" className="w-28 rounded-lg border border-[#E6E7E2] bg-white px-3 py-2 text-xs text-[#111312] placeholder:text-[#969a94]"/><select aria-label="Filter by decision" value={decision} onChange={(e) => setDecision(e.target.value)} className="rounded-lg border border-[#E6E7E2] bg-white px-3 py-2 text-xs text-[#4B4F4A]"><option value="all">All decisions</option><option value="APPROVED">Approved</option><option value="APPROVED_OVERRIDE">Approved override</option><option value="APPROVED_WITH_WARNING">Warning</option><option value="MANUAL_REVIEW">Manual review</option><option value="REJECTED">Rejected</option><option value="REJECTED_OVERRIDE">Rejected override</option></select></div></div>
        <div className="mt-4 overflow-x-auto"><table className="w-full min-w-[680px] text-left text-[13px]"><thead className="text-[11px] text-[#7A7E78]"><tr><th className="pb-3 font-medium">Invoice</th><th className="pb-3 font-medium">Vendor</th><th className="pb-3 text-right font-medium">Currency / Amount</th><th className="pb-3 pl-5 font-medium">AI model</th><th className="pb-3 pl-5 font-medium">Decision</th><th className="pb-3 text-right font-medium">Date</th></tr></thead><tbody>{filtered.length ? filtered.map((run) => { const item = run.invoice as AnyRecord | undefined; const verdict = String((run.decision as AnyRecord | undefined)?.decision ?? 'MANUAL_REVIEW'); const currency = String(item?.totals?.currency ?? 'USD'); return <tr key={run.run_id} className="border-t border-[#EFEFEB] hover:bg-[#fafbf8]"><td className="py-3 font-mono text-xs text-[#4B4F4A]">{String(run.display_id ?? item?.invoice_number ?? run.run_id)}</td><td className="py-3 font-medium text-[#111312]">{String(run.vendor_display ?? item?.vendor_name ?? 'Unknown vendor')}</td><td className="py-3 text-right tabular-nums text-[#111312]">{currency} {money(item?.totals?.total ?? run.total_amount, currency)}</td><td className="py-3 pl-5 text-xs text-[#7A7E78]">{run.extraction_metadata?.model_used?.split('/').at(-1) ?? 'PDF text'}</td><td className="py-3 pl-5"><span className={`rounded-md px-2 py-1 text-[11px] font-medium ${decisionStyle(verdict)}`}>{verdict.replaceAll('_', ' ')}</span></td><td className="py-3 text-right tabular-nums text-[#7A7E78]">{formatDate(run.created_at)}</td></tr>}) : <tr><td colSpan={6} className="py-8 text-center text-sm text-[#7A7E78]">No runs match these filters</td></tr>}</tbody></table></div>
      </section>
    </>}
  </div>
}
export default function App() { return <BrowserRouter><Shell /></BrowserRouter> }
