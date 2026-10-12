import { useCallback, useEffect, useRef, useState } from 'react'
import { BookOpen, Upload, Plus, Search, Check, ArrowUp, Square, FileText, ChevronRight, X, SlidersHorizontal, RotateCw, MessageSquare, PanelRightClose, PanelRightOpen, FlaskConical, LoaderCircle, Quote, Image as ImageIcon, Table2, ShieldCheck, AlertCircle } from 'lucide-react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { api, consumeEvents, type Paper, type Health, type Message, type Chat, type Source, type Parsed, type Position } from './api'
import PdfViewer from './PdfViewer'

const suggestions = ['Fe³⁺ 在降解过程中起什么作用？', '实验条件和主要结论是什么？', '这篇研究有哪些局限？']
const paperStatus: Record<string, string> = { queued: '等待处理', processing: '正在处理', ready: '可提问', error: '处理失败' }

export default function App() {
  const [papers, setPapers] = useState<Paper[]>([])
  const [health, setHealth] = useState<Health | null>(null)
  const [search, setSearch] = useState('')
  const [selected, setSelected] = useState<string[]>([])
  const [selectedScope, setSelectedScope] = useState(false)
  const [paperId, setPaperId] = useState<string | null>(null)
  const [parsed, setParsed] = useState<Parsed | null>(null)
  const [parseError, setParseError] = useState('')
  const [pageNumber, setPageNumber] = useState(1)
  const [activeSource, setActiveSource] = useState<Source | null>(null)
  const [previewTab, setPreviewTab] = useState('pdf')
  const [previewOpen, setPreviewOpen] = useState(true)
  const [chats, setChats] = useState<Chat[]>([])
  const [chatId, setChatId] = useState<string | null>(null)
  const [messages, setMessages] = useState<Message[]>([])
  const [question, setQuestion] = useState('')
  const [generating, setGenerating] = useState(false)
  const [stage, setStage] = useState('')
  const [notice, setNotice] = useState('')
  const [uploading, setUploading] = useState(false)
  const [dragging, setDragging] = useState(false)
  const [settings, setSettings] = useState<any>(null)
  const [historyOpen, setHistoryOpen] = useState(false)
  const fileRef = useRef<HTMLInputElement>(null)
  const abortRef = useRef<AbortController | null>(null)
  const conversationRef = useRef<HTMLDivElement>(null)
  const currentPaper = papers.find(p => p.id === paperId)
  const readyCount = papers.filter(p => p.active_index).length

  const refresh = useCallback(async () => {
    try {
      const [docs, status] = await Promise.all([api<Paper[]>('/documents'), api<Health>('/health')])
      setPapers(docs); setHealth(status)
    } catch (error) { setNotice((error as Error).message) }
  }, [])
  useEffect(() => {
    void refresh()
    void api<Chat[]>('/chats').then(setChats).catch(() => {})
    const interval = setInterval(() => void refresh(), 2500)
    return () => { clearInterval(interval); abortRef.current?.abort() }
  }, [refresh])
  useEffect(() => {
    if (!paperId) { setParsed(null); setParseError(''); return }
    let current = true
    setParsed(null); setParseError('')
    api<Parsed>(`/documents/${paperId}/content${activeSource?.index_id ? `?index_id=${activeSource.index_id}` : ''}`)
      .then(value => { if (current) setParsed(value) })
      .catch(error => { if (current) setParseError(error.message) })
    return () => { current = false }
  }, [paperId, currentPaper?.active_index, currentPaper?.job?.status, activeSource?.index_id])
  useEffect(() => {
    const element = conversationRef.current
    if (element) element.scrollTop = element.scrollHeight
  }, [messages, stage])

  const openPaper = (id: string) => { setPaperId(id); setPageNumber(1); setActiveSource(null); setPreviewTab('pdf'); setPreviewOpen(true) }
  const openSource = (source: Source) => {
    setPaperId(source.document_id); setActiveSource(source); setPageNumber(source.pages[0] || 1)
    setPreviewTab(source.kind === 'figure' || source.kind === 'table' ? 'elements' : 'pdf'); setPreviewOpen(true)
  }
  async function upload(files: FileList | File[]) {
    if (!files.length) return
    setUploading(true); setNotice('')
    const form = new FormData()
    Array.from(files).forEach(file => form.append('files', file))
    try {
      const result = await api<{ results: { filename: string; document_id?: string; duplicate?: boolean; error?: string }[] }>('/documents', { method: 'POST', body: form })
      const errors = result.results.filter(item => item.error)
      const duplicateCount = result.results.filter(item => item.duplicate).length
      const imported = result.results.filter(item => item.document_id && !item.duplicate).length
      setNotice([imported ? `已添加 ${imported} 篇文献，正在后台处理。` : '', duplicateCount ? `${duplicateCount} 篇已存在，已跳过重复导入。` : '', ...errors.map(item => `${item.filename}：${item.error}`)].filter(Boolean).join(' '))
      await refresh()
      const first = result.results.find(item => item.document_id)
      if (first?.document_id) openPaper(first.document_id)
    } catch (error) { setNotice((error as Error).message) }
    finally { setUploading(false); if (fileRef.current) fileRef.current.value = '' }
  }
  async function openChat(id: string) {
    if (generating) return
    try { setMessages(await api<Message[]>(`/chats/${id}/messages`)); setChatId(id); setHistoryOpen(false); setActiveSource(null) }
    catch (error) { setNotice((error as Error).message) }
  }
  async function reindex(paper: Paper) {
    try {
      await api(`/documents/${paper.id}/reindex`, { method: 'POST' }); await refresh()
      setNotice('已安排重新索引，完成前保留已有检索结果。')
    } catch (error) { setNotice((error as Error).message) }
  }
  async function retry(paper: Paper) {
    try { await api(`/jobs/${paper.job!.id}/retry`, { method: 'POST' }); await refresh() }
    catch (error) { setNotice((error as Error).message) }
  }
  async function send() {
    const input = question.trim()
    if (!input || generating) return
    if (selectedScope && !selected.length) { setNotice('请勾选至少一篇文献，或切换到全部文献。'); return }
    if (!health?.llm_configured) { setNotice('请先在项目根目录的 .env 中填写 DeepSeek 密钥并重启服务。文献导入和原文查看可以继续使用。'); return }
    setGenerating(true); setNotice(''); setStage('连接问答服务')
    const controller = new AbortController(); abortRef.current = controller
    const userId = `user-${Date.now()}`, assistantId = `assistant-${Date.now()}`
    let activeId = chatId
    try {
      if (!activeId) { const chat = await api<Chat>('/chats', { method: 'POST' }); activeId = chat.id; setChatId(chat.id) }
      setMessages(previous => [...previous, { id: userId, role: 'user', content: input, status: 'complete', sources: [] }, { id: assistantId, role: 'assistant', content: '', status: 'streaming', sources: [] }])
      setQuestion('')
      const updateAssistant = (update: Partial<Message> | ((message: Message) => Partial<Message>)) => setMessages(previous => previous.map(message => message.id === assistantId ? { ...message, ...(typeof update === 'function' ? update(message) : update) } : message))
      const response = await fetch(`/api/chats/${activeId}/messages`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ question: input, document_ids: selectedScope ? selected : null }), signal: controller.signal })
      await consumeEvents(response, (event, data) => {
        if (event === 'status') setStage(data.message)
        if (event === 'warning') setNotice(data.message)
        if (event === 'sources') updateAssistant({ sources: data.sources })
        if (event === 'token') updateAssistant(message => ({ content: message.content + data.text }))
        if (event === 'done') updateAssistant({ content: data.content, sources: data.sources, status: 'complete' })
        if (event === 'error') { setNotice(data.message); updateAssistant({ content: data.content, status: 'error' }) }
      })
    } catch (error) {
      const interrupted = (error as Error).name === 'AbortError'
      setMessages(previous => previous.map(message => message.id === assistantId ? { ...message, status: interrupted ? 'interrupted' : 'error', content: message.content || (interrupted ? '已停止生成。' : (error as Error).message) } : message))
      if (!interrupted) setNotice((error as Error).message)
    } finally {
      setGenerating(false); setStage(''); abortRef.current = null
      void api<Chat[]>('/chats').then(setChats)
    }
  }

  const previewBlocks = (parsed?.blocks || []).filter(block => !block.removed && ['figure', 'table'].includes(block.kind))
  const boxes: Position[] = activeSource?.boxes || []
  const filteredPapers = papers.filter(paper => (paper.title + paper.filename).toLowerCase().includes(search.toLowerCase()))

  return <div className={`workspace ${previewOpen ? '' : 'preview-collapsed'}`}>
    <header className="topbar">
      <a className="brand" href="/" aria-label="PaperSleuth 首页"><span className="brand-icon"><BookOpen size={21} /></span><span>PaperSleuth<span className="brand-sub">文献研究工作台</span></span></a>
      <div className="topbar-right"><span className="local-label"><span className="status-dot" />文献在本机处理</span>
        <button className="quiet-button" onClick={() => void api('/settings').then(setSettings).catch(error => setNotice(error.message))}><SlidersHorizontal size={16} /><span>设置</span></button>
      </div>
    </header>
    <aside className={`library ${dragging ? 'dragging' : ''}`} onDragOver={event => { event.preventDefault(); setDragging(true) }} onDragLeave={event => { if (!event.currentTarget.contains(event.relatedTarget as Node)) setDragging(false) }} onDrop={event => { event.preventDefault(); setDragging(false); void upload(event.dataTransfer.files) }}>
      <div className="panel-heading"><div><span className="eyebrow">RESEARCH LIBRARY</span><h2>我的文献 <span>{papers.length}</span></h2></div><button className="icon-button" title="上传文献" aria-label="上传文献" onClick={() => fileRef.current?.click()}><Plus size={19} /></button></div>
      <input ref={fileRef} type="file" accept=".pdf,application/pdf" multiple hidden onChange={event => event.target.files && void upload(event.target.files)} />
      <button className="upload-box" onClick={() => fileRef.current?.click()} disabled={uploading}><span className="upload-icon">{uploading ? <LoaderCircle size={20} className="spin" /> : <Upload size={20} />}</span><strong>{uploading ? '正在上传' : '导入 PDF 文献'}</strong><span>点击选择，或拖入多个文件</span></button>
      <label className="search-box"><Search size={15} /><input placeholder="搜索标题或文件名" aria-label="搜索文献" value={search} onChange={event => setSearch(event.target.value)} /></label>
      <div className="library-meta"><span>{readyCount} 篇可提问</span><button className={!selectedScope ? 'selected-link' : ''} onClick={() => { setSelectedScope(false); setSelected([]) }}>全部文献</button></div>
      <div className="paper-list">{filteredPapers.map(paper => <article key={paper.id} className={`paper-row ${paperId === paper.id ? 'active' : ''}`}>
        <label className="paper-checkbox"><input type="checkbox" aria-label={`选择 ${paper.title}`} checked={selected.includes(paper.id)} onChange={event => { setSelectedScope(true); setSelected(previous => event.target.checked ? [...previous, paper.id] : previous.filter(id => id !== paper.id)) }} /><span><Check size={12} /></span></label>
        <button className="paper-main" onClick={() => openPaper(paper.id)}><span className="paper-type"><FileText size={13} />PDF · {paper.pages} 页</span><h3>{paper.title}</h3><span className={`paper-state ${paper.status}`}><span className="status-dot" />{paperStatus[paper.status] || paper.status}{paper.chunk_count > 0 && <span className="chunk-count">{paper.chunk_count} 个片段</span>}</span></button>
        {paper.job && ['queued', 'running'].includes(paper.job.status) && <div className="paper-progress"><div className="progress-bar"><span style={{ width: `${paper.job.progress * 100}%` }} /></div><span title={paper.job.stage}>{paper.job.stage}</span></div>}
        {paper.error && <div className="paper-error"><span>{paper.error}</span><button onClick={() => void retry(paper)}>重试</button></div>}
      </article>)}{!papers.length && <div className="library-empty"><BookOpen size={26} /><p>从第一篇论文开始</p><span>原文、图表和证据会保存在这里</span></div>}{papers.length > 0 && !filteredPapers.length && <p className="muted library-empty">没有匹配的文献</p>}</div>
      <div className="library-footer"><ShieldCheck size={15} /><span>原文保留 · 回答可追溯</span><button aria-label="查看对话记录" title="对话记录" onClick={() => setHistoryOpen(true)}><MessageSquare size={17} /></button></div>
      {dragging && <div className="drop-overlay"><Upload size={32} /><strong>松开，导入文献</strong></div>}
    </aside>
    <main className="chat-panel">
      <div className="chat-header"><div><span className="eyebrow">ASK YOUR PAPERS</span><h2>{chatId ? chats.find(chat => chat.id === chatId)?.title || '文献对话' : '与文献对话'}</h2></div><div className="chat-actions"><button className="quiet-button" disabled={generating} onClick={() => { setChatId(null); setMessages([]); setActiveSource(null); setNotice('') }}><Plus size={15} />新对话</button><button className="icon-button" aria-label={previewOpen ? '收起证据预览' : '展开证据预览'} onClick={() => setPreviewOpen(!previewOpen)}>{previewOpen ? <PanelRightClose size={19} /> : <PanelRightOpen size={19} />}</button></div></div>
      {notice && <div className="notice"><AlertCircle size={15} /><span>{notice}</span><button className="icon-button" aria-label="关闭提示" onClick={() => setNotice('')}><X size={14} /></button></div>}
      <div className="conversation" ref={conversationRef}>
        {!messages.length && <div className="welcome"><div className="welcome-mark"><FlaskConical size={31} /></div><span className="eyebrow">从原文出发，沿证据深入</span><h1>把问题交给文献，<br />让答案有据可循。</h1><p>整理研究思路、比较实验结果、追问方法与局限。<br />每一个主要结论，都可以回到原文核对。</p><div className="question-suggestions">{suggestions.map((text, i) => <button key={text} onClick={() => setQuestion(text)}><span>{i === 0 ? <FlaskConical size={16} /> : i === 1 ? <FileText size={16} /> : <Search size={16} />}</span>{text}<ChevronRight size={15} /></button>)}</div><div className="welcome-note"><Quote size={14} /><span>证据不足时，会明确告诉你。</span></div></div>}
        {messages.map(message => <article key={message.id} className={`message ${message.role}`}><div className="message-label">{message.role === 'user' ? '我的问题' : <><span className="mini-brand"><BookOpen size={12} /></span>文献回答</>}{message.status === 'interrupted' && <span> · 已停止</span>}</div><div className="message-content"><ReactMarkdown remarkPlugins={[remarkGfm]} components={{ a: ({ href, children }) => href?.startsWith('#source-') ? <button className="inline-citation" onClick={() => { const source = message.sources.find(item => item.source_id === href.slice(8)); if (source) openSource(source) }}>{children}</button> : <a href={href} target="_blank" rel="noreferrer">{children}</a> }}>{message.content.replace(/\[(S\d+)\]/g, '[$1](#source-$1)')}</ReactMarkdown>{message.status === 'streaming' && !message.content && <div className="thinking"><span /><span /><span />{stage || '正在核对文献'}</div>}</div>
          {message.sources.length > 0 && <div className="source-strip"><span className="source-strip-label">原文证据</span>{message.sources.map(source => <button key={source.source_id} className={activeSource?.id === source.id ? 'active' : ''} onClick={() => openSource(source)}><span>{source.source_id}</span><span>{source.kind === 'figure' ? '图注' : source.kind === 'table' ? '表格' : '正文'} · 第 {source.pages.join('、')} 页</span><ChevronRight size={12} /></button>)}</div>}
        </article>)}
      </div>
      <div className="composer-area"><div className="scope-label"><BookOpen size={13} /><span>{selectedScope ? `在已选的 ${selected.length} 篇文献中提问` : `在全部 ${readyCount} 篇已入库文献中提问`}</span>{selectedScope && <button onClick={() => { setSelectedScope(false); setSelected([]) }}>切换到全部</button>}</div><div className={`composer ${generating ? 'busy' : ''}`}><textarea aria-label="输入文献问题" placeholder="输入你的问题，或继续追问…" value={question} onChange={event => setQuestion(event.target.value)} onKeyDown={event => { if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); void send() } }} rows={2} /><div className="composer-bottom"><span>{generating ? stage : 'Enter 发送 · Shift + Enter 换行'}</span>{generating ? <button className="send-button stop-button" aria-label="停止生成" onClick={() => abortRef.current?.abort()}><Square size={15} fill="currentColor" /></button> : <button className="send-button" aria-label="发送问题" disabled={!question.trim()} onClick={() => void send()}><ArrowUp size={20} /></button>}</div></div><p className="composer-footnote">回答基于所选文献。研究结论与数据，请结合原文核对。</p></div>
    </main>
    <aside className="evidence-panel"><div className="panel-heading"><div><span className="eyebrow">FOLLOW THE EVIDENCE</span><h2>原文与证据</h2></div><button className="icon-button mobile-close" aria-label="关闭原文预览" onClick={() => setPreviewOpen(false)}><X size={18} /></button></div>
      {paperId ? <><div className="preview-title"><FileText size={16} /><h3>{currentPaper?.title || activeSource?.title}</h3><button className="icon-button" aria-label="重新索引" title="重新索引" disabled={currentPaper?.job?.status === 'running' || currentPaper?.job?.status === 'queued'} onClick={() => currentPaper && void reindex(currentPaper)}><RotateCw size={14} /></button></div><div className="preview-tabs">{[['pdf', '原文'], ['text', '解析文本'], ['elements', '图表'], ['cleaning', '清洗记录']].map(([value, label]) => <button key={value} className={previewTab === value ? 'active' : ''} onClick={() => setPreviewTab(value)}>{label}</button>)}</div>
        {activeSource && <div className="evidence-excerpt"><span>{activeSource.source_id} · 第 {activeSource.pages.join('、')} 页</span><p>{activeSource.text.slice(0, 420)}{activeSource.text.length > 420 ? '…' : ''}</p></div>}
        {previewTab === 'pdf' && <PdfViewer documentId={paperId} pageNumber={pageNumber} onPageChange={setPageNumber} boxes={boxes} />}
        {previewTab !== 'pdf' && <div className="parsed-scroll">{parseError && <div className="notice">{parseError}</div>}{parsed && previewTab === 'text' && parsed.blocks.filter(block => !block.removed && block.kind === 'text').map(block => <div className={`text-block ${activeSource?.block_ids.includes(block.id) ? 'highlighted' : ''}`} key={block.id}><button className="page-marker" onClick={() => { setPageNumber(block.pages[0] || 1); setPreviewTab('pdf') }}>第 {block.pages.join('、')} 页</button><p className={['title', 'section_header'].includes(block.label) ? 'block-heading' : ''}>{block.text}</p></div>)}
        {parsed && previewTab === 'elements' && <>{previewBlocks.length === 0 && <p className="muted">这篇文献未识别到图表。</p>}{previewBlocks.map(block => <div className={`element-card ${activeSource?.block_ids.includes(block.id) ? 'highlighted' : ''}`} key={block.id}><div className="element-label">{block.kind === 'table' ? <Table2 size={15} /> : <ImageIcon size={15} />}<span>{block.kind === 'table' ? '表格' : '图片'} · 第 {block.pages.join('、')} 页</span><button onClick={() => { setPageNumber(block.pages[0] || 1); setPreviewTab('pdf') }}>查看原文</button></div>{block.asset_url && <img src={block.asset_url} alt={block.text || '文献图片'} loading="lazy" />}{block.table_html && <div className="table-scroll" dangerouslySetInnerHTML={{ __html: block.table_html }} />}<p>{block.text || '该图片没有识别到图注，仅供原文查看。'}</p>{block.warning && <small>{block.warning}</small>}</div>)}</>}
        {parsed && previewTab === 'cleaning' && <><div className="cleaning-summary">共 {parsed.cleaning_log.length} 处清洗或排版修复。原 PDF 保留。</div>{parsed.cleaning_log.map((entry, i) => <div className="cleaning-entry" key={i}><strong>{entry.reason}</strong><span>第 {entry.pages.join('、')} 页</span><div className="before-text">{entry.before}</div>{entry.after && <div className="after-text">{entry.after}</div>}</div>)}</>}
        </div>}</> : <div className="evidence-empty"><div className="paper-illustration"><div /><div /><Quote size={25} /></div><h3>每个答案，都有出处</h3><p>打开一篇文献，或点击回答中的引用，<br />在这里查看原文、图表与证据。</p><div className="evidence-steps"><span><FileText size={14} />原始 PDF</span><span><Quote size={14} />英文证据</span><span><ImageIcon size={14} />论文图表</span></div></div>}
    </aside>
    {settings && <div className="modal-backdrop" onClick={() => setSettings(null)}><section className="settings-modal" role="dialog" aria-modal="true" aria-label="系统设置" onClick={event => event.stopPropagation()}><div className="modal-heading"><div><span className="eyebrow">WORKSPACE SETTINGS</span><h2>运行与处理设置</h2></div><button className="icon-button" aria-label="关闭设置" onClick={() => setSettings(null)}><X size={20} /></button></div><p>问答连接配置在项目根目录的 .env 中填写，保存后重启服务。处理参数在 config.yaml 中调整，入库参数修改后对文献重新索引。</p><div className="settings-grid"><div><span>文献处理</span><strong>本机解析与向量化</strong></div><div><span>当前推理设备</span><strong>{health?.models.device || '待自检'}</strong></div><div><span>向量模型</span><strong>{settings.config.embedding.model}</strong></div><div><span>切片长度 / 重叠</span><strong>{settings.config.chunking.max_tokens} / {settings.config.chunking.overlap_tokens} tokens</strong></div><div><span>问答模型</span><strong>{settings.config.llm.model}</strong></div><div><span>问答连接</span><strong>{settings.llm_configured ? '已配置密钥' : '尚未配置密钥'}</strong></div></div>{!settings.llm_configured && <div className="setup-instruction"><strong>连接 DeepSeek</strong><p>编辑项目根目录的 .env，填写以下配置后保存并重启服务：</p><code>{'DEEPSEEK_BASE_URL=https://api.deepseek.com\nDEEPSEEK_API_KEY=your-key\nDEEPSEEK_MODEL=deepseek-flash'}</code><p>问题和检索片段会发送给你配置的问答服务。文献解析、向量化和存储在本机完成。</p></div>}<details><summary>查看完整配置</summary><pre>{JSON.stringify(settings.config, null, 2)}</pre></details></section></div>}
    {historyOpen && <div className="modal-backdrop" onClick={() => setHistoryOpen(false)}><section className="history-modal" role="dialog" aria-modal="true" aria-label="对话记录" onClick={event => event.stopPropagation()}><div className="modal-heading"><h2>对话记录</h2><button className="icon-button" aria-label="关闭对话记录" onClick={() => setHistoryOpen(false)}><X size={20} /></button></div>{!chats.length && <p className="muted">还没有保存的对话。</p>}{chats.map(chat => <button className="history-row" key={chat.id} disabled={generating} onClick={() => void openChat(chat.id)}><MessageSquare size={17} /><span>{chat.title}<small>{new Date(chat.updated_at).toLocaleString('zh-CN')}</small></span><ChevronRight size={15} /></button>)}</section></div>}
  </div>
}
