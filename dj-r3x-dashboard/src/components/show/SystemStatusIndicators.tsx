'use client'

import { useState, useEffect } from 'react'
import { useSocketContext } from '../../contexts/SocketContext'

interface ServiceStatus {
  name: string
  status: 'RUNNING' | 'STOPPED' | 'ERROR' | 'STARTING' | 'STOPPING'
  uptime?: number
  lastUpdate?: Date
}

export default function SystemStatusIndicators() {
  const { socket } = useSocketContext()
  const [services, setServices] = useState<Record<string, ServiceStatus>>({})
  const [showDetails, setShowDetails] = useState(false)

  // Key services to monitor (simplified for show interface)
  const keyServices = [
    'DeepgramDirectMicService',
    'GPTService', 
    'ElevenLabsService',
    'MusicControllerService',
    'BrainService'
  ]

  useEffect(() => {
    if (!socket) return

    const handleServiceStatus = (data: any) => {
      const serviceName = data.service_name || data.service || 'Unknown'
      const status = data.status || 'UNKNOWN'
      
      setServices(prev => ({
        ...prev,
        [serviceName]: {
          name: serviceName,
          status: status,
          uptime: data.uptime,
          lastUpdate: new Date()
        }
      }))
    }

    socket.on('service_status_update', handleServiceStatus)

    return () => {
      socket.off('service_status_update', handleServiceStatus)
    }
  }, [socket])

  const getStatusColor = (status: string) => {
    switch (status) {
      case 'RUNNING':
        return 'bg-sw-green'
      case 'ERROR':
        return 'bg-sw-red'
      case 'STARTING':
      case 'STOPPING':
        return 'bg-sw-yellow animate-pulse'
      case 'STOPPED':
      default:
        return 'bg-sw-blue-400/40'
    }
  }

  const getStatusSymbol = (status: string) => {
    switch (status) {
      case 'RUNNING':
        return '●'
      case 'ERROR':
        return '◉'
      case 'STARTING':
      case 'STOPPING':
        return '◐'
      case 'STOPPED':
      default:
        return '○'
    }
  }

  const getShortServiceName = (serviceName: string) => {
    const nameMap: Record<string, string> = {
      'DeepgramDirectMicService': 'MIC',
      'GPTService': 'AI',
      'ElevenLabsService': 'TTS',
      'MusicControllerService': 'MUS',
      'BrainService': 'BRAIN',
      'WebBridgeService': 'WEB',
      'EyeLightControllerService': 'LED'
    }
    
    return nameMap[serviceName] || serviceName.substring(0, 3).toUpperCase()
  }

  // Calculate overall system health
  const runningServices = keyServices.filter(service => 
    services[service]?.status === 'RUNNING'
  ).length
  
  const systemHealth = keyServices.length > 0 ? 
    Math.round((runningServices / keyServices.length) * 100) : 0

  return (
    <div className="relative">
      {/* Enhanced compact indicators with Star Wars styling */}
      <div 
        className="sw-cantina-panel border border-sw-blue-600/30 rounded-lg p-3 cursor-pointer sw-holo-flicker sw-radar-sweep"
        onMouseEnter={() => setShowDetails(true)}
        onMouseLeave={() => setShowDetails(false)}
      >
        <div className="flex items-center space-x-4">
          {/* Enhanced targeting reticle for system health */}
          <div className="flex items-center space-x-2">
            <div className="sw-targeting-reticle w-3 h-3"></div>
            <div className={`w-3 h-3 rounded-full ${
              systemHealth >= 80 ? 'sw-status-cantina' :
              systemHealth >= 60 ? 'sw-status-targeting' :
              'sw-status-critical'
            } ${systemHealth < 100 ? 'sw-system-blink' : ''}`}></div>
            <span className="text-xs font-mono sw-color-cantina sw-terminal-text">
              SYS
            </span>
          </div>

          {/* Key service indicators */}
          <div className="flex items-center space-x-1">
            {keyServices.slice(0, 4).map(serviceName => {
              const service = services[serviceName]
              const status = service?.status || 'STOPPED'
              
              return (
                <div key={serviceName} className="flex items-center sw-event-ripple">
                  <span className={`
                    text-sm font-mono sw-terminal-text
                    ${status === 'RUNNING' ? 'sw-color-cantina' :
                      status === 'ERROR' ? 'sw-color-warning' :
                      status === 'STARTING' || status === 'STOPPING' ? 'sw-color-targeting' :
                      'text-sw-blue-400/40'
                    }
                    ${status !== 'RUNNING' && status !== 'STOPPED' ? 'sw-signal-glitch' : ''}
                  `}>
                    {getStatusSymbol(status)}
                  </span>
                </div>
              )
            })}
          </div>

          {/* Enhanced health percentage with targeting system styling */}
          <span className="text-xs font-mono sw-color-targeting sw-terminal-text">
            {systemHealth}% OP
          </span>
        </div>
      </div>

      {/* Enhanced detailed status popup with Star Wars styling */}
      {showDetails && (
        <div className="absolute top-full right-0 mt-2 w-72 sw-cantina-panel border border-sw-blue-600/50 rounded-lg shadow-lg z-50 sw-holo-flicker">
          <div className="p-4">
            <div className="flex items-center space-x-2 mb-4">
              <div className="sw-targeting-reticle w-3 h-3"></div>
              <h3 className="text-sm font-mono sw-color-cantina tracking-wider sw-terminal-text">
                CANTINA SYSTEM STATUS • REAL-TIME
              </h3>
            </div>
            
            <div className="space-y-2">
              {keyServices.map(serviceName => {
                const service = services[serviceName]
                const status = service?.status || 'UNKNOWN'
                
                return (
                  <div key={serviceName} className="flex items-center justify-between">
                    <div className="flex items-center space-x-2">
                      <div className={`w-2 h-2 rounded-full ${getStatusColor(status)}`}></div>
                      <span className="text-xs font-mono text-sw-blue-200">
                        {getShortServiceName(serviceName)}
                      </span>
                    </div>
                    <span className={`text-xs font-mono ${
                      status === 'RUNNING' ? 'text-sw-green' :
                      status === 'ERROR' ? 'text-sw-red' :
                      'text-sw-yellow'
                    }`}>
                      {status}
                    </span>
                  </div>
                )
              })}
            </div>

            <div className="mt-3 pt-2 border-t border-sw-blue-600/30">
              <div className="flex items-center justify-between text-xs font-mono">
                <span className="text-sw-blue-400">OVERALL HEALTH:</span>
                <span className={`${
                  systemHealth >= 80 ? 'text-sw-green' :
                  systemHealth >= 60 ? 'text-sw-yellow' :
                  'text-sw-red'
                }`}>
                  {systemHealth}% OPERATIONAL
                </span>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}