import { Component, type ErrorInfo, type ReactNode } from 'react'

export default class ErrorBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false }

  static getDerivedStateFromError() {
    return { failed: true }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('Workspace rendering failed', error, info.componentStack)
  }

  render() {
    if (this.state.failed) return <main className="min-h-screen bg-[#F3F4EF] p-6 text-[#111312]"><section className="mx-auto mt-20 max-w-lg rounded-[20px] border border-[#EAECE6] bg-white p-8 text-center shadow-sm"><h1 className="text-xl font-bold">Workspace needs to reload</h1><p className="mt-2 text-sm text-[#7A7E78]">Something went wrong while displaying this page.</p><button type="button" onClick={() => window.location.reload()} className="mt-5 rounded-lg bg-[#C5F084] px-4 py-2 text-sm font-semibold hover:bg-[#B7E86E]">Reload workspace</button></section></main>
    return this.props.children
  }
}
