'use client'

import { useState, useEffect } from 'react'
import { useSocketContext } from '@/contexts/SocketContext'

export default function Header() {
  const { connected, systemStatus } = useSocketContext()
  const [isClient, setIsClient] = useState(false)
  
  // Prevent hydration mismatch by only rendering dynamic content after client mount
  useEffect(() => {
    setIsClient(true)
  }, [])
  
  const getConnectionStatus = (): 'connecting' | 'connected' | 'disconnected' => {
    if (!connected) return 'disconnected'
    if (connected && systemStatus?.cantina_os_connected) return 'connected'
    return 'connecting'
  }
  
  const connectionStatus = isClient ? getConnectionStatus() : 'disconnected'

  const getStatusColor = () => {
    switch (connectionStatus) {
      case 'connected':
        return 'sw-status-online'
      case 'connecting':
        return 'sw-status-warning'
      case 'disconnected':
        return 'sw-status-offline'
      default:
        return 'sw-status-offline'
    }
  }

  const getStatusText = () => {
    if (!isClient) return 'INITIALIZING...'
    switch (connectionStatus) {
      case 'connected':
        return 'CANTINA OS ONLINE'
      case 'connecting':
        return 'CONNECTING...'
      case 'disconnected':
        return 'CANTINA OS OFFLINE'
      default:
        return 'STATUS UNKNOWN'
    }
  }

  return (
    <header className="border-b border-sw-blue-600/30 bg-sw-dark-800/50 backdrop-blur-sm">
      <div className="container mx-auto px-4 py-4">
        <div className="flex items-center justify-between">
          {/* Logo and Title */}
          <div className="flex items-center space-x-4">
            <div className="text-2xl font-bold text-sw-blue-100 sw-text-glow">
              DJ R3X
            </div>
            <div className="text-sm text-sw-blue-300/70 uppercase tracking-wider">
              MONITORING DASHBOARD
            </div>
          </div>

          {/* Connection Status */}
          <div className="flex items-center space-x-3">
            <div className={`sw-status-indicator ${getStatusColor()}`}></div>
            <span className="text-sm font-medium text-sw-blue-200 uppercase tracking-wide">
              {getStatusText()}
            </span>
          </div>
        </div>
      </div>
    </header>
  )
}