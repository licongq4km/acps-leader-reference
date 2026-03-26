import { useRef, useEffect, useState } from 'react'
import ThinkingBlock from './ThinkingBlock'

function MessageList({ messages, streaming, isLoading }) {
  const endRef = useRef(null)

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, streaming])

  if (messages.length === 0 && !streaming) {
    return (
      <div className="message-list">
        <div className="empty-state">
          <div className="empty-state-icon">💬</div>
          <p>发送消息开始对话</p>
        </div>
      </div>
    )
  }

  return (
    <div className="message-list">
      {messages.map((msg, i) => (
        <div key={i} className="message-group">
          {msg.role === 'user' ? (
            <UserMessage content={msg.content} />
          ) : (
            <AssistantMessage
              steps={msg.steps}
              content={msg.content}
              responseType={msg.responseType}
              isStreaming={false}
            />
          )}
        </div>
      ))}

      {streaming && (
        <div className="message-group">
          <AssistantMessage
            steps={streaming.steps}
            content={streaming.content}
            responseType={streaming.responseType}
            isStreaming={true}
          />
        </div>
      )}

      {isLoading && !streaming && (
        <div className="message-group">
          <div className="msg-assistant">
            <span className="assistant-label">Agent</span>
            <div className="loading-dots">
              <span></span><span></span><span></span>
            </div>
          </div>
        </div>
      )}

      <div ref={endRef} />
    </div>
  )
}

function UserMessage({ content }) {
  return (
    <div className="msg-user">
      <div className="user-bubble">{content}</div>
    </div>
  )
}

function AssistantMessage({ steps, content, responseType, isStreaming }) {
  const hasSteps = steps && steps.length > 0
  const hasContent = content && content.trim()

  if (!hasSteps && !hasContent && !isStreaming) return null

  return (
    <div className="msg-assistant">
      <span className="assistant-label">Agent</span>

      {hasSteps && (
        <ThinkingBlock
          steps={steps}
          isStreaming={isStreaming}
          defaultExpanded={isStreaming || !hasContent}
        />
      )}

      {hasContent && (
        <div className={`agent-bubble ${responseType === 'error' ? 'error' : ''}`}>
          {content}
        </div>
      )}

      {isStreaming && !hasContent && !hasSteps && (
        <div className="loading-dots">
          <span></span><span></span><span></span>
        </div>
      )}
    </div>
  )
}

export default MessageList
