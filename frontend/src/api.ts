export interface Job { id: string; status: string; stage: string; progress: number; error?: string }
export interface Paper { id: string; title: string; filename: string; status: string; pages: number; chunk_count: number; active_index?: string; error?: string; job?: Job }
export interface Position { page: number; bbox: number[]; width: number; height: number }
export interface Source { id: string; source_id: string; document_id: string; index_id: string; title: string; text: string; section: string; kind: string; pages: number[]; boxes: Position[]; block_ids: string[]; score: number }
export interface Block { id: string; kind: string; label: string; raw_text: string; text: string; pages: number[]; positions: Position[]; removed: boolean; cleaning_reason?: string; asset_url?: string; table_html?: string; warning?: string }
export interface Parsed { title: string; pages: number; figures: number; tables: number; blocks: Block[]; index_id: string; cleaning_log: { reason: string; before: string; after: string; pages: number[] }[] }
export interface Message { id: string; role: string; content: string; status: string; sources: Source[] }
export interface Chat { id: string; title: string; updated_at: string }
export interface Health { status: string; llm_configured: boolean; llm_model: string; pending_jobs: number; models: { embedding: string; reranker: string; device: string; warning?: string } }

export async function api<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch('/api' + path, options)
  if (!response.ok) {
    const data = await response.json().catch(() => ({}))
    throw new Error(typeof data.detail === 'string' ? data.detail : `请求失败（${response.status}）`)
  }
  return response.json() as Promise<T>
}

export async function consumeEvents(response: Response, callback: (event: string, data: any) => void) {
  if (!response.ok) {
    const data = await response.json().catch(() => ({}))
    throw new Error(data.detail || '无法开始问答')
  }
  const reader = response.body!.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  while (true) {
    const { value, done } = await reader.read()
    buffer += decoder.decode(value, { stream: !done }).replace(/\r\n/g, '\n')
    let boundary: number
    while ((boundary = buffer.indexOf('\n\n')) !== -1) {
      const frame = buffer.slice(0, boundary)
      buffer = buffer.slice(boundary + 2)
      const event = frame.split('\n').find(line => line.startsWith('event:'))?.slice(6).trim() || 'message'
      const payload = frame.split('\n').filter(line => line.startsWith('data:')).map(line => line.slice(5).trim()).join('\n')
      if (payload) callback(event, JSON.parse(payload))
    }
    if (done) break
  }
}
