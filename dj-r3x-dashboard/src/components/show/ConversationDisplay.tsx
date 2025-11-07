'use client'

import { useState, useEffect, useRef } from 'react'
import { useSocketContext } from '../../contexts/SocketContext'

interface ConversationMessage {
  id: string
  speaker: 'visitor' | 'dj_r3x'
  text: string
  timestamp: Date
  isAnimating?: boolean
}

export default function ConversationDisplay() {
  const { socket } = useSocketContext()
  const [messages, setMessages] = useState<ConversationMessage[]>([])
  const [currentResponse, setCurrentResponse] = useState<string>('')
  const [isTyping, setIsTyping] = useState(false)
  const messagesEndRef = useRef<HTMLDivElement>(null)

  // Auto-scroll to bottom when new messages arrive
  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }

  useEffect(() => {
    scrollToBottom()
  }, [messages, currentResponse])

  // CantinaOS event integration
  useEffect(() => {
    if (!socket) return

    const handleTranscriptionFinal = (data: any) => {
      if (data.text || data.transcript) {
        const newMessage: ConversationMessage = {
          id: `visitor-${Date.now()}`,
          speaker: 'visitor',
          text: data.text || data.transcript,
          timestamp: new Date(),
          isAnimating: true
        }
        
        setMessages(prev => [...prev, newMessage])
        
        // Remove animation flag after animation completes
        setTimeout(() => {
          setMessages(prev => 
            prev.map(msg => 
              msg.id === newMessage.id ? { ...msg, isAnimating: false } : msg
            )
          )
        }, 1000)
      }
    }

    const handleLLMResponse = (data: any) => {
      const responseText = data.response || data.text || data.content || ''
      
      if (responseText) {
        setIsTyping(true)
        setCurrentResponse('')
        
        // Simulate typewriter effect
        let index = 0
        const typeText = () => {
          if (index < responseText.length) {
            setCurrentResponse(responseText.slice(0, index + 1))
            index++
            setTimeout(typeText, 30) // Adjust speed here
          } else {
            // Complete message, add to permanent conversation
            const newMessage: ConversationMessage = {
              id: `dj_r3x-${Date.now()}`,
              speaker: 'dj_r3x',
              text: responseText,
              timestamp: new Date()
            }
            
            setMessages(prev => [...prev, newMessage])
            setCurrentResponse('')
            setIsTyping(false)
          }
        }
        
        typeText()
      }
    }

    socket.on('transcription_final', handleTranscriptionFinal)
    socket.on('llm_response', handleLLMResponse)

    return () => {
      socket.off('transcription_final', handleTranscriptionFinal)
      socket.off('llm_response', handleLLMResponse)
    }
  }, [socket])

  return (
    <div className="h-full w-full flex flex-col bg-transparent">
      {/* Conversation area with rounded message bubbles like reference */}
      <div className="flex-1 p-4 overflow-y-auto">
        {messages.length === 0 && !currentResponse && (
          <div className="h-full flex items-center justify-center">
            <div className="text-center">
              <div className="text-cyan-400 font-mono text-lg mb-4">AWAITING INTERACTION</div>
              <div className="text-yellow-400 font-mono text-sm">
                Conversation will appear here when guests interact with DJ R3X
              </div>
            </div>
          </div>
        )}

        {/* Render conversation messages in large, readable format */}
        {messages.map((message) => (
          <div key={message.id} className="mb-6">
            {/* Speaker identifier */}
            <div className="mb-2">
              <span className={`font-mono font-bold text-sm ${
                message.speaker === 'visitor' ? 'text-yellow-400' : 'text-cyan-400'
              }`}>
                {message.speaker === 'visitor' ? '● VISITOR' : '● DJ R3X (R3X)'}
              </span>
              <span className="text-slate-400 font-mono text-xs ml-4">
                {message.timestamp.toLocaleTimeString()}
              </span>
            </div>
            
            {/* Message content - large and prominent like reference */}
            <div className={`
              text-lg leading-relaxed font-mono p-4 rounded-lg
              ${message.speaker === 'visitor' 
                ? 'bg-slate-700 border-l-4 border-yellow-400 text-white' 
                : 'bg-slate-600 border-l-4 border-cyan-400 text-yellow-400'
              }
            `}>
              {message.text}
            </div>
          </div>
        ))}

        {/* Current typing response */}
        {currentResponse && (
          <div className="mb-6">
            <div className="mb-2">
              <span className="text-cyan-400 font-mono font-bold text-sm">
                ● DJ R3X (R3X)
              </span>
              <span className="text-slate-400 font-mono text-xs ml-4">
                RESPONDING...
              </span>
            </div>
            
            <div className="text-lg leading-relaxed font-mono p-4 rounded-lg bg-slate-600 border-l-4 border-cyan-400 text-yellow-400">
              {currentResponse}
              <span className="animate-pulse">|</span>
            </div>
          </div>
        )}

        <div ref={messagesEndRef} />
      </div>
    </div>
  )
}