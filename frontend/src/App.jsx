import { useState, useRef, useCallback } from 'react'
import ChatInput from './components/ChatInput'
import MessageList from './components/MessageList'
import './App.css'

const API_BASE = import.meta.env.VITE_API_BASE_URL || ''
const SKILL_PATH = import.meta.env.VITE_SKILL_CHAT_PATH || '/api/skill/chat/stream'
const MCP_PATH = import.meta.env.VITE_MCP_CHAT_PATH || '/api/mcp/chat/stream'

const MODE_CONFIG = {
  skill: {
    title: 'ACPS Leader Agent',
    badge: 'Powered by ACPs',
    chatPath: SKILL_PATH,
    themeClass: 'mode-skill',
  },
  mcp: {
    title: 'ACPS Leader Agent',
    badge: 'Powered by ACPs',
    chatPath: MCP_PATH,
    themeClass: 'mode-mcp',
  },
}

function generateId() {
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, c => {
    const r = Math.random() * 16 | 0
    return (c === 'x' ? r : (r & 0x3 | 0x8)).toString(16)
  })
}

function App() {
  const [mode, setMode] = useState('skill')
  const [messagesByMode, setMessagesByMode] = useState({ skill: [], mcp: [] })
  const [streaming, setStreaming] = useState(null)
  const [isLoading, setIsLoading] = useState(false)
  const [threadIds] = useState(() => ({ skill: generateId(), mcp: generateId() }))
  const abortRef = useRef(null)
  const streamRef = useRef({ steps: [], content: '', responseType: 'info' })
  const rafRef = useRef(null)

  const messages = messagesByMode[mode]
  const config = MODE_CONFIG[mode]

  const switchMode = useCallback((newMode) => {
    if (newMode === mode || isLoading) return
    setMode(newMode)
    setStreaming(null)
  }, [mode, isLoading])

  const requestUIUpdate = useCallback(() => {
    if (rafRef.current) return
    rafRef.current = requestAnimationFrame(() => {
      setStreaming({
        steps: [...streamRef.current.steps],
        content: streamRef.current.content,
        responseType: streamRef.current.responseType,
      })
      rafRef.current = null
    })
  }, [])

  const handleSSEEvent = useCallback((type, data) => {
    const s = streamRef.current
    switch (type) {
      case 'thinking': {
        const last = s.steps[s.steps.length - 1]
        if (last && last.type === 'thinking') {
          last.content += data.content
        } else {
          s.steps.push({ type: 'thinking', content: data.content })
        }
        requestUIUpdate()
        break
      }
      case 'tool_start':
        s.steps.push({
          type: 'tool',
          tool: data.tool,
          args: data.args || {},
          status: 'running',
          result: '',
        })
        requestUIUpdate()
        break
      case 'tool_end':
        for (let i = s.steps.length - 1; i >= 0; i--) {
          if (s.steps[i].type === 'tool' && s.steps[i].tool === data.tool && s.steps[i].status === 'running') {
            s.steps[i].status = 'done'
            s.steps[i].result = data.result || ''
            break
          }
        }
        requestUIUpdate()
        break
      case 'message':
        s.content += data.content
        requestUIUpdate()
        break
      case 'message_end':
        s.responseType = data.response_type || 'info'
        requestUIUpdate()
        break
      case 'error':
        s.content = data.message
        s.responseType = 'error'
        requestUIUpdate()
        break
      case 'done':
        break
      default:
        break
    }
  }, [requestUIUpdate])

  const sendMessage = useCallback(async (text) => {
    if (!text.trim() || isLoading) return

    const currentMode = mode
    setMessagesByMode(prev => ({
      ...prev,
      [currentMode]: [...prev[currentMode], { role: 'user', content: text }],
    }))
    setIsLoading(true)
    streamRef.current = { steps: [], content: '', responseType: 'info' }
    setStreaming({ steps: [], content: '', responseType: 'info' })

    try {
      const controller = new AbortController()
      abortRef.current = controller

      const chatPath = MODE_CONFIG[currentMode].chatPath
      const threadId = threadIds[currentMode]

      const response = await fetch(`${API_BASE}${chatPath}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: text, thread_id: threadId }),
        signal: controller.signal,
      })

      if (!response.ok) {
        throw new Error(`HTTP ${response.status}: ${response.statusText}`)
      }

      const reader = response.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''

      while (true) {
        const { done, value } = await reader.read()
        if (done) break

        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split('\n')
        buffer = lines.pop() || ''

        let eventType = ''
        for (const line of lines) {
          if (line.startsWith('event: ')) {
            eventType = line.slice(7).trim()
          } else if (line.startsWith('data: ') && eventType) {
            try {
              const data = JSON.parse(line.slice(6))
              handleSSEEvent(eventType, data)
            } catch {
              // ignore parse errors
            }
            eventType = ''
          }
        }
      }
    } catch (err) {
      if (err.name !== 'AbortError') {
        console.error('Stream error:', err)
        streamRef.current.content = `连接错误: ${err.message}`
        streamRef.current.responseType = 'error'
        requestUIUpdate()
      }
    } finally {
      if (rafRef.current) {
        cancelAnimationFrame(rafRef.current)
        rafRef.current = null
      }

      const final = { ...streamRef.current, steps: [...streamRef.current.steps] }

      if (final.content) {
        const thinkingText = final.steps
          .filter(s => s.type === 'thinking')
          .map(s => s.content)
          .join('')
        if (thinkingText && final.content === thinkingText) {
          final.steps = final.steps.filter(s => s.type !== 'thinking')
        }
      }

      setMessagesByMode(prev => ({
        ...prev,
        [currentMode]: [...prev[currentMode], {
          role: 'assistant',
          steps: final.steps,
          content: final.content,
          responseType: final.responseType,
        }],
      }))
      setStreaming(null)
      setIsLoading(false)
    }
  }, [isLoading, mode, threadIds, handleSSEEvent, requestUIUpdate])

  const stopStreaming = useCallback(() => {
    if (abortRef.current) {
      abortRef.current.abort()
      abortRef.current = null
    }
  }, [])

  return (
    <div className={`app ${config.themeClass}`}>
      <header className="app-header">
        <div className="header-content">
          <h1>{config.title}</h1>
          <span className="header-badge">{config.badge}</span>
        </div>
        <div className="mode-switcher">
          <button
            className={`mode-btn ${mode === 'skill' ? 'active' : ''}`}
            onClick={() => switchMode('skill')}
            disabled={isLoading}
          >
            Agent Skill
          </button>
          <button
            className={`mode-btn ${mode === 'mcp' ? 'active' : ''}`}
            onClick={() => switchMode('mcp')}
            disabled={isLoading}
          >
            MCP Server
          </button>
        </div>
      </header>
      <MessageList
        messages={messages}
        streaming={streaming}
        isLoading={isLoading}
      />
      <ChatInput
        onSend={sendMessage}
        onStop={stopStreaming}
        isLoading={isLoading}
      />
    </div>
  )
}

export default App
