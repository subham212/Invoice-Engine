import { useEffect, useRef, useState } from 'react'
import { BrowserRouter, NavLink, Route, Routes } from 'react-router-dom'
import { getDocument, GlobalWorkerOptions } from 'pdfjs-dist'
import { apiBaseUrl, getStats, streamInvoice, type DashboardStats, type ProcessResponse, type SseEvent } from './services'
import 'pdfjs-dist/web/pdf_viewer.css'

GlobalWorkerOptions.workerSrc = new URL('pdfjs-dist/build/pdf.worker.min.mjs', import.meta.url).toString()

const scenarios = [
  { key: 'happy_path', name: 'Happy path', description: 'Clean invoice against its purchase order.', icon: <polyline points="20 6 9 17 4 12"></polyline> },
  { key: 'partial_billing', name: 'Partial billing', description: 'Invoice covering part of an open PO.', icon: <><path d="M21.21 15.89A10 10 0 1 1 8 2.83"></path><path d="M22 12A10 10 0 0 0 12 2v10z"></path></> },
  { key: 'price_creep', name: 'Price creep', description: 'Surface a unit price variance.', icon: <><polyline points="23 6 13.5 15.5 8.5 10.5 1 18"></polyline><polyline points="17 6 23 6 23 12"></polyline></> },
  { key: 'duplicate', name: 'Duplicate invoice', description: 'Identify a repeated invoice in the ledger.', icon: <><rect height="13" rx="2" ry="2" width="13" x="9" y="9"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></> },
  { key: 'missing_po', name: 'Missing PO', description: 'Route an invoice without a resolvable PO.', icon: <><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path><line x1="9" x2="15" y1="15" y2="15"></line></> },
]

const stages = ['Intake', 'Extraction', 'Vendor Guard', 'PO Matching', 'Financial Validation', 'Decision']
type StageState = 'pending' | 'running' | 'passed' | 'warning' | 'failed'
type Stage = { state: StageState; progress: number; logs: string[] }
const initialStages = (): Record<string, Stage> => Object.fromEntries(stages.map((stage) => [stage, { state: 'pending', progress: 0, logs: [] }]))

type AnyRecord = Record<string, any>

function Navigation() {
  return (
    <aside className="w-64 flex-shrink-0 bg-[#0B0D10] border-r border-[#232830] flex flex-col justify-between" data-purpose="application-sidebar">
      <div>
        <div className="h-16 px-5 flex items-center gap-3 border-b border-[#232830]">
          <div className="h-8 w-8 rounded bg-[#191D24] border border-[#2E353F] flex items-center justify-center text-[#4FD1C5]">
            <svg className="w-4 h-4" fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" viewBox="0 0 24 24">
              <path d="M4 4h16c1.1 0 2 .9 2 2v12c0 1.1-.9 2-2 2H4c-1.1 0-2-.9-2-2V6c0-1.1.9-2 2-2z"></path>
              <line x1="2" x2="22" y1="10" y2="10"></line>
              <line x1="12" x2="12" y1="10" y2="20"></line>
            </svg>
          </div>
          <div className="flex flex-col">
            <span className="text-sm font-semibold tracking-tight text-[#F2F4F7]">Zamp / AP</span>
            <span className="text-[10px] uppercase font-mono tracking-widest text-[#5B636E]">Operations</span>
          </div>
        </div>
        <div className="px-3 pt-6 pb-2">
          <p className="px-3 text-[11px] font-mono uppercase tracking-wider text-[#5B636E] mb-2 font-medium">Workspace</p>
          <nav className="space-y-1" data-purpose="primary-navigation">
            <NavLink to="/" className={({ isActive }) => `flex items-center gap-3 px-3 py-2 text-sm font-medium rounded transition-colors ${isActive ? 'bg-[#13161B] text-[#F2F4F7] border-l-2 border-[#4FD1C5] rounded-r rounded-l-none' : 'text-[#9AA3AF] hover:text-[#F2F4F7] hover:bg-[#13161B]/60'}`}>
              <svg className={`w-4 h-4 ${window.location.pathname === '/' ? 'text-[#4FD1C5]' : ''}`} fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" viewBox="0 0 24 24">
                <polygon points="5 3 19 12 5 21 5 3"></polygon>
              </svg>
              <span>Live Run</span>
            </NavLink>
            <NavLink to="/dashboard" className={({ isActive }) => `flex items-center gap-3 px-3 py-2 text-sm font-medium rounded transition-colors ${isActive ? 'bg-[#13161B] text-[#F2F4F7] border-l-2 border-[#4FD1C5] rounded-r rounded-l-none' : 'text-[#9AA3AF] hover:text-[#F2F4F7] hover:bg-[#13161B]/60'}`}>
              <svg className="w-4 h-4" fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" viewBox="0 0 24 24">
                <rect height="7" width="7" x="3" y="3"></rect>
                <rect height="7" width="7" x="14" y="3"></rect>
                <rect height="7" width="7" x="14" y="14"></rect>
                <rect height="7" width="7" x="3" y="14"></rect>
              </svg>
              <span>Dashboard</span>
            </NavLink>
            <a className="flex items-center gap-3 px-3 py-2 text-sm font-medium rounded text-[#9AA3AF] hover:text-[#F2F4F7] hover:bg-[#13161B]/60 transition-colors" href="#">
              <svg className="w-4 h-4" fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" viewBox="0 0 24 24">
                <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path>
                <polyline points="14 2 14 8 20 8"></polyline>
                <line x1="16" x2="8" y1="13" y2="13"></line>
                <line x1="16" x2="8" y1="17" y2="17"></line>
                <polyline points="10 9 9 9 8 9"></polyline>
              </svg>
              <span>Audit Logs</span>
            </a>
            <a className="flex items-center gap-3 px-3 py-2 text-sm font-medium rounded text-[#9AA3AF] hover:text-[#F2F4F7] hover:bg-[#13161B]/60 transition-colors" href="#">
              <svg className="w-4 h-4" fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" viewBox="0 0 24 24">
                <circle cx="12" cy="12" r="3"></circle>
                <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"></path>
              </svg>
              <span>Settings</span>
            </a>
          </nav>
        </div>
      </div>
      <div className="p-4 border-t border-[#232830]">
        <div className="flex items-center gap-2.5 px-3 py-2 rounded bg-[#13161B] border border-[#232830] text-[#9AA3AF] text-xs">
          <svg className="w-4 h-4 text-[#34D399] flex-shrink-0" fill="none" stroke="currentColor" strokeWidth="2" viewBox="0 0 24 24">
            <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"></path>
            <path d="M9 12l2 2 4-4"></path>
          </svg>
          <div className="flex flex-col">
            <span className="font-medium text-[#F2F4F7]">Traceable by default</span>
            <span className="text-[10px] text-[#5B636E] font-mono">SOC2 Type II • v2.4.1</span>
          </div>
        </div>
      </div>
    </aside>
  )
}

function Shell() {
  const [healthy, setHealthy] = useState<boolean | null>(null)
  
  useEffect(() => {
    fetch(`${apiBaseUrl}/api/health`)
      .then((r) => setHealthy(r.ok))
      .catch(() => setHealthy(false))
  }, [])

  return (
    <div className="flex h-screen w-full overflow-hidden text-[#F2F4F7] font-sans selection:bg-[#4FD1C5]/20 selection:text-[#4FD1C5]">
      <Navigation />
      <div className="flex-1 flex flex-col min-w-0 bg-[#0B0D10] overflow-y-auto">
        <header className="h-14 border-b border-[#232830] px-8 flex items-center justify-between flex-shrink-0 bg-[#0B0D10]/95 backdrop-blur z-10" data-purpose="top-header">
          <div className="flex items-center gap-2 text-xs font-medium text-[#9AA3AF]">
            <span className="hover:text-[#F2F4F7] transition-colors cursor-pointer">Accounts payable</span>
            <svg className="w-3 h-3 text-[#5B636E]" fill="none" stroke="currentColor" strokeWidth="2" viewBox="0 0 24 24"><polyline points="9 18 15 12 9 6"></polyline></svg>
            <span className="text-[#F2F4F7]">Operations</span>
          </div>
          <div className="flex items-center gap-2 px-2.5 py-1 rounded-full bg-[#13161B] border border-[#232830] text-xs text-[#9AA3AF]">
            <span className={`h-1.5 w-1.5 rounded-full ${healthy ? 'bg-[#34D399]' : healthy === false ? 'bg-[#F87171]' : 'bg-[#FBBF24]'}`}></span>
            <span className="font-mono text-[11px]">API {healthy === null ? 'checking' : healthy ? 'operational' : 'offline'}</span>
          </div>
        </header>
        <main className="flex-1 px-8 py-7 space-y-6 max-w-7xl mx-auto w-full">
          <Routes>
            <Route path="/" element={<LiveRun />} />
            <Route path="/dashboard" element={<Dashboard />} />
          </Routes>
        </main>
      </div>
    </div>
  )
}

function PdfPreview({ source, title }: { source: string | null; title: string }) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const [page, setPage] = useState(1)
  const [pages, setPages] = useState(0)

  useEffect(() => {
    let cancelled = false
    if (!source) return
    setPage(1)
    getDocument({ url: source }).promise
      .then(async (pdf) => {
        if (cancelled) return
        setPages(pdf.numPages)
        const current = await pdf.getPage(1)
        const viewport = current.getViewport({ scale: 1.25 })
        const canvas = canvasRef.current
        if (!canvas) return
        canvas.width = viewport.width
        canvas.height = viewport.height
        await current.render({ canvas, canvasContext: canvas.getContext('2d')!, viewport }).promise
      })
      .catch(() => {})
    return () => { cancelled = true }
  }, [source])

  useEffect(() => {
    if (!source || page === 1 || !canvasRef.current) return
    getDocument({ url: source }).promise
      .then(async (pdf) => {
        const current = await pdf.getPage(page)
        const viewport = current.getViewport({ scale: 1.25 })
        const canvas = canvasRef.current!
        canvas.width = viewport.width
        canvas.height = viewport.height
        await current.render({ canvas, canvasContext: canvas.getContext('2d')!, viewport }).promise
      })
      .catch(() => {})
  }, [page, source])

  return (
    <div className="lg:col-span-5 flex flex-col bg-[#13161B] border border-[#232830] rounded-lg overflow-hidden" data-purpose="document-preview-panel">
      <div className="px-4 py-3 border-b border-[#232830] flex items-center justify-between">
        <div className="flex items-center gap-2.5">
          <svg className="w-4 h-4 text-[#9AA3AF]" fill="none" stroke="currentColor" strokeWidth="2" viewBox="0 0 24 24">
            <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path>
            <polyline points="14 2 14 8 20 8"></polyline>
          </svg>
          <div className="flex flex-col">
            <span className="text-xs font-medium text-[#F2F4F7]">{title}</span>
            <span className="text-[10px] font-mono text-[#5B636E]">INV-2026-1001 • Acme Corp</span>
          </div>
        </div>
        <span className="text-[10px] font-mono bg-[#191D24] text-[#9AA3AF] px-2 py-0.5 rounded border border-[#232830]">PDF/A-1b</span>
      </div>
      <div className="px-4 py-2 bg-[#0B0D10] border-b border-[#232830] flex items-center justify-between text-xs text-[#9AA3AF]">
        <div className="flex items-center gap-1.5">
          <button disabled={page <= 1} onClick={() => setPage(p => p - 1)} className="p-1 rounded text-[#9AA3AF] hover:text-[#F2F4F7] hover:bg-[#191D24] transition-colors disabled:opacity-40 disabled:cursor-not-allowed" title="Previous page" type="button">
            <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" strokeWidth="2" viewBox="0 0 24 24"><polyline points="15 18 9 12 15 6"></polyline></svg>
          </button>
          <span className="font-mono text-[11px] text-[#5B636E] px-1">Page {pages ? page : 0} of {pages}</span>
          <button disabled={page >= pages || pages === 0} onClick={() => setPage(p => p + 1)} className="p-1 rounded text-[#9AA3AF] hover:text-[#F2F4F7] hover:bg-[#191D24] transition-colors disabled:opacity-40 disabled:cursor-not-allowed" title="Next page" type="button">
            <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" strokeWidth="2" viewBox="0 0 24 24"><polyline points="9 18 15 12 9 6"></polyline></svg>
          </button>
        </div>
        <div className="flex items-center gap-1">
          <button className="flex items-center gap-1 px-2 py-0.5 rounded text-[11px] font-mono text-[#9AA3AF] hover:text-[#F2F4F7] hover:bg-[#191D24] transition-colors" title="Download Document" type="button">
            <svg className="w-3 h-3" fill="none" stroke="currentColor" strokeWidth="2" viewBox="0 0 24 24"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path><polyline points="7 10 12 15 17 10"></polyline><line x1="12" x2="12" y1="15" y2="3"></line></svg>
            <span>PDF</span>
          </button>
        </div>
      </div>
      <div className="p-6 bg-[#1A1D23] flex items-center justify-center min-h-[560px] relative overflow-hidden">
        {!source && <p className="text-sm text-[#5B636E]">Select a scenario or upload a PDF to preview it.</p>}
        {source && <canvas ref={canvasRef} className="max-w-[420px] w-full shadow-xl" />}
      </div>
    </div>
  )
}

function LiveRun() {
  const [selected, setSelected] = useState<string>('happy_path')
  const [file, setFile] = useState<File | null>(null)
  const [preview, setPreview] = useState<string | null>(null)
  const [run, setRun] = useState(false)
  const [error, setError] = useState('')
  const [activeStage, setActiveStage] = useState('Intake')
  const [stageMap, setStageMap] = useState(initialStages)
  const [result, setResult] = useState<ProcessResponse | null>(null)
  const abortRef = useRef<AbortController | null>(null)

  useEffect(() => {
    let url: string | null = null
    if (file) {
      url = URL.createObjectURL(file)
      setPreview(url)
    } else if (selected !== 'custom') {
      fetch(`${apiBaseUrl}/api/scenarios/${selected}/pdf`)
        .then((r) => r.ok ? r.blob() : Promise.reject())
        .then((blob) => { url = URL.createObjectURL(blob); setPreview(url) })
        .catch(() => setPreview(null))
    }
    return () => { if (url) URL.revokeObjectURL(url) }
  }, [file, selected])

  const update = (name: string, state: StageState, progress: number, log: string) => {
    setStageMap((current) => ({
      ...current,
      [name]: { state, progress, logs: log ? [...current[name].logs, log] : current[name].logs }
    }))
    setActiveStage(name)
  }

  const handleEvent = (event: SseEvent) => {
    const data = event.data
    const s = String(data.stage ?? '')
    const mapped = s === 'intake' ? 'Intake' : s === 'extraction' ? 'Extraction' : s === 'vendor_guard' ? 'Vendor Guard' : s === 'po_matching' ? 'PO Matching' : s === 'financial_validation' ? 'Financial Validation' : s === 'decision' ? 'Decision' : ''
    
    if (mapped) update(mapped, String(data.status ?? 'running') as StageState, Number(data.progress ?? 100), String(data.message ?? 'Processing stage'))
    
    if (event.event === 'complete') {
      setResult(data as unknown as ProcessResponse)
      setRun(false)
      update('Decision', String((data.decision as AnyRecord)?.decision ?? '').includes('WARNING') ? 'warning' : 'passed', 100, 'Run complete')
    }
    
    if (event.event === 'error') {
      setRun(false)
      setError(String(data.detail ?? 'Processing failed'))
      update(activeStage, 'failed', 100, String(data.detail ?? 'Processing failed'))
    }
  }

  const start = async () => {
    setError('')
    setResult(null)
    setStageMap(initialStages())
    setRun(true)
    const controller = new AbortController()
    abortRef.current = controller
    try {
      await streamInvoice(file, selected, handleEvent, controller.signal)
    } catch (cause) {
      if ((cause as Error).name !== 'AbortError') {
        setError((cause as Error).message)
        update(activeStage, 'failed', 100, (cause as Error).message)
      }
      setRun(false)
    }
  }

  return (
    <>
      <section className="space-y-1.5" data-purpose="hero-header">
        <div className="flex items-center gap-2">
          <span className="inline-block w-1.5 h-1.5 rounded-full bg-[#9AA3AF]"></span>
          <span className="font-mono text-xs font-semibold tracking-wider text-[#9AA3AF] uppercase">Live Run Workspace</span>
        </div>
        <h1 className="text-2xl lg:text-3xl font-semibold tracking-tight text-[#F2F4F7]">
          Make every invoice decision <span className="text-[#4FD1C5]">defensible.</span>
        </h1>
        <p className="text-sm text-[#9AA3AF] max-w-2xl">
          Select a configured scenario or upload a PDF, then watch the real processing trace move through the pipeline.
        </p>
      </section>

      <section className="flex flex-col lg:flex-row items-stretch lg:items-center justify-between gap-3 p-1.5 bg-[#13161B] border border-[#232830] rounded-lg mt-6" data-purpose="scenario-bar">
        <div aria-label="Invoice Scenarios" className="flex-1 inline-flex p-0.5 rounded-md bg-[#0B0D10] border border-[#232830] divide-x divide-[#232830] overflow-x-auto" role="radiogroup">
          {scenarios.map((scenario) => (
            <button key={scenario.key} aria-checked={selected === scenario.key} onClick={() => { setSelected(scenario.key); setFile(null) }} disabled={run} type="button" className={`flex items-center justify-center gap-2 px-3.5 py-1.5 text-xs font-medium rounded-sm shadow-inner text-left transition-all flex-shrink-0 ${selected === scenario.key && !file ? 'bg-[#191D24] text-[#F2F4F7]' : 'text-[#9AA3AF] hover:text-[#F2F4F7] hover:bg-[#13161B]'}`}>
              <svg className={`w-3.5 h-3.5 flex-shrink-0 ${selected === scenario.key && !file ? 'text-[#4FD1C5]' : 'text-[#5B636E]'}`} fill="none" stroke="currentColor" strokeWidth="2" viewBox="0 0 24 24">
                {scenario.icon}
              </svg>
              <span className="truncate">{scenario.name}</span>
            </button>
          ))}
          <label className={`flex cursor-pointer items-center justify-center gap-2 px-3.5 py-1.5 text-xs font-medium rounded-sm shadow-inner text-left transition-all flex-shrink-0 ${file ? 'bg-[#191D24] text-[#F2F4F7]' : 'text-[#9AA3AF] hover:text-[#F2F4F7] hover:bg-[#13161B]'}`}>
            <svg className={`w-3.5 h-3.5 flex-shrink-0 ${file ? 'text-[#4FD1C5]' : 'text-[#5B636E]'}`} fill="none" stroke="currentColor" strokeWidth="2" viewBox="0 0 24 24"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path><polyline points="17 8 12 3 7 8"></polyline><line x1="12" x2="12" y1="3" y2="15"></line></svg>
            <span className="truncate">{file ? file.name : 'Custom PDF'}</span>
            <input className="sr-only" type="file" accept="application/pdf,.pdf" disabled={run} onChange={(e) => { setFile(e.target.files?.[0] ?? null); setSelected('custom') }} />
          </label>
        </div>
        <div className="flex-shrink-0 flex items-center">
          <button onClick={start} disabled={run} className="w-full lg:w-auto inline-flex items-center justify-center gap-2 px-4 py-2 rounded bg-[#4FD1C5] hover:bg-[#38B2AC] active:scale-[0.99] text-[#0B0D10] font-semibold text-xs transition-all shadow-sm disabled:opacity-50 disabled:cursor-not-allowed" type="button">
            <svg className="w-3.5 h-3.5 fill-current" viewBox="0 0 24 24"><polygon points="5 3 19 12 5 21 5 3"></polygon></svg>
            <span>{run ? 'Running...' : 'Run invoice'}</span>
          </button>
        </div>
      </section>

      {error && (
        <div className="p-4 bg-[#F87171]/10 text-[#F87171] border border-[#F87171]/30 rounded-lg text-sm mt-4">
          {error}
        </div>
      )}

      <section className="bg-[#13161B] border border-[#232830] rounded-lg p-5 mt-6" data-purpose="pipeline-tracker">
        <div className="flex items-center justify-between mb-5">
          <div className="flex items-center gap-2">
            <span className="text-xs font-mono font-medium text-[#9AA3AF] uppercase tracking-wider">Pipeline Stage Tracker</span>
            <span className="text-[11px] px-1.5 py-0.5 rounded bg-[#191D24] text-[#4FD1C5] font-mono border border-[#232830]">STAGE {String(stages.indexOf(activeStage) + 1).padStart(2, '0')} / 06</span>
          </div>
          <span className="text-xs text-[#5B636E] font-mono">Deterministic Sequence</span>
        </div>
        <div className="relative flex items-center justify-between">
          <div className="absolute left-6 right-6 top-3.5 -translate-y-1/2 h-[2px] bg-[#232830] z-0">
            <div className={`h-full bg-[#4FD1C5] transition-all`} style={{ width: `${(stages.indexOf(activeStage) / (stages.length - 1)) * 100}%` }}></div>
          </div>
          
          {stages.map((stage, i) => {
            const item = stageMap[stage]
            const index = stages.indexOf(activeStage)
            const isActive = stage === activeStage
            const isPast = i < index || item.state === 'passed'
            
            return (
              <div key={stage} className="relative z-10 flex flex-col items-center group cursor-default gap-2.5">
                <div className={`h-7 w-7 rounded-full flex items-center justify-center font-mono text-xs shadow-sm transition-colors ${isActive ? 'bg-[#0B0D10] border-2 border-[#4FD1C5] text-[#4FD1C5] font-semibold' : isPast ? 'bg-[#4FD1C5] border border-[#4FD1C5] text-[#0B0D10]' : 'bg-[#13161B] border border-[#2E353F] text-[#5B636E]'}`}>
                  {i + 1}
                </div>
                <div className="text-center flex flex-col items-center gap-1">
                  <p className={`text-xs ${isActive || isPast ? 'font-semibold text-[#F2F4F7]' : 'font-medium text-[#5B636E]'}`}>{stage}</p>
                  <span className={`px-1.5 py-0.5 rounded text-[10px] font-mono uppercase tracking-wider ${isActive ? 'bg-[#191D24] text-[#4FD1C5] border border-[#232830]' : isPast ? 'bg-[#191D24] text-[#9AA3AF] border border-[#232830]' : 'bg-[#191D24]/60 text-[#5B636E] border border-transparent'}`}>
                    {item.state === 'running' ? 'Running' : item.state === 'passed' ? 'Done' : item.state === 'failed' ? 'Failed' : isActive ? 'Ready' : 'Queued'}
                  </span>
                </div>
              </div>
            )
          })}
        </div>
      </section>

      <div className="grid grid-cols-1 lg:grid-cols-12 gap-6 items-start pb-12 mt-6" data-purpose="workspace-split-layout">
        <PdfPreview source={preview} title={file ? file.name : `${scenarios.find(s => s.key === selected)?.name ?? 'Invoice'} sample`} />
        
        <div className="lg:col-span-7 flex flex-col space-y-4" data-purpose="telemetry-reconciliation-panel">
          <div className="relative bg-[#13161B] border border-[#232830] rounded-lg p-4 overflow-hidden">
            <div className="absolute top-0 left-0 right-0 h-[1px] bg-[#4FD1C5]"></div>
            <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2.5">
              <div className="flex items-center gap-2.5">
                <span className="h-2 w-2 rounded-full bg-[#4FD1C5]"></span>
                <span className="px-1.5 py-0.5 rounded text-[10px] font-mono tracking-wider uppercase bg-[#191D24] text-[#4FD1C5] border border-[#232830]">STAGE {String(stages.indexOf(activeStage) + 1).padStart(2, '0')} / 06</span>
                <span className="text-xs font-mono uppercase tracking-wider text-[#5B636E]">Active Stage:</span>
                <span className="text-sm font-semibold text-[#F2F4F7]">{activeStage}</span>
              </div>
              <div className="flex items-center gap-2">
                <span className="font-mono text-[10px] text-[#5B636E]">HASH: 0x{result?.run_id?.slice(0, 8) ?? 'pending'}</span>
                <span className="px-2 py-0.5 rounded text-[11px] font-mono bg-[#191D24] text-[#4FD1C5] border border-[#232830]">
                  {run ? 'Executing' : result ? 'Complete' : 'Initialized'}
                </span>
              </div>
            </div>
            
            <div className="mt-4 pt-3 border-t border-[#232830]/80 h-40 overflow-y-auto space-y-2 font-mono text-xs">
              {stageMap[activeStage].logs.length ? stageMap[activeStage].logs.map((log, i) => (
                <p key={`${log}-${i}`} className="text-[#9AA3AF]">{log}</p>
              )) : (
                <p className="text-[#5B636E]">Waiting for operations...</p>
              )}
            </div>

            <div className="mt-3 pt-2.5 border-t border-[#232830]/80 flex flex-wrap items-center justify-between gap-2 text-[11px] font-mono text-[#5B636E]">
              <span>TS: {new Date().toISOString()}</span>
              <span>ENGINE: v2.4.1-rc3</span>
            </div>
          </div>

          {!result && !run && (
            <div className="bg-[#13161B] border border-[#232830] rounded-lg p-6 flex flex-col items-center justify-center min-h-[478px]">
              <div className="h-12 w-12 rounded-lg bg-[#191D24] border border-[#2E353F] flex items-center justify-center text-[#5B636E] mb-4">
                <svg className="w-6 h-6" fill="none" stroke="currentColor" strokeWidth="1.75" viewBox="0 0 24 24">
                  <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path>
                  <circle cx="11.5" cy="14.5" r="2.5"></circle>
                  <path d="M13.5 16.5L16 19"></path>
                </svg>
              </div>
              <h4 className="text-sm font-medium text-[#F2F4F7] tracking-tight text-center">
                Waiting for invoice execution
              </h4>
              <p className="mt-1.5 text-xs text-[#9AA3AF] max-w-md text-center leading-relaxed">
                Initiate the run to observe extraction confidence, automated PO line reconciliation, and policy guard decisions in real time.
              </p>
              
              <div className="w-full mt-8 grid grid-cols-1 sm:grid-cols-3 gap-3 pt-6 border-t border-[#232830]">
                {['Fields', 'Line Match', 'Guards'].map((section, idx) => (
                  <div key={idx} className="p-3 rounded bg-[#0B0D10]/50 border border-[#232830]/80 flex flex-col gap-1.5">
                    <div className="flex items-center justify-between">
                      <span className="text-[10px] font-mono uppercase text-[#5B636E]">{section}</span>
                      <span className="text-[10px] font-mono text-[#5B636E]">—</span>
                    </div>
                    <span className="text-xs text-[#9AA3AF] font-medium">{section === 'Fields' ? 'Extraction metrics' : section === 'Line Match' ? 'PO Line Matching' : 'Policy checks'}</span>
                    <div className="h-1 w-full bg-[#191D24] rounded-full overflow-hidden mt-1"></div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {result && (
            <div className="bg-[#13161B] border border-[#232830] rounded-lg p-6 space-y-6">
              <div className="flex justify-between items-start border-b border-[#232830] pb-4">
                <div>
                  <h3 className="text-sm font-medium text-[#F2F4F7]">Execution Trace Summary</h3>
                  <p className="text-xl font-mono mt-2" style={{color: String(result.decision?.decision).includes('APPROVE') ? '#34D399' : String(result.decision?.decision).includes('WARNING') ? '#FBBF24' : '#F87171'}}>
                    {String(result.decision?.decision).replace(/_/g, ' ')}
                  </p>
                  <p className="text-xs text-[#9AA3AF] mt-2">{String(result.decision?.reason ?? 'No explanation provided')}</p>
                </div>
                <div className="bg-[#191D24] border border-[#2E353F] px-3 py-2 rounded text-center">
                  <p className="text-[10px] font-mono text-[#5B636E] uppercase">Confidence</p>
                  <p className="font-mono text-lg text-[#F2F4F7]">{String(result.decision?.confidence ?? 0)}%</p>
                </div>
              </div>
              
              <div>
                <h4 className="text-xs font-mono uppercase text-[#5B636E] mb-3">Extracted Values</h4>
                <div className="grid grid-cols-2 gap-3 text-sm">
                  {['invoice_number', 'vendor_name', 'invoice_date', 'purchase_order_number'].map(k => (
                    <div key={k} className="p-2 border border-[#232830] rounded bg-[#0B0D10]/50">
                      <span className="text-xs text-[#9AA3AF] block mb-1">{k.replace(/_/g, ' ').toUpperCase()}</span>
                      <span className="font-mono text-[#F2F4F7]">{result.invoice?.[k] ? String(result.invoice?.[k]) : '—'}</span>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          )}
        </div>
      </div>
    </>
  )
}

function Dashboard() {
  const [stats, setStats] = useState<DashboardStats | null>(null)
  
  useEffect(() => {
    getStats().then(setStats).catch(() => {})
  }, [])

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold text-[#F2F4F7]">Command Center</h1>
      <p className="text-sm text-[#9AA3AF]">Dashboard metrics will be populated here.</p>
      
      <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
        {[
          { label: 'Runs', value: stats?.total_runs ?? 0 },
          { label: 'Approved', value: stats?.approved ?? 0 },
          { label: 'Needs Review', value: stats?.manual_review ?? 0 },
          { label: 'Total Volume', value: `$${stats?.total_amount ? stats.total_amount.toLocaleString() : 0}` }
        ].map((card, i) => (
          <div key={i} className="bg-[#13161B] border border-[#232830] rounded-lg p-5">
            <p className="text-xs font-mono uppercase text-[#5B636E]">{card.label}</p>
            <p className="text-2xl font-semibold text-[#F2F4F7] mt-2">{card.value}</p>
          </div>
        ))}
      </div>
    </div>
  )
}

export default function App() {
  return (
    <BrowserRouter>
      <Shell />
    </BrowserRouter>
  )
}