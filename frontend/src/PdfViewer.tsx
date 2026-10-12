import { useEffect, useRef, useState } from 'react'
import { getDocument, GlobalWorkerOptions, type PDFDocumentProxy } from 'pdfjs-dist'
import workerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?url'
import { ChevronLeft, ChevronRight, ExternalLink, LoaderCircle } from 'lucide-react'
import type { Position } from './api'

GlobalWorkerOptions.workerSrc = workerUrl

export default function PdfViewer({ documentId, pageNumber, onPageChange, boxes }: {
  documentId: string; pageNumber: number; onPageChange: (page: number) => void; boxes: Position[]
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const containerRef = useRef<HTMLDivElement>(null)
  const [pdf, setPdf] = useState<PDFDocumentProxy | null>(null)
  const [width, setWidth] = useState(440)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [size, setSize] = useState({ width: 440, height: 580 })
  useEffect(() => {
    const observer = new ResizeObserver(entries => setWidth(Math.max(240, entries[0].contentRect.width - 24)))
    if (containerRef.current) observer.observe(containerRef.current)
    return () => observer.disconnect()
  }, [])
  useEffect(() => {
    setPdf(null); setError(''); setLoading(true)
    const task = getDocument({ url: `/api/documents/${documentId}/file` })
    task.promise.then(setPdf).catch(err => { if (err?.name !== 'AbortException') setError('无法打开 PDF，请检查文件是否完整') })
    return () => { void task.destroy() }
  }, [documentId])
  useEffect(() => {
    if (!pdf || !canvasRef.current) return
    let canceled = false
    let renderTask: ReturnType<Awaited<ReturnType<PDFDocumentProxy['getPage']>>['render']> | undefined
    setLoading(true)
    pdf.getPage(Math.min(Math.max(1, pageNumber), pdf.numPages)).then(page => {
      if (canceled || !canvasRef.current) return
      const viewport = page.getViewport({ scale: width / page.getViewport({ scale: 1 }).width })
      const canvas = canvasRef.current
      const dpr = window.devicePixelRatio || 1
      canvas.width = Math.floor(viewport.width * dpr); canvas.height = Math.floor(viewport.height * dpr)
      canvas.style.width = `${viewport.width}px`; canvas.style.height = `${viewport.height}px`
      setSize({ width: viewport.width, height: viewport.height })
      renderTask = page.render({ canvas, viewport, transform: dpr !== 1 ? [dpr, 0, 0, dpr, 0, 0] : undefined })
      return renderTask.promise
    }).then(() => { if (!canceled) setLoading(false) }).catch(err => {
      if (!canceled && err?.name !== 'RenderingCancelledException') { setLoading(false); setError('页面渲染失败') }
    })
    return () => { canceled = true; renderTask?.cancel() }
  }, [pdf, pageNumber, width])
  return <div className="pdf-viewer" ref={containerRef}>
    <div className="pdf-toolbar">
      <button className="icon-button" aria-label="上一页" disabled={pageNumber <= 1} onClick={() => onPageChange(pageNumber - 1)}><ChevronLeft size={16} /></button>
      <span>第 <input aria-label="PDF 页码" type="number" min="1" max={pdf?.numPages || 1} value={pageNumber} onChange={e => onPageChange(Math.max(1, Math.min(pdf?.numPages || 1, Number(e.target.value))))} /> / {pdf?.numPages || '—'} 页</span>
      <button className="icon-button" aria-label="下一页" disabled={!pdf || pageNumber >= pdf.numPages} onClick={() => onPageChange(pageNumber + 1)}><ChevronRight size={16} /></button>
      <a className="icon-button" href={`/api/documents/${documentId}/file`} target="_blank" rel="noreferrer" aria-label="在新窗口打开原文"><ExternalLink size={15} /></a>
    </div>
    {error && <div className="notice error">{error}</div>}
    <div className="pdf-scroll"><div className="pdf-page" style={size}>
      <canvas ref={canvasRef} />
      {boxes.filter(box => box.page === pageNumber).map((box, i) => <div key={i} className="pdf-highlight" style={{
        left: `${box.bbox[0] / box.width * 100}%`, top: `${box.bbox[1] / box.height * 100}%`,
        width: `${(box.bbox[2] - box.bbox[0]) / box.width * 100}%`, height: `${(box.bbox[3] - box.bbox[1]) / box.height * 100}%`,
      }} />)}
      {loading && <div className="pdf-loading"><LoaderCircle className="spin" size={24} />正在打开原文</div>}
    </div></div>
  </div>
}
