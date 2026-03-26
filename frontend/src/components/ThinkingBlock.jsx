import { useState, useRef, useEffect } from 'react'

function ThinkingBlock({ steps, isStreaming, defaultExpanded }) {
  const [expanded, setExpanded] = useState(defaultExpanded)
  const bodyRef = useRef(null)

  useEffect(() => {
    if (isStreaming) {
      setExpanded(true)
    }
  }, [isStreaming])

  useEffect(() => {
    if (expanded && bodyRef.current) {
      bodyRef.current.scrollTop = bodyRef.current.scrollHeight
    }
  }, [steps, expanded])

  const thinkingCount = steps.filter(s => s.type === 'thinking').length
  const toolCount = steps.filter(s => s.type === 'tool').length
  const hasRunning = steps.some(s => s.type === 'tool' && s.status === 'running')

  return (
    <div className="thinking-block">
      <div className="thinking-header" onClick={() => setExpanded(!expanded)}>
        <span className={`thinking-toggle ${expanded ? 'expanded' : ''}`}>▶</span>
        <span className={`thinking-dot ${isStreaming && hasRunning ? 'active' : ''}`} />
        <span>
          思考过程
          {toolCount > 0 && ` · ${toolCount} 个工具调用`}
          {isStreaming && ' · 进行中...'}
        </span>
      </div>

      {expanded && (
        <div className="thinking-body" ref={bodyRef}>
          {steps.map((step, i) => {
            if (step.type === 'thinking') {
              return <div key={i} className="thinking-text">{step.content}</div>
            }
            if (step.type === 'tool') {
              return <ToolIndicator key={i} {...step} />
            }
            return null
          })}
        </div>
      )}
    </div>
  )
}

function ToolIndicator({ tool, args, status }) {
  const displayName = {
    read_file: '读取文件',
    write_file: '写入文件',
    make_dir: '创建目录',
    exists: '检查路径',
    run_python: '执行脚本',
    discover: '发现智能体',
    start_task: '启动任务',
    get_task: '轮询任务',
    continue_task: '继续任务',
    complete_task: '完成任务',
    cancel_task: '取消任务',
  }[tool] || tool

  let detail = ''
  if (tool === 'run_python' && args?.script_name) {
    detail = String(args.script_name)
  } else if ((tool === 'read_file' || tool === 'write_file') && args?.path) {
    detail = String(args.path)
  }

  return (
    <div className="tool-indicator">
      <span className={`tool-icon ${status}`}>
        {status === 'running' ? '⟳' : '✓'}
      </span>
      <span className="tool-name">{displayName}</span>
      {detail && <span className="tool-status" title={detail}>{detail}</span>}
    </div>
  )
}

export default ThinkingBlock
