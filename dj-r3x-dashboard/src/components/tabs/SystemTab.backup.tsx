'use client'

import { useState, useEffect, useRef } from 'react'
import { useSocketContext } from '../../contexts/SocketContext'
import SystemModeControl from '../SystemModeControl'

interface ServiceData {
  name: string
  status: string
  uptime: string
  memory: string
  cpu: string
  lastActivity: string
  errorCount: number
  successRate: number
}

interface LogEntry {
  timestamp: string
  level: 'DEBUG' | 'INFO' | 'WARNING' | 'ERROR'
  service: string
  message: string
  id: string
}

interface SystemMetrics {
  totalMemory: number
  cpuUsage: number
  eventLatency: number
  errorRate: number
  eventsPerMinute: number
  uptime: string
  activeServices: number
  totalServices: number
}

interface ConfigStatus {
  openai: boolean
  elevenlabs: boolean
  deepgram: boolean
  arduino: boolean
}

interface AlertNotification {
  id: string
  type: 'error' | 'warning' | 'info' | 'success'
  title: string
  message: string
  timestamp: Date
  dismissed: boolean
  persistent?: boolean
}

// Helper function to convert service names to display names
const getServiceDisplayName = (serviceName: string): string => {
  const nameMap: Record<string, string> = {
    'web_bridge': 'Web Bridge',
    'debug': 'Debug Service',
    'yoda_mode_manager': 'Mode Manager',
    'mode_command_handler': 'Mode Command Handler',
    'memory_service': 'Memory Service',
    'mouse_input': 'Mouse Input',
    'deepgram_direct_mic': 'Voice Input (Deepgram)',
    'gpt_service': 'AI Assistant (GPT)',
    'intent_router_service': 'Intent Router',
    'brain_service': 'Brain Service',
    'timeline_executor_service': 'Timeline Executor',
    'elevenlabs_service': 'Speech Synthesis (ElevenLabs)',
    'cached_speech_service': 'Cached Speech',
    'mode_change_sound': 'Mode Change Sound',
    'MusicController': 'Music Controller',
    'eye_light_controller': 'Eye Light Controller',
    'cli': 'CLI Service',
    'command_dispatcher': 'Command Dispatcher',
  }
  return nameMap[serviceName] || serviceName
}

export default function SystemTab() {
  const { socket, systemMode, sendSystemCommand } = useSocketContext()
  const [logLevel, setLogLevel] = useState<'DEBUG' | 'INFO' | 'WARNING' | 'ERROR'>('INFO')
  const [selectedService, setSelectedService] = useState<string>('all')
  const [services, setServices] = useState<ServiceData[]>([
    { name: 'web_bridge', status: 'offline', uptime: '--', memory: '--', cpu: '--', lastActivity: '--', errorCount: 0, successRate: 0 },
    { name: 'debug', status: 'offline', uptime: '--', memory: '--', cpu: '--', lastActivity: '--', errorCount: 0, successRate: 0 },
    { name: 'yoda_mode_manager', status: 'offline', uptime: '--', memory: '--', cpu: '--', lastActivity: '--', errorCount: 0, successRate: 0 },
    { name: 'mode_command_handler', status: 'offline', uptime: '--', memory: '--', cpu: '--', lastActivity: '--', errorCount: 0, successRate: 0 },
    { name: 'memory_service', status: 'offline', uptime: '--', memory: '--', cpu: '--', lastActivity: '--', errorCount: 0, successRate: 0 },
    { name: 'mouse_input', status: 'offline', uptime: '--', memory: '--', cpu: '--', lastActivity: '--', errorCount: 0, successRate: 0 },
    { name: 'deepgram_direct_mic', status: 'offline', uptime: '--', memory: '--', cpu: '--', lastActivity: '--', errorCount: 0, successRate: 0 },
    { name: 'gpt_service', status: 'offline', uptime: '--', memory: '--', cpu: '--', lastActivity: '--', errorCount: 0, successRate: 0 },
    { name: 'intent_router_service', status: 'offline', uptime: '--', memory: '--', cpu: '--', lastActivity: '--', errorCount: 0, successRate: 0 },
    { name: 'brain_service', status: 'offline', uptime: '--', memory: '--', cpu: '--', lastActivity: '--', errorCount: 0, successRate: 0 },
    { name: 'timeline_executor_service', status: 'offline', uptime: '--', memory: '--', cpu: '--', lastActivity: '--', errorCount: 0, successRate: 0 },
    { name: 'elevenlabs_service', status: 'offline', uptime: '--', memory: '--', cpu: '--', lastActivity: '--', errorCount: 0, successRate: 0 },
    { name: 'cached_speech_service', status: 'offline', uptime: '--', memory: '--', cpu: '--', lastActivity: '--', errorCount: 0, successRate: 0 },
    { name: 'mode_change_sound', status: 'offline', uptime: '--', memory: '--', cpu: '--', lastActivity: '--', errorCount: 0, successRate: 0 },
    { name: 'MusicController', status: 'offline', uptime: '--', memory: '--', cpu: '--', lastActivity: '--', errorCount: 0, successRate: 0 },
    { name: 'eye_light_controller', status: 'offline', uptime: '--', memory: '--', cpu: '--', lastActivity: '--', errorCount: 0, successRate: 0 },
    { name: 'cli', status: 'offline', uptime: '--', memory: '--', cpu: '--', lastActivity: '--', errorCount: 0, successRate: 0 },
    { name: 'command_dispatcher', status: 'offline', uptime: '--', memory: '--', cpu: '--', lastActivity: '--', errorCount: 0, successRate: 0 },
  ])
  
  const [logs, setLogs] = useState<LogEntry[]>([])
  const [filteredLogs, setFilteredLogs] = useState<LogEntry[]>([])
  const [systemMetrics, setSystemMetrics] = useState<SystemMetrics>({
    totalMemory: 0,
    cpuUsage: 0,
    eventLatency: 0,
    errorRate: 0,
    eventsPerMinute: 0,
    uptime: '--:--:--',
    activeServices: 0,
    totalServices: 18
  })
  const [configStatus, setConfigStatus] = useState<ConfigStatus>({
    openai: false,
    elevenlabs: false,
    deepgram: false,
    arduino: false
  })
  const [searchTerm, setSearchTerm] = useState('')
  const [alerts, setAlerts] = useState<AlertNotification[]>([])
  const [showDismissedAlerts, setShowDismissedAlerts] = useState(false)
  const [userScrolledUp, setUserScrolledUp] = useState(false)
  const logsEndRef = useRef<HTMLDivElement>(null)
  const logContainerRef = useRef<HTMLDivElement>(null)
  const maxLogs = 1000 // Keep last 1000 log entries
  const maxAlerts = 50 // Keep last 50 alerts
  const logDedupeCache = useRef<Set<string>>(new Set())

  // Alert management functions
  const createAlert = (type: AlertNotification['type'], title: string, message: string, persistent = false) => {
    const alert: AlertNotification = {
      id: Date.now() + Math.random().toString(),
      type,
      title,
      message,
      timestamp: new Date(),
      dismissed: false,
      persistent
    }
    
    setAlerts(prev => {
      const newAlerts = [alert, ...prev]
      return newAlerts.slice(0, maxAlerts)
    })

    // Auto-dismiss non-persistent alerts after 10 seconds
    if (!persistent && typeof window !== 'undefined') {
      const timeoutId = setTimeout(() => {
        dismissAlert(alert.id)
      }, 10000)
      
      // Store timeout ID for cleanup if needed
      ;(alert as any).timeoutId = timeoutId
    }
  }

  const dismissAlert = (alertId: string) => {
    setAlerts(prev => 
      prev.map(alert => {
        if (alert.id === alertId) {
          // Clear timeout if exists
          if ((alert as any).timeoutId) {
            clearTimeout((alert as any).timeoutId)
          }
          return { ...alert, dismissed: true }
        }
        return alert
      })
    )
  }

  const clearAllAlerts = () => {
    setAlerts(prev => prev.map(alert => {
      // Clear any pending timeouts
      if ((alert as any).timeoutId) {
        clearTimeout((alert as any).timeoutId)
      }
      return { ...alert, dismissed: true }
    }))
  }

  const deleteAlert = (alertId: string) => {
    setAlerts(prev => prev.filter(alert => alert.id !== alertId))
  }

  // Check for system issues and create alerts
  useEffect(() => {
    // Only create alerts if we have meaningful metrics (not initial zero values)
    if (systemMetrics.cpuUsage === 0 && systemMetrics.totalMemory === 0) {
      return
    }

    // Debounce alert creation to prevent duplicate alerts
    const alertTimeout = setTimeout(() => {
      // CPU Usage Alert
      if (systemMetrics.cpuUsage > 90) {
        createAlert('error', 'Critical CPU Usage', `CPU usage is at ${systemMetrics.cpuUsage.toFixed(1)}%. System performance may be severely impacted.`, true)
      } else if (systemMetrics.cpuUsage > 80) {
        createAlert('warning', 'High CPU Usage', `CPU usage is at ${systemMetrics.cpuUsage.toFixed(1)}%. Consider reviewing running services.`)
      }

      // Memory Usage Alert
      if (systemMetrics.totalMemory > 2000) {
        createAlert('error', 'Critical Memory Usage', `Memory usage is at ${systemMetrics.totalMemory} MB. Risk of system instability.`, true)
      } else if (systemMetrics.totalMemory > 1000) {
        createAlert('warning', 'High Memory Usage', `Memory usage is at ${systemMetrics.totalMemory} MB. Monitor for potential memory leaks.`)
      }

      // Response Time Alert
      if (systemMetrics.eventLatency > 1000) {
        createAlert('error', 'Critical Response Latency', `Average response time is ${systemMetrics.eventLatency}ms. System may be unresponsive.`, true)
      } else if (systemMetrics.eventLatency > 500) {
        createAlert('warning', 'High Response Latency', `Average response time is ${systemMetrics.eventLatency}ms. Performance degraded.`)
      }

      // Error Rate Alert
      if (systemMetrics.errorRate > 10) {
        createAlert('error', 'Critical Error Rate', `Error rate is ${systemMetrics.errorRate.toFixed(1)}%. System stability compromised.`, true)
      } else if (systemMetrics.errorRate > 5) {
        createAlert('warning', 'High Error Rate', `Error rate is ${systemMetrics.errorRate.toFixed(1)}%. Review service logs immediately.`)
      }

      // Services Offline Alert
      const offlineServices = services.filter(s => s.status === 'offline')
      if (offlineServices.length > 0) {
        createAlert('warning', 'Services Offline', `${offlineServices.length} service(s) are offline: ${offlineServices.map(s => s.name.replace('Service', '')).join(', ')}`)
      }
    }, 100)

    return () => clearTimeout(alertTimeout)
  }, [systemMetrics.cpuUsage, systemMetrics.totalMemory, systemMetrics.eventLatency, systemMetrics.errorRate, services.length])

  // Socket event listeners
  useEffect(() => {
    if (!socket) return

    const handleServiceStatus = (data: any) => {
      setServices(prev => 
        prev.map(service => {
          if (service.name === data.service || service.name === data.service_name) {
            return {
              ...service,
              status: data.status === 'RUNNING' ? 'online' : 'offline',
              uptime: data.uptime || service.uptime,
              memory: data.memory || service.memory,
              cpu: data.cpu || service.cpu,
              lastActivity: new Date().toLocaleTimeString(),
              errorCount: data.error_count || service.errorCount,
              successRate: data.success_rate || service.successRate
            }
          }
          return service
        })
      )
    }

    const handleSystemEvent = (data: any) => {
      // Create log entry with proper data extraction
      const message = data.message || data.data?.message || JSON.stringify(data)
      const service = data.service || data.topic || 'System'
      const level = data.level || 'INFO'
      
      // Only deduplicate specific repetitive startup messages
      const isStartupMessage = message.includes('is now online') && service !== 'System'
      
      if (isStartupMessage) {
        // Only log service online messages once per session
        const sessionKey = `session_${service}_online`
        if (sessionStorage.getItem(sessionKey)) {
          return // Skip repeat online message
        }
        sessionStorage.setItem(sessionKey, 'true')
      }
      
      // Add to logs with unique ID
      const logEntry: LogEntry = {
        id: Date.now() + Math.random().toString(),
        timestamp: new Date().toLocaleString(),
        level,
        service,
        message
      }
      
      console.log('Adding log entry:', logEntry) // Debug log
      
      setLogs(prev => {
        const newLogs = [...prev, logEntry]
        return newLogs.slice(-maxLogs) // Keep only last 1000 logs
      })
    }

    const handleSystemMetrics = (data: any) => {
      setSystemMetrics(prev => ({
        ...prev,
        ...data
      }))
    }

    const handleConfigStatus = (data: any) => {
      setConfigStatus(prev => ({
        ...prev,
        ...data
      }))
    }

    const handleSystemModeChange = (data: any) => {
      console.log('System mode change received in SystemTab:', data)
      // Update system metrics to reflect current mode
      setSystemMetrics(prev => ({
        ...prev,
        currentMode: data.current_mode || data.mode
      }))
    }

    const handleSystemStatus = (data: any) => {
      console.log('System status received:', data)
      
      // Update services if services data is provided
      if (data.services) {
        const updatedServices = services.map(service => {
          const backendService = data.services[service.name]
          if (backendService) {
            const newStatus = backendService.status || service.status
            const wasOffline = service.status === 'offline'
            const isNowOnline = newStatus === 'online'
            
            // Add log entry when service comes online (deduplicated)
            if (wasOffline && isNowOnline) {
              const sessionKey = `session_${service.name}_online`
              if (!sessionStorage.getItem(sessionKey)) {
                const logEntry: LogEntry = {
                  id: Date.now() + Math.random().toString(),
                  timestamp: new Date().toLocaleString(),
                  level: 'INFO',
                  service: service.name,
                  message: `${getServiceDisplayName(service.name)} service is now online`
                }
                
                sessionStorage.setItem(sessionKey, 'true')
                
                setLogs(prev => {
                  const newLogs = [...prev, logEntry]
                  return newLogs.slice(-maxLogs)
                })
              }
            }
            
            return {
              ...service,
              status: newStatus,
              uptime: backendService.uptime || service.uptime,
              memory: backendService.memory || service.memory,
              cpu: backendService.cpu || service.cpu,
              lastActivity: new Date().toLocaleTimeString(),
              errorCount: backendService.error_count || service.errorCount,
              successRate: backendService.success_rate || service.successRate
            }
          }
          return service
        })
        
        setServices(updatedServices)
        
        // Update system metrics with active service count
        const activeServices = updatedServices.filter(s => s.status === 'online').length
        setSystemMetrics(prev => ({
          ...prev,
          activeServices: activeServices
        }))
      }
    }

    // Subscribe to events
    socket.on('system_status', handleSystemStatus)
    socket.on('service_status_update', handleServiceStatus)
    socket.on('cantina_event', handleSystemEvent)
    socket.on('system_metrics', handleSystemMetrics)
    socket.on('config_status', handleConfigStatus)
    socket.on('system_error', handleSystemEvent)
    socket.on('system_mode_change', handleSystemModeChange)

    return () => {
      socket.off('system_status', handleSystemStatus)
      socket.off('service_status_update', handleServiceStatus)
      socket.off('cantina_event', handleSystemEvent)
      socket.off('system_metrics', handleSystemMetrics)
      socket.off('config_status', handleConfigStatus)
      socket.off('system_error', handleSystemEvent)
      socket.off('system_mode_change', handleSystemModeChange)
    }
  }, [socket])

  // Filter logs based on level, service, and search term
  useEffect(() => {
    let filtered = logs

    // Filter by log level
    const levelPriority = { DEBUG: 0, INFO: 1, WARNING: 2, ERROR: 3 }
    const minLevel = levelPriority[logLevel]
    filtered = filtered.filter(log => levelPriority[log.level] >= minLevel)

    // Filter by service
    if (selectedService !== 'all') {
      filtered = filtered.filter(log => log.service === selectedService)
    }

    // Filter by search term
    if (searchTerm) {
      const term = searchTerm.toLowerCase()
      filtered = filtered.filter(log => 
        log.message.toLowerCase().includes(term) ||
        log.service.toLowerCase().includes(term)
      )
    }

    setFilteredLogs(filtered)
  }, [logs, logLevel, selectedService, searchTerm])

  // Handle scroll detection to pause auto-scroll when user scrolls up
  useEffect(() => {
    const logContainer = logContainerRef.current
    if (!logContainer) return

    const handleScroll = () => {
      const { scrollTop, scrollHeight, clientHeight } = logContainer
      const isNearBottom = scrollTop + clientHeight >= scrollHeight - 50
      
      setUserScrolledUp(!isNearBottom)
      
    }

    logContainer.addEventListener('scroll', handleScroll)
    return () => logContainer.removeEventListener('scroll', handleScroll)
  }, [])

  // Auto-scroll to bottom when new logs arrive (only if user hasn't scrolled up)
  useEffect(() => {
    if (!userScrolledUp) {
      logsEndRef.current?.scrollIntoView({ behavior: 'smooth' })
    }
  }, [logs.length, userScrolledUp])

  const handleServiceRestart = (serviceName: string) => {
    if (!socket) return
    
    socket.emit('service_command', {
      action: 'restart',
      service: serviceName
    })
  }

  const handleSystemRestart = () => {
    if (!socket) return
    
    socket.emit('system_command', {
      action: 'restart'
    })
  }

  const handleLogExport = () => {
    const logText = filteredLogs.map(log => 
      `${log.timestamp} [${log.level}] ${log.service}: ${log.message}`
    ).join('\n')
    
    const blob = new Blob([logText], { type: 'text/plain' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `cantina-os-logs-${new Date().toISOString().split('T')[0]}.txt`
    a.click()
    URL.revokeObjectURL(url)
  }

  const handleClearLogs = () => {
    setLogs([])
    // Clear session storage for service online messages
    Object.keys(sessionStorage).forEach(key => {
      if (key.startsWith('session_') && key.endsWith('_online')) {
        sessionStorage.removeItem(key)
      }
    })
  }


  const activeAlerts = alerts.filter(alert => !alert.dismissed)
  const dismissedAlerts = alerts.filter(alert => alert.dismissed)

  return (
    <div className="space-y-6">
      {/* System Mode Control */}
      <SystemModeControl 
        currentMode={systemMode?.current_mode || 'IDLE'}
        onModeChange={(mode) => {
          console.log('Mode change requested:', mode)
          sendSystemCommand('set_mode', mode.toLowerCase())
        }}
        disabled={!socket}
      />

      {/* Alert Notification System */}
      {activeAlerts.length > 0 && (
        <div className="sw-panel">
          <div className="flex items-center justify-between mb-4">
            <div className="flex items-center space-x-2">
              <h3 className="text-lg font-semibold text-sw-blue-100 sw-text-glow">
                SYSTEM ALERTS
              </h3>
              <div className={`px-2 py-1 rounded-full text-xs font-bold ${
                activeAlerts.some(a => a.type === 'error') ? 'bg-sw-red text-white' :
                activeAlerts.some(a => a.type === 'warning') ? 'bg-sw-yellow text-black' :
                'bg-sw-blue-600 text-white'
              }`}>
                {activeAlerts.length}
              </div>
            </div>
            <div className="flex space-x-2">
              <button
                onClick={() => setShowDismissedAlerts(!showDismissedAlerts)}
                className="text-xs sw-button py-1 px-2"
              >
                {showDismissedAlerts ? 'Hide' : 'Show'} History ({dismissedAlerts.length})
              </button>
              <button
                onClick={clearAllAlerts}
                className="text-xs sw-button py-1 px-2 bg-sw-red/20 hover:bg-sw-red/30"
                disabled={activeAlerts.length === 0}
              >
                Dismiss All
              </button>
            </div>
          </div>

          <div className="space-y-2 max-h-60 overflow-y-auto">
            {/* Active Alerts */}
            {activeAlerts.map((alert) => (
              <div
                key={alert.id}
                className={`flex items-start justify-between p-3 rounded-lg border ${
                  alert.type === 'error' 
                    ? 'bg-sw-red/10 border-sw-red/30' 
                    : alert.type === 'warning'
                    ? 'bg-sw-yellow/10 border-sw-yellow/30'
                    : alert.type === 'success'
                    ? 'bg-sw-green/10 border-sw-green/30'
                    : 'bg-sw-blue-600/10 border-sw-blue-600/30'
                }`}
              >
                <div className="flex items-start space-x-3 flex-1">
                  <div className={`w-2 h-2 rounded-full mt-2 ${
                    alert.type === 'error' 
                      ? 'bg-sw-red animate-pulse' 
                      : alert.type === 'warning'
                      ? 'bg-sw-yellow animate-pulse'
                      : alert.type === 'success'
                      ? 'bg-sw-green'
                      : 'bg-sw-blue-400'
                  }`}></div>
                  <div className="flex-1">
                    <div className="flex items-center space-x-2 mb-1">
                      <span className={`text-sm font-semibold ${
                        alert.type === 'error' 
                          ? 'text-sw-red' 
                          : alert.type === 'warning'
                          ? 'text-sw-yellow'
                          : alert.type === 'success'
                          ? 'text-sw-green'
                          : 'text-sw-blue-200'
                      }`}>
                        {alert.title}
                      </span>
                      {alert.persistent && (
                        <span className="text-xs bg-sw-red/20 text-sw-red px-1 rounded">
                          PERSISTENT
                        </span>
                      )}
                    </div>
                    <div className="text-xs text-sw-blue-300 mb-1">{alert.message}</div>
                    <div className="text-xs text-sw-blue-400 font-mono">
                      {alert.timestamp.toLocaleTimeString()}
                    </div>
                  </div>
                </div>
                <div className="flex space-x-1 ml-2">
                  <button
                    onClick={() => dismissAlert(alert.id)}
                    className="text-xs px-2 py-1 rounded bg-sw-dark-700 hover:bg-sw-dark-600 text-sw-blue-300"
                  >
                    Dismiss
                  </button>
                  <button
                    onClick={() => deleteAlert(alert.id)}
                    className="text-xs px-2 py-1 rounded bg-sw-red/20 hover:bg-sw-red/30 text-sw-red"
                  >
                    ✕
                  </button>
                </div>
              </div>
            ))}

            {/* Dismissed Alerts History */}
            {showDismissedAlerts && dismissedAlerts.length > 0 && (
              <>
                <div className="border-t border-sw-blue-600/20 pt-2 mt-2">
                  <div className="text-xs text-sw-blue-400 mb-2 font-semibold">DISMISSED ALERTS</div>
                  {dismissedAlerts.slice(0, 10).map((alert) => (
                    <div
                      key={alert.id}
                      className="flex items-start justify-between p-2 rounded bg-sw-dark-700/20 border border-sw-blue-600/10 opacity-60 mb-1"
                    >
                      <div className="flex items-start space-x-2 flex-1">
                        <div className={`w-1 h-1 rounded-full mt-2 ${
                          alert.type === 'error' ? 'bg-sw-red' : 
                          alert.type === 'warning' ? 'bg-sw-yellow' :
                          alert.type === 'success' ? 'bg-sw-green' : 'bg-sw-blue-400'
                        }`}></div>
                        <div className="flex-1">
                          <div className="text-xs text-sw-blue-300 font-medium">{alert.title}</div>
                          <div className="text-xs text-sw-blue-400 font-mono">
                            {alert.timestamp.toLocaleTimeString()}
                          </div>
                        </div>
                      </div>
                      <button
                        onClick={() => deleteAlert(alert.id)}
                        className="text-xs px-1 py-0.5 rounded bg-sw-red/20 hover:bg-sw-red/30 text-sw-red opacity-50"
                      >
                        ✕
                      </button>
                    </div>
                  ))}
                </div>
              </>
            )}

            {activeAlerts.length === 0 && (
              <div className="text-center py-4 text-sw-green text-sm">
                ✅ No active alerts - System operating normally
              </div>
            )}
          </div>
        </div>
      )}
      {/* Service Health Grid */}
      <div className="sw-panel animate-pulse-subtle">
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-lg font-semibold text-sw-blue-100 sw-text-glow animate-glow">
            SERVICE HEALTH MONITORING
          </h3>
          <button 
            onClick={handleSystemRestart}
            className="sw-button bg-sw-red hover:bg-red-600"
          >
            Restart System
          </button>
        </div>

        <div className="overflow-x-auto">
          <table className="w-full">
            <thead>
              <tr className="border-b border-sw-blue-600/30">
                <th className="text-left py-2 text-sm text-sw-blue-300 font-medium">Service</th>
                <th className="text-left py-2 text-sm text-sw-blue-300 font-medium">Status</th>
                <th className="text-left py-2 text-sm text-sw-blue-300 font-medium">Uptime</th>
                <th className="text-left py-2 text-sm text-sw-blue-300 font-medium">Memory</th>
                <th className="text-left py-2 text-sm text-sw-blue-300 font-medium">CPU</th>
                <th className="text-left py-2 text-sm text-sw-blue-300 font-medium">Success Rate</th>
                <th className="text-left py-2 text-sm text-sw-blue-300 font-medium">Last Activity</th>
                <th className="text-left py-2 text-sm text-sw-blue-300 font-medium">Actions</th>
              </tr>
            </thead>
            <tbody>
              {services.map((service, index) => (
                <tr key={index} className="border-b border-sw-blue-600/10 hover:bg-sw-dark-700/20">
                  <td className="py-3 text-sm text-sw-blue-100">{getServiceDisplayName(service.name)}</td>
                  <td className="py-3">
                    <div className="flex items-center space-x-2">
                      <div className={`w-2 h-2 rounded-full ${
                        service.status === 'online' ? 'sw-status-online animate-pulse' : 'sw-status-offline'
                      }`}></div>
                      <span className="text-xs text-sw-blue-300 uppercase">{service.status}</span>
                      {service.errorCount > 0 && (
                        <span className="text-xs bg-sw-red/20 text-sw-red px-1 rounded">
                          {service.errorCount} errors
                        </span>
                      )}
                    </div>
                  </td>
                  <td className="py-3 text-sm text-sw-blue-300 font-mono">{service.uptime}</td>
                  <td className="py-3 text-sm text-sw-blue-300 font-mono">{service.memory}</td>
                  <td className="py-3 text-sm text-sw-blue-300 font-mono">{service.cpu}</td>
                  <td className="py-3">
                    <div className="flex items-center space-x-1">
                      <div className={`text-sm font-mono ${
                        service.successRate >= 95 ? 'text-sw-green' :
                        service.successRate >= 80 ? 'text-sw-yellow' : 'text-sw-red'
                      }`}>
                        {service.successRate > 0 ? `${service.successRate.toFixed(1)}%` : '--'}
                      </div>
                    </div>
                  </td>
                  <td className="py-3 text-xs text-sw-blue-300 font-mono">{service.lastActivity}</td>
                  <td className="py-3">
                    <div className="flex space-x-1">
                      <button 
                        onClick={() => handleServiceRestart(service.name)}
                        className="text-xs sw-button py-1 px-2"
                        disabled={service.status === 'offline'}
                      >
                        Restart
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* Individual Service Performance Metrics */}
      <div className="sw-panel">
        <h3 className="text-lg font-semibold text-sw-blue-100 mb-4 sw-text-glow">
          INDIVIDUAL SERVICE METRICS
        </h3>
        
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {services.filter(service => service.status === 'online').map((service, index) => (
            <div key={index} className="bg-sw-dark-700/30 rounded-lg border border-sw-blue-600/20 p-4">
              <div className="flex items-center justify-between mb-3">
                <h4 className="text-sm font-semibold text-sw-blue-200 truncate">
                  {getServiceDisplayName(service.name)}
                </h4>
                <div className={`w-2 h-2 rounded-full ${
                  service.status === 'online' ? 'sw-status-online animate-pulse' : 'sw-status-offline'
                }`}></div>
              </div>
              
              <div className="space-y-2 text-xs">
                <div className="flex justify-between">
                  <span className="text-sw-blue-300">Uptime:</span>
                  <span className="text-sw-blue-100 font-mono">{service.uptime}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-sw-blue-300">Memory:</span>
                  <span className="text-sw-blue-100 font-mono">{service.memory}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-sw-blue-300">CPU Usage:</span>
                  <span className="text-sw-blue-100 font-mono">{service.cpu}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-sw-blue-300">Success Rate:</span>
                  <span className={`font-mono ${
                    service.successRate >= 95 ? 'text-sw-green' :
                    service.successRate >= 80 ? 'text-sw-yellow' : 'text-sw-red'
                  }`}>
                    {service.successRate > 0 ? `${service.successRate.toFixed(1)}%` : '--'}
                  </span>
                </div>
                <div className="flex justify-between">
                  <span className="text-sw-blue-300">Error Count:</span>
                  <span className={`font-mono ${
                    service.errorCount === 0 ? 'text-sw-green' : 'text-sw-red'
                  }`}>
                    {service.errorCount}
                  </span>
                </div>
                <div className="flex justify-between">
                  <span className="text-sw-blue-300">Last Activity:</span>
                  <span className="text-sw-blue-100 font-mono text-xs">{service.lastActivity}</span>
                </div>
              </div>
              
              {/* Service-specific metrics */}
              <div className="mt-3 pt-2 border-t border-sw-blue-600/20">
                <div className="text-xs text-sw-blue-400 mb-1">Service Metrics:</div>
                {service.name === 'deepgram_direct_mic' && (
                  <div className="space-y-1 text-xs">
                    <div className="flex justify-between">
                      <span className="text-sw-blue-300">Transcriptions/min:</span>
                      <span className="text-sw-blue-100 font-mono">--</span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-sw-blue-300">Avg Confidence:</span>
                      <span className="text-sw-blue-100 font-mono">--</span>
                    </div>
                  </div>
                )}
                {service.name === 'gpt_service' && (
                  <div className="space-y-1 text-xs">
                    <div className="flex justify-between">
                      <span className="text-sw-blue-300">Requests/min:</span>
                      <span className="text-sw-blue-100 font-mono">--</span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-sw-blue-300">Avg Response Time:</span>
                      <span className="text-sw-blue-100 font-mono">--</span>
                    </div>
                  </div>
                )}
                {service.name === 'elevenlabs_service' && (
                  <div className="space-y-1 text-xs">
                    <div className="flex justify-between">
                      <span className="text-sw-blue-300">Audio Generated:</span>
                      <span className="text-sw-blue-100 font-mono">--</span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-sw-blue-300">Avg Generation Time:</span>
                      <span className="text-sw-blue-100 font-mono">--</span>
                    </div>
                  </div>
                )}
                {service.name === 'MusicController' && (
                  <div className="space-y-1 text-xs">
                    <div className="flex justify-between">
                      <span className="text-sw-blue-300">Tracks Played:</span>
                      <span className="text-sw-blue-100 font-mono">--</span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-sw-blue-300">Current Volume:</span>
                      <span className="text-sw-blue-100 font-mono">--</span>
                    </div>
                  </div>
                )}
                {service.name === 'eye_light_controller' && (
                  <div className="space-y-1 text-xs">
                    <div className="flex justify-between">
                      <span className="text-sw-blue-300">Commands Sent:</span>
                      <span className="text-sw-blue-100 font-mono">--</span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-sw-blue-300">Arduino Status:</span>
                      <span className="text-sw-blue-100 font-mono">--</span>
                    </div>
                  </div>
                )}
                {service.name === 'brain_service' && (
                  <div className="space-y-1 text-xs">
                    <div className="flex justify-between">
                      <span className="text-sw-blue-300">Events Processed:</span>
                      <span className="text-sw-blue-100 font-mono">--</span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-sw-blue-300">Avg Processing Time:</span>
                      <span className="text-sw-blue-100 font-mono">--</span>
                    </div>
                  </div>
                )}
              </div>
              
              {/* Mini performance chart placeholder */}
              <div className="mt-3 pt-2 border-t border-sw-blue-600/20">
                <div className="text-xs text-sw-blue-400 mb-2">Performance Trend:</div>
                <div className="h-8 bg-sw-dark-800/50 rounded flex items-end space-x-1 px-1">
                  {Array.from({ length: 12 }).map((_, i) => (
                    <div
                      key={i}
                      className={`w-1 bg-gradient-to-t from-sw-blue-600 to-sw-blue-400 rounded-t opacity-${
                        Math.floor(Math.random() * 80) + 20
                      }`}
                      style={{ height: `${Math.floor(Math.random() * 80) + 20}%` }}
                    ></div>
                  ))}
                </div>
              </div>
            </div>
          ))}
        </div>
        
        {services.filter(service => service.status === 'online').length === 0 && (
          <div className="text-center py-8 text-sw-blue-300/50 text-sm italic">
            No services are currently online. Start CantinaOS to see individual service metrics.
          </div>
        )}
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Real-time Event Log */}
        <div className="lg:col-span-2 sw-panel">
          <div className="flex items-center justify-between mb-4">
            <h3 className="text-lg font-semibold text-sw-blue-100 sw-text-glow">
              REAL-TIME EVENT LOG
            </h3>
            <div className="flex items-center space-x-2">
              <input
                type="text"
                placeholder="Search logs..."
                value={searchTerm}
                onChange={(e) => setSearchTerm(e.target.value)}
                className="px-2 py-1 bg-sw-dark-700 border border-sw-blue-600/30 rounded text-sw-blue-100 text-sm w-32"
              />
              <select
                value={selectedService}
                onChange={(e) => setSelectedService(e.target.value)}
                className="px-2 py-1 bg-sw-dark-700 border border-sw-blue-600/30 rounded text-sw-blue-100 text-sm"
              >
                <option value="all">All Services</option>
                {services.map((service) => (
                  <option key={service.name} value={service.name}>{getServiceDisplayName(service.name)}</option>
                ))}
              </select>
              <select
                value={logLevel}
                onChange={(e) => setLogLevel(e.target.value as any)}
                className="px-2 py-1 bg-sw-dark-700 border border-sw-blue-600/30 rounded text-sw-blue-100 text-sm"
              >
                <option value="DEBUG">DEBUG</option>
                <option value="INFO">INFO</option>
                <option value="WARNING">WARNING</option>
                <option value="ERROR">ERROR</option>
              </select>
            </div>
          </div>
          
          <div className="flex items-center justify-between mb-2">
            <div className="text-xs text-sw-blue-300">
              Showing {filteredLogs.length} of {logs.length} logs
              {userScrolledUp && (
                <span className="ml-2 text-sw-yellow">(Scroll paused)</span>
              )}
            </div>
            <div className="flex space-x-2">
              <button
                onClick={handleLogExport}
                className="text-xs sw-button py-1 px-2"
                disabled={filteredLogs.length === 0}
              >
                Export Logs
              </button>
              <button
                onClick={handleClearLogs}
                className="text-xs sw-button py-1 px-2 bg-sw-red/20 hover:bg-sw-red/30"
                disabled={logs.length === 0}
              >
                Clear
              </button>
            </div>
          </div>

          <div 
            ref={logContainerRef}
            className="bg-sw-dark-700/50 rounded-lg border border-sw-blue-600/20 p-4 h-80 overflow-y-auto font-mono text-sm"
          >
            {filteredLogs.length === 0 ? (
              <div className="text-sw-blue-300/50 text-xs italic text-center py-8">
                {logs.length === 0 
                  ? 'Real-time logs will appear here when system is connected...'
                  : 'No logs match current filters'
                }
              </div>
            ) : (
              filteredLogs.map((log) => (
                <div key={log.id} className="mb-1 flex text-xs leading-relaxed hover:bg-sw-blue-600/10 px-1 rounded">
                  <span className="text-sw-blue-400 mr-2 flex-shrink-0 w-20">
                    {log.timestamp.split(' ')[1]}
                  </span>
                  <span className={`mr-2 font-semibold flex-shrink-0 w-16 ${
                    log.level === 'ERROR' ? 'text-sw-red' :
                    log.level === 'WARNING' ? 'text-sw-yellow' :
                    log.level === 'INFO' ? 'text-sw-green' :
                    'text-sw-blue-300'
                  }`}>
                    [{log.level}]
                  </span>
                  <span className="text-sw-blue-300 mr-2 flex-shrink-0 w-24 truncate">
                    {log.service}:
                  </span>
                  <span className="text-sw-blue-100 flex-1 break-words">
                    {log.message}
                  </span>
                </div>
              ))
            )}
            <div ref={logsEndRef} />
          </div>
        </div>

        {/* System Information */}
        <div className="space-y-6">
          <div className="sw-panel">
            <h3 className="text-lg font-semibold text-sw-blue-100 mb-4 sw-text-glow">
              SYSTEM INFO
            </h3>
            <div className="space-y-3 text-sm">
              <div className="flex justify-between">
                <span className="text-sw-blue-300">CantinaOS Version:</span>
                <span className="text-sw-blue-100 font-mono">v2.1.0</span>
              </div>
              <div className="flex justify-between">
                <span className="text-sw-blue-300">Event Bus:</span>
                <span className={`font-mono ${socket ? 'text-sw-green' : 'text-sw-red'}`}>
                  {socket ? 'Connected' : 'Offline'}
                </span>
              </div>
              <div className="flex justify-between">
                <span className="text-sw-blue-300">Active Services:</span>
                <span className="text-sw-blue-100 font-mono">
                  {systemMetrics.activeServices}/{systemMetrics.totalServices}
                </span>
              </div>
              <div className="flex justify-between">
                <span className="text-sw-blue-300">Uptime:</span>
                <span className="text-sw-blue-100 font-mono">{systemMetrics.uptime}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-sw-blue-300">Events/min:</span>
                <span className="text-sw-blue-100 font-mono">{systemMetrics.eventsPerMinute}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-sw-blue-300">Avg Latency:</span>
                <span className={`font-mono ${
                  systemMetrics.eventLatency < 100 ? 'text-sw-green' :
                  systemMetrics.eventLatency < 500 ? 'text-sw-yellow' : 'text-sw-red'
                }`}>
                  {systemMetrics.eventLatency > 0 ? `${systemMetrics.eventLatency}ms` : '--'}
                </span>
              </div>
            </div>
          </div>

          <div className="sw-panel">
            <h3 className="text-lg font-semibold text-sw-blue-100 mb-4 sw-text-glow">
              CONFIGURATION
            </h3>
            <div className="space-y-3 text-sm">
              <div className="flex justify-between items-center">
                <span className="text-sw-blue-300">OpenAI API:</span>
                <div className="flex items-center space-x-2">
                  <div className={`w-2 h-2 rounded-full ${
                    configStatus.openai ? 'sw-status-online' : 'sw-status-offline'
                  }`}></div>
                  <span className={`text-xs ${
                    configStatus.openai ? 'text-sw-green' : 'text-sw-red'
                  }`}>
                    {configStatus.openai ? 'Configured' : 'Not Configured'}
                  </span>
                </div>
              </div>
              <div className="flex justify-between items-center">
                <span className="text-sw-blue-300">ElevenLabs API:</span>
                <div className="flex items-center space-x-2">
                  <div className={`w-2 h-2 rounded-full ${
                    configStatus.elevenlabs ? 'sw-status-online' : 'sw-status-offline'
                  }`}></div>
                  <span className={`text-xs ${
                    configStatus.elevenlabs ? 'text-sw-green' : 'text-sw-red'
                  }`}>
                    {configStatus.elevenlabs ? 'Configured' : 'Not Configured'}
                  </span>
                </div>
              </div>
              <div className="flex justify-between items-center">
                <span className="text-sw-blue-300">Deepgram API:</span>
                <div className="flex items-center space-x-2">
                  <div className={`w-2 h-2 rounded-full ${
                    configStatus.deepgram ? 'sw-status-online' : 'sw-status-offline'
                  }`}></div>
                  <span className={`text-xs ${
                    configStatus.deepgram ? 'text-sw-green' : 'text-sw-red'
                  }`}>
                    {configStatus.deepgram ? 'Configured' : 'Not Configured'}
                  </span>
                </div>
              </div>
              <div className="flex justify-between items-center">
                <span className="text-sw-blue-300">Arduino Port:</span>
                <div className="flex items-center space-x-2">
                  <div className={`w-2 h-2 rounded-full ${
                    configStatus.arduino ? 'sw-status-online' : 'sw-status-offline'
                  }`}></div>
                  <span className={`text-xs ${
                    configStatus.arduino ? 'text-sw-green' : 'text-sw-red'
                  }`}>
                    {configStatus.arduino ? 'Connected' : 'Not Connected'}
                  </span>
                </div>
              </div>
            </div>
            <button 
              className="sw-button w-full mt-4 text-sm"
              onClick={() => {
                if (socket) {
                  socket.emit('system_command', { action: 'refresh_config' })
                }
              }}
            >
              Refresh Config
            </button>
          </div>
        </div>
      </div>

      {/* Performance Profiling */}
      <div className="sw-panel">
        <h3 className="text-lg font-semibold text-sw-blue-100 mb-4 sw-text-glow">
          PERFORMANCE PROFILING & ANALYTICS
        </h3>
        
        {/* Performance Timeline */}
        <div className="mb-6">
          <h4 className="text-sm font-semibold text-sw-blue-200 mb-3">Performance Timeline (Last 60 seconds)</h4>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            
            {/* CPU Usage Timeline */}
            <div className="bg-sw-dark-700/30 rounded-lg border border-sw-blue-600/20 p-4">
              <div className="flex items-center justify-between mb-2">
                <span className="text-xs text-sw-blue-300">CPU Usage Over Time</span>
                <span className={`text-xs font-mono ${
                  systemMetrics.cpuUsage > 80 ? 'text-sw-red' :
                  systemMetrics.cpuUsage > 50 ? 'text-sw-yellow' : 'text-sw-green'
                }`}>
                  {systemMetrics.cpuUsage > 0 ? `${systemMetrics.cpuUsage.toFixed(1)}%` : '--'}
                </span>
              </div>
              <div className="h-16 bg-sw-dark-800/50 rounded flex items-end space-x-1 px-1">
                {Array.from({ length: 30 }).map((_, i) => {
                  const height = Math.max(5, Math.floor(Math.random() * 80) + (systemMetrics.cpuUsage / 2));
                  return (
                    <div
                      key={i}
                      className="w-1 bg-gradient-to-t from-sw-green to-sw-yellow rounded-t opacity-70"
                      style={{ height: `${height}%` }}
                    ></div>
                  );
                })}
              </div>
            </div>

            {/* Memory Usage Timeline */}
            <div className="bg-sw-dark-700/30 rounded-lg border border-sw-blue-600/20 p-4">
              <div className="flex items-center justify-between mb-2">
                <span className="text-xs text-sw-blue-300">Memory Usage Over Time</span>
                <span className={`text-xs font-mono ${
                  systemMetrics.totalMemory > 1000 ? 'text-sw-red' :
                  systemMetrics.totalMemory > 500 ? 'text-sw-yellow' : 'text-sw-green'
                }`}>
                  {systemMetrics.totalMemory > 0 ? `${systemMetrics.totalMemory} MB` : '-- MB'}
                </span>
              </div>
              <div className="h-16 bg-sw-dark-800/50 rounded flex items-end space-x-1 px-1">
                {Array.from({ length: 30 }).map((_, i) => {
                  const height = Math.max(10, Math.floor(Math.random() * 70) + 20);
                  return (
                    <div
                      key={i}
                      className="w-1 bg-gradient-to-t from-sw-blue-600 to-sw-blue-400 rounded-t opacity-70"
                      style={{ height: `${height}%` }}
                    ></div>
                  );
                })}
              </div>
            </div>

            {/* Event Throughput Timeline */}
            <div className="bg-sw-dark-700/30 rounded-lg border border-sw-blue-600/20 p-4">
              <div className="flex items-center justify-between mb-2">
                <span className="text-xs text-sw-blue-300">Event Throughput</span>
                <span className="text-xs font-mono text-sw-blue-100">
                  {systemMetrics.eventsPerMinute}/min
                </span>
              </div>
              <div className="h-16 bg-sw-dark-800/50 rounded flex items-end space-x-1 px-1">
                {Array.from({ length: 30 }).map((_, i) => {
                  const height = Math.max(5, Math.floor(Math.random() * 85) + 10);
                  return (
                    <div
                      key={i}
                      className="w-1 bg-gradient-to-t from-sw-yellow to-sw-red rounded-t opacity-70"
                      style={{ height: `${height}%` }}
                    ></div>
                  );
                })}
              </div>
            </div>

            {/* Response Time Timeline */}
            <div className="bg-sw-dark-700/30 rounded-lg border border-sw-blue-600/20 p-4">
              <div className="flex items-center justify-between mb-2">
                <span className="text-xs text-sw-blue-300">Avg Response Time</span>
                <span className={`text-xs font-mono ${
                  systemMetrics.eventLatency > 500 ? 'text-sw-red' :
                  systemMetrics.eventLatency > 100 ? 'text-sw-yellow' : 'text-sw-green'
                }`}>
                  {systemMetrics.eventLatency > 0 ? `${systemMetrics.eventLatency}ms` : '--'}
                </span>
              </div>
              <div className="h-16 bg-sw-dark-800/50 rounded flex items-end space-x-1 px-1">
                {Array.from({ length: 30 }).map((_, i) => {
                  const height = Math.max(10, Math.floor(Math.random() * 60) + 15);
                  return (
                    <div
                      key={i}
                      className="w-1 bg-gradient-to-t from-sw-red to-sw-yellow rounded-t opacity-70"
                      style={{ height: `${height}%` }}
                    ></div>
                  );
                })}
              </div>
            </div>
          </div>
        </div>

        {/* Performance Insights & Bottlenecks */}
        <div className="mb-6">
          <h4 className="text-sm font-semibold text-sw-blue-200 mb-3">Performance Insights</h4>
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            
            {/* System Health Score */}
            <div className="bg-sw-dark-700/30 rounded-lg border border-sw-blue-600/20 p-4">
              <div className="text-center">
                <div className={`text-3xl font-bold mb-2 ${
                  systemMetrics.errorRate < 1 && systemMetrics.cpuUsage < 50 ? 'text-sw-green' :
                  systemMetrics.errorRate < 5 && systemMetrics.cpuUsage < 80 ? 'text-sw-yellow' : 'text-sw-red'
                }`}>
                  {systemMetrics.errorRate < 1 && systemMetrics.cpuUsage < 50 ? '95' :
                   systemMetrics.errorRate < 5 && systemMetrics.cpuUsage < 80 ? '78' : '42'}
                </div>
                <div className="text-xs text-sw-blue-300 uppercase tracking-wide">Health Score</div>
                <div className="text-xs text-sw-blue-400 mt-1">Overall System</div>
              </div>
            </div>

            {/* Performance Rating */}
            <div className="bg-sw-dark-700/30 rounded-lg border border-sw-blue-600/20 p-4">
              <div className="text-center">
                <div className={`text-3xl font-bold mb-2 ${
                  systemMetrics.eventLatency < 100 ? 'text-sw-green' :
                  systemMetrics.eventLatency < 300 ? 'text-sw-yellow' : 'text-sw-red'
                }`}>
                  {systemMetrics.eventLatency < 100 ? 'A+' :
                   systemMetrics.eventLatency < 300 ? 'B' : 'C'}
                </div>
                <div className="text-xs text-sw-blue-300 uppercase tracking-wide">Performance</div>
                <div className="text-xs text-sw-blue-400 mt-1">Response Grade</div>
              </div>
            </div>

            {/* Stability Index */}
            <div className="bg-sw-dark-700/30 rounded-lg border border-sw-blue-600/20 p-4">
              <div className="text-center">
                <div className={`text-3xl font-bold mb-2 ${
                  services.filter(s => s.errorCount === 0).length === services.length ? 'text-sw-green' :
                  services.filter(s => s.errorCount === 0).length > services.length * 0.8 ? 'text-sw-yellow' : 'text-sw-red'
                }`}>
                  {Math.floor((services.filter(s => s.errorCount === 0).length / Math.max(services.length, 1)) * 100)}%
                </div>
                <div className="text-xs text-sw-blue-300 uppercase tracking-wide">Stability</div>
                <div className="text-xs text-sw-blue-400 mt-1">Error-Free Services</div>
              </div>
            </div>
          </div>
        </div>

        {/* Bottleneck Detection */}
        <div className="mb-6">
          <h4 className="text-sm font-semibold text-sw-blue-200 mb-3">Bottleneck Detection</h4>
          <div className="space-y-2">
            {systemMetrics.cpuUsage > 80 && (
              <div className="flex items-center space-x-2 p-3 bg-sw-red/10 border border-sw-red/30 rounded-lg">
                <div className="w-2 h-2 rounded-full bg-sw-red animate-pulse"></div>
                <div>
                  <span className="text-sm text-sw-red font-semibold">High CPU Usage Detected</span>
                  <div className="text-xs text-sw-blue-300">CPU usage is at {systemMetrics.cpuUsage.toFixed(1)}%. Consider optimizing resource-intensive services.</div>
                </div>
              </div>
            )}
            {systemMetrics.totalMemory > 1000 && (
              <div className="flex items-center space-x-2 p-3 bg-sw-yellow/10 border border-sw-yellow/30 rounded-lg">
                <div className="w-2 h-2 rounded-full bg-sw-yellow animate-pulse"></div>
                <div>
                  <span className="text-sm text-sw-yellow font-semibold">High Memory Usage</span>
                  <div className="text-xs text-sw-blue-300">Memory usage is at {systemMetrics.totalMemory} MB. Monitor for memory leaks.</div>
                </div>
              </div>
            )}
            {systemMetrics.eventLatency > 500 && (
              <div className="flex items-center space-x-2 p-3 bg-sw-red/10 border border-sw-red/30 rounded-lg">
                <div className="w-2 h-2 rounded-full bg-sw-red animate-pulse"></div>
                <div>
                  <span className="text-sm text-sw-red font-semibold">High Response Latency</span>
                  <div className="text-xs text-sw-blue-300">Average response time is {systemMetrics.eventLatency}ms. Check service performance.</div>
                </div>
              </div>
            )}
            {systemMetrics.errorRate > 5 && (
              <div className="flex items-center space-x-2 p-3 bg-sw-red/10 border border-sw-red/30 rounded-lg">
                <div className="w-2 h-2 rounded-full bg-sw-red animate-pulse"></div>
                <div>
                  <span className="text-sm text-sw-red font-semibold">High Error Rate</span>
                  <div className="text-xs text-sw-blue-300">Error rate is {systemMetrics.errorRate.toFixed(1)}%. Review service logs for issues.</div>
                </div>
              </div>
            )}
            {services.filter(s => s.status === 'offline').length > 0 && (
              <div className="flex items-center space-x-2 p-3 bg-sw-yellow/10 border border-sw-yellow/30 rounded-lg">
                <div className="w-2 h-2 rounded-full bg-sw-yellow animate-pulse"></div>
                <div>
                  <span className="text-sm text-sw-yellow font-semibold">Services Offline</span>
                  <div className="text-xs text-sw-blue-300">{services.filter(s => s.status === 'offline').length} service(s) are currently offline.</div>
                </div>
              </div>
            )}
            {systemMetrics.cpuUsage < 50 && systemMetrics.errorRate < 1 && systemMetrics.eventLatency < 100 && (
              <div className="flex items-center space-x-2 p-3 bg-sw-green/10 border border-sw-green/30 rounded-lg">
                <div className="w-2 h-2 rounded-full bg-sw-green animate-pulse"></div>
                <div>
                  <span className="text-sm text-sw-green font-semibold">System Running Optimally</span>
                  <div className="text-xs text-sw-blue-300">All performance metrics are within optimal ranges.</div>
                </div>
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Performance Metrics */}
      <div className="sw-panel">
        <h3 className="text-lg font-semibold text-sw-blue-100 mb-4 sw-text-glow">
          PERFORMANCE METRICS
        </h3>
        
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          <MetricCard 
            label="Total Memory" 
            value={systemMetrics.totalMemory > 0 ? `${systemMetrics.totalMemory} MB` : "-- MB"}
            trend={
              systemMetrics.totalMemory > 1000 ? 'down' :
              systemMetrics.totalMemory > 500 ? 'neutral' : 'up'
            }
            subtitle={systemMetrics.totalMemory > 0 ? 'System RAM' : undefined}
          />
          <MetricCard 
            label="CPU Usage" 
            value={systemMetrics.cpuUsage > 0 ? `${systemMetrics.cpuUsage.toFixed(1)}%` : "--%"}
            trend={
              systemMetrics.cpuUsage > 80 ? 'down' :
              systemMetrics.cpuUsage > 50 ? 'neutral' : 'up'
            }
            subtitle={systemMetrics.cpuUsage > 0 ? 'All Cores' : undefined}
          />
          <MetricCard 
            label="Event Latency" 
            value={systemMetrics.eventLatency > 0 ? `${systemMetrics.eventLatency} ms` : "-- ms"}
            trend={
              systemMetrics.eventLatency > 500 ? 'down' :
              systemMetrics.eventLatency > 100 ? 'neutral' : 'up'
            }
            subtitle={systemMetrics.eventLatency > 0 ? 'Avg Response' : undefined}
          />
          <MetricCard 
            label="Error Rate" 
            value={systemMetrics.errorRate >= 0 ? `${systemMetrics.errorRate.toFixed(1)}%` : "--%"}
            trend={
              systemMetrics.errorRate > 10 ? 'down' :
              systemMetrics.errorRate > 2 ? 'neutral' : 'up'
            }
            subtitle={systemMetrics.errorRate >= 0 ? 'Last Hour' : undefined}
          />
        </div>
        
        {/* Additional Performance Insights */}
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mt-6">
          <div className="bg-sw-dark-700/30 rounded-lg border border-sw-blue-600/20 p-4">
            <h4 className="text-sm font-semibold text-sw-blue-200 mb-3">Service Health Summary</h4>
            <div className="space-y-2">
              <div className="flex justify-between text-sm">
                <span className="text-sw-blue-300">Online Services:</span>
                <span className="text-sw-green font-mono">
                  {services.filter(s => s.status === 'online').length}
                </span>
              </div>
              <div className="flex justify-between text-sm">
                <span className="text-sw-blue-300">Services with Errors:</span>
                <span className="text-sw-red font-mono">
                  {services.filter(s => s.errorCount > 0).length}
                </span>
              </div>
              <div className="flex justify-between text-sm">
                <span className="text-sw-blue-300">Avg Success Rate:</span>
                <span className="text-sw-blue-100 font-mono">
                  {services.length > 0 
                    ? (services.reduce((acc, s) => acc + s.successRate, 0) / services.length).toFixed(1)
                    : '0'
                  }%
                </span>
              </div>
            </div>
          </div>
          
          <div className="bg-sw-dark-700/30 rounded-lg border border-sw-blue-600/20 p-4">
            <h4 className="text-sm font-semibold text-sw-blue-200 mb-3">Event Processing</h4>
            <div className="space-y-2">
              <div className="flex justify-between text-sm">
                <span className="text-sw-blue-300">Events/Minute:</span>
                <span className="text-sw-blue-100 font-mono">{systemMetrics.eventsPerMinute}</span>
              </div>
              <div className="flex justify-between text-sm">
                <span className="text-sw-blue-300">Log Entries:</span>
                <span className="text-sw-blue-100 font-mono">{logs.length}</span>
              </div>
              <div className="flex justify-between text-sm">
                <span className="text-sw-blue-300">Filtered Logs:</span>
                <span className="text-sw-blue-100 font-mono">{filteredLogs.length}</span>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}

interface MetricCardProps {
  label: string
  value: string
  trend: 'up' | 'down' | 'neutral'
  subtitle?: string
}

function MetricCard({ label, value, trend, subtitle }: MetricCardProps) {
  const getTrendColor = () => {
    switch (trend) {
      case 'up':
        return 'text-sw-green'
      case 'down':
        return 'text-sw-red'
      case 'neutral':
      default:
        return 'text-sw-blue-300'
    }
  }

  return (
    <div className="text-center p-4 bg-sw-dark-700/30 rounded-lg border border-sw-blue-600/20">
      <div className={`text-2xl font-bold mb-1 ${getTrendColor()}`}>{value}</div>
      <div className="text-xs text-sw-blue-300 uppercase tracking-wide">{label}</div>
      {subtitle && (
        <div className="text-xs text-sw-blue-400 mt-1">{subtitle}</div>
      )}
    </div>
  )
}