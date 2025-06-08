'use client'

import { useEffect, useState } from 'react'
import { useSocketContext } from '@/contexts/SocketContext'
import AudioSpectrum from '@/components/AudioSpectrum'

export default function MonitorTab() {
  const { 
    systemStatus, 
    lastTranscription,
    performanceMetrics,
    connected 
  } = useSocketContext()

  // Real-time transcription feed
  const [transcriptionFeed, setTranscriptionFeed] = useState<Array<{
    text: string
    timestamp: string
    final: boolean
  }>>([])

  // Add new transcriptions to feed
  useEffect(() => {
    if (lastTranscription) {
      setTranscriptionFeed(prev => {
        const newFeed = [...prev, {
          text: lastTranscription.text,
          timestamp: new Date().toISOString(),
          final: lastTranscription.final || false
        }]
        // Keep only last 10 transcriptions
        return newFeed.slice(-10)
      })
    }
  }, [lastTranscription])

  // Map service names from backend to display names
  const serviceDisplayMap = {
    'deepgram_direct_mic': { name: 'Voice Processing', details: 'Deepgram Direct Mic' },
    'gpt_service': { name: 'AI Response', details: 'OpenAI GPT Service' },
    'elevenlabs_service': { name: 'Speech Synthesis', details: 'ElevenLabs TTS' },
    'MusicController': { name: 'Music Controller', details: 'VLC Player Backend' },
    'eye_light_controller': { name: 'LED Controller', details: 'Arduino Connection' },
    'brain_service': { name: 'Brain Service', details: 'Main Logic Controller' }
  }
  return (
    <div className="space-y-6">
      {/* System Status Grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
        {Object.entries(serviceDisplayMap).map(([serviceId, config]) => {
          const serviceData = systemStatus?.services?.[serviceId]
          const status = serviceData?.status === 'online' ? 'online' : 
                        serviceData?.status === 'warning' ? 'warning' : 'offline'
          
          return (
            <ServiceCard 
              key={serviceId}
              name={config.name}
              status={status}
              details={config.details}
              uptime={serviceData?.uptime || '0:00:00'}
              lastUpdate={serviceData?.last_update}
            />
          )
        })}
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Audio Visualization */}
        <div className="sw-panel">
          <h3 className="text-lg font-semibold text-sw-blue-100 mb-4 sw-text-glow">
            AUDIO SPECTRUM
          </h3>
          <AudioSpectrum 
            height={128}
            className="h-32"
            isActive={connected && systemStatus?.cantina_os_connected}
          />
          <div className="mt-2 text-xs text-sw-blue-300/70 text-center">
            {connected && systemStatus?.cantina_os_connected 
              ? 'Real-time microphone frequency analysis'
              : 'Connect to CantinaOS to enable audio visualization'
            }
          </div>
        </div>

        {/* Live Transcription */}
        <div className="sw-panel">
          <h3 className="text-lg font-semibold text-sw-blue-100 mb-4 sw-text-glow">
            LIVE TRANSCRIPTION FEED
          </h3>
          <div className="h-32 bg-sw-dark-700/50 rounded-lg border border-sw-blue-600/20 p-4 overflow-y-auto">
            {transcriptionFeed.length > 0 ? (
              <div className="space-y-2">
                {transcriptionFeed.map((item, index) => (
                  <div key={index} className={`text-sm ${item.final ? 'text-sw-blue-100' : 'text-sw-blue-300/70'}`}>
                    <span className="text-xs text-sw-blue-400 mr-2">
                      {new Date(item.timestamp).toLocaleTimeString()}
                    </span>
                    {item.text}
                    {!item.final && <span className="text-sw-blue-500"> [interim]</span>}
                  </div>
                ))}
              </div>
            ) : (
              <div className="text-sw-blue-300/50 text-sm">
                {connected ? 'Waiting for voice activity...' : 'Connect to CantinaOS to see transcriptions'}
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Performance Metrics */}
      <div className="sw-panel">
        <h3 className="text-lg font-semibold text-sw-blue-100 mb-4 sw-text-glow">
          SYSTEM PERFORMANCE
        </h3>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          <MetricCard 
            label="CantinaOS Status" 
            value={systemStatus?.cantina_os_connected ? "ONLINE" : "OFFLINE"}
            valueClass={systemStatus?.cantina_os_connected ? "text-sw-green" : "text-sw-red"}
          />
          <MetricCard 
            label="Dashboard Clients" 
            value={systemStatus?.dashboard_clients?.toString() || "0"} 
          />
          <MetricCard 
            label="Events/Min" 
            value={performanceMetrics?.events_per_minute?.toString() || "0"} 
          />
          <MetricCard 
            label="Last Update" 
            value={systemStatus?.timestamp ? new Date(systemStatus.timestamp).toLocaleTimeString() : "--:--:--"} 
          />
        </div>
      </div>

      {/* Connection Status Banner */}
      {!connected && (
        <div className="sw-panel border-sw-red/50 bg-sw-red/10">
          <div className="flex items-center justify-center space-x-2">
            <div className="w-3 h-3 bg-sw-red rounded-full animate-pulse"></div>
            <span className="text-sw-red font-semibold">
              Bridge Service Disconnected - Attempting Reconnection...
            </span>
          </div>
        </div>
      )}
    </div>
  )
}

interface ServiceCardProps {
  name: string
  status: 'online' | 'offline' | 'warning'
  details: string
  uptime?: string
  lastUpdate?: string
}

function ServiceCard({ name, status, details, uptime, lastUpdate }: ServiceCardProps) {
  const getStatusClass = () => {
    switch (status) {
      case 'online':
        return 'sw-status-online'
      case 'warning':
        return 'sw-status-warning'
      case 'offline':
      default:
        return 'sw-status-offline'
    }
  }

  const getStatusText = () => {
    switch (status) {
      case 'online':
        return 'ONLINE'
      case 'warning':
        return 'WARNING'
      case 'offline':
      default:
        return 'OFFLINE'
    }
  }

  return (
    <div className="sw-terminal-border p-4">
      <div className="flex items-center justify-between mb-2">
        <h4 className="font-medium text-sw-blue-100">{name}</h4>
        <div className={`sw-status-indicator ${getStatusClass()}`}></div>
      </div>
      <p className="text-xs text-sw-blue-300/70 mb-2">{details}</p>
      
      <div className="space-y-1">
        <p className="text-xs font-semibold text-sw-blue-200 uppercase tracking-wide">
          {getStatusText()}
        </p>
        {uptime && (
          <p className="text-xs text-sw-blue-300/70">
            Uptime: {uptime}
          </p>
        )}
        {lastUpdate && (
          <p className="text-xs text-sw-blue-300/50">
            Updated: {new Date(lastUpdate).toLocaleTimeString()}
          </p>
        )}
      </div>
    </div>
  )
}

interface MetricCardProps {
  label: string
  value: string
  valueClass?: string
}

function MetricCard({ label, value, valueClass = "text-sw-blue-100" }: MetricCardProps) {
  return (
    <div className="text-center">
      <div className={`text-2xl font-bold sw-text-glow ${valueClass}`}>{value}</div>
      <div className="text-xs text-sw-blue-300/70 uppercase tracking-wide">{label}</div>
    </div>
  )
}