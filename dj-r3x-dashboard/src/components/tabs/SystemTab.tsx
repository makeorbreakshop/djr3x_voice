'use client'

import { useState, useEffect, useRef } from 'react'
import { useSocketContext } from '../../contexts/SocketContext'
import { LogEntry } from '@/hooks/useSocket'
import SystemModeControl from '../SystemModeControl'
import { SystemActionEnum, SystemModeEnum } from '../../types/schemas'

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

// LogEntry interface moved to useSocket hook

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

interface AlertNotification {
  id: string
  type: 'error' | 'warning' | 'info' | 'success'
  title: string
  message: string
  timestamp: Date
  dismissed: boolean
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
    'deepgram_direct_mic': 'Voice Input',
    'gpt_service': 'AI Assistant',
    'intent_router_service': 'Intent Router',
    'brain_service': 'Brain Service',
    'timeline_executor_service': 'Timeline Executor',
    'elevenlabs_service': 'Speech Synthesis',
    'cached_speech_service': 'Cached Speech',
    'mode_change_sound': 'Mode Sound',
    'MusicController': 'Music Controller',
    'eye_light_controller': 'Eye Lights',
    'cli': 'CLI Service',
    'command_dispatcher': 'Command Dispatcher',
  }
  return nameMap[serviceName] || serviceName
}

// Helper function to calculate system health score
const calculateHealthScore = (services: ServiceData[], metrics: SystemMetrics): number => {
  const onlineServices = services.filter(s => s.status === 'online').length
  const serviceHealth = services.length > 0 ? (onlineServices / services.length) * 100 : 0
  
  const cpuHealth = metrics.cpuUsage > 0 ? Math.max(0, 100 - metrics.cpuUsage) : 100
  const memoryHealth = metrics.totalMemory > 0 ? Math.max(0, 100 - (metrics.totalMemory / 20)) : 100
  const latencyHealth = metrics.eventLatency > 0 ? Math.max(0, 100 - (metrics.eventLatency / 10)) : 100
  const errorHealth = Math.max(0, 100 - (metrics.errorRate * 10))
  
  return Math.round((serviceHealth + cpuHealth + memoryHealth + latencyHealth + errorHealth) / 5)
}

export default function SystemTab() {
  const { socket, systemMode, sendSystemCommand, logs } = useSocketContext()
  const [services, setServices] = useState<ServiceData[]>([
    { name: 'web_bridge', status: 'offline', uptime: '--', memory: '--', cpu: '--', lastActivity: '--', errorCount: 0, successRate: 0 },
    { name: 'debug', status: 'offline', uptime: '--', memory: '--', cpu: '--', lastActivity: '--', errorCount: 0, successRate: 0 },
    { name: 'yoda_mode_manager', status: 'offline', uptime: '--', memory: '--', cpu: '--', lastActivity: '--', errorCount: 0, successRate: 0 },
    { name: 'deepgram_direct_mic', status: 'offline', uptime: '--', memory: '--', cpu: '--', lastActivity: '--', errorCount: 0, successRate: 0 },
    { name: 'gpt_service', status: 'offline', uptime: '--', memory: '--', cpu: '--', lastActivity: '--', errorCount: 0, successRate: 0 },
    { name: 'brain_service', status: 'offline', uptime: '--', memory: '--', cpu: '--', lastActivity: '--', errorCount: 0, successRate: 0 },
    { name: 'elevenlabs_service', status: 'offline', uptime: '--', memory: '--', cpu: '--', lastActivity: '--', errorCount: 0, successRate: 0 },
    { name: 'MusicController', status: 'offline', uptime: '--', memory: '--', cpu: '--', lastActivity: '--', errorCount: 0, successRate: 0 },
    { name: 'eye_light_controller', status: 'offline', uptime: '--', memory: '--', cpu: '--', lastActivity: '--', errorCount: 0, successRate: 0 },
    { name: 'memory_service', status: 'offline', uptime: '--', memory: '--', cpu: '--', lastActivity: '--', errorCount: 0, successRate: 0 },
    { name: 'timeline_executor_service', status: 'offline', uptime: '--', memory: '--', cpu: '--', lastActivity: '--', errorCount: 0, successRate: 0 },
    { name: 'cli', status: 'offline', uptime: '--', memory: '--', cpu: '--', lastActivity: '--', errorCount: 0, successRate: 0 },
  ])
  
  const [filteredLogs, setFilteredLogs] = useState<LogEntry[]>([])
  const [systemMetrics, setSystemMetrics] = useState<SystemMetrics>({
    totalMemory: 0,
    cpuUsage: 0,
    eventLatency: 0,
    errorRate: 0,
    eventsPerMinute: 0,
    uptime: '--:--:--',
    activeServices: 0,
    totalServices: 12
  })
  const [alerts, setAlerts] = useState<AlertNotification[]>([])
  const [showFullLog, setShowFullLog] = useState(false)
  const [logLevel, setLogLevel] = useState<'DEBUG' | 'INFO' | 'WARNING' | 'ERROR'>('INFO')
  const logsEndRef = useRef<HTMLDivElement>(null)

  // Calculate derived values
  const healthScore = calculateHealthScore(services, systemMetrics)
  const onlineServices = services.filter(s => s.status === 'online').length
  const criticalAlerts = alerts.filter(a => !a.dismissed && a.type === 'error').length
  const warningAlerts = alerts.filter(a => !a.dismissed && a.type === 'warning').length

  // Get health status color and text
  const getHealthStatus = () => {
    if (healthScore >= 85) return { color: 'text-sw-green', bg: 'bg-sw-green/10', status: 'EXCELLENT' }
    if (healthScore >= 70) return { color: 'text-sw-yellow', bg: 'bg-sw-yellow/10', status: 'GOOD' }
    if (healthScore >= 50) return { color: 'text-sw-orange', bg: 'bg-orange-500/10', status: 'DEGRADED' }
    return { color: 'text-sw-red', bg: 'bg-sw-red/10', status: 'CRITICAL' }
  }

  const healthStatus = getHealthStatus()

  // Alert management
  const createAlert = (type: AlertNotification['type'], title: string, message: string) => {
    const alert: AlertNotification = {
      id: Date.now() + Math.random().toString(),
      type,
      title,
      message,
      timestamp: new Date(),
      dismissed: false
    }
    
    setAlerts(prev => [alert, ...prev.slice(0, 9)]) // Keep last 10 alerts
  }

  const dismissAlert = (alertId: string) => {
    setAlerts(prev => prev.map(alert => 
      alert.id === alertId ? { ...alert, dismissed: true } : alert
    ))
  }

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

    // Log handling now done in global useSocket hook

    const handleSystemMetrics = (data: any) => {
      setSystemMetrics(prev => ({
        ...prev,
        ...data
      }))
    }

    const handleSystemStatus = (data: any) => {
      if (data.services) {
        const updatedServices = services.map(service => {
          const backendService = data.services[service.name]
          if (backendService) {
            return {
              ...service,
              status: backendService.status || service.status,
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
        
        const activeServices = updatedServices.filter(s => s.status === 'online').length
        setSystemMetrics(prev => ({
          ...prev,
          activeServices: activeServices
        }))
      }
    }

    // Subscribe to events (log events now handled globally)
    socket.on('system_status', handleSystemStatus)
    socket.on('service_status_update', handleServiceStatus)
    socket.on('system_metrics', handleSystemMetrics)

    return () => {
      socket.off('system_status', handleSystemStatus)
      socket.off('service_status_update', handleServiceStatus)
      socket.off('system_metrics', handleSystemMetrics)
    }
  }, [socket, services])

  // Filter logs based on level
  useEffect(() => {
    const levelPriority = { DEBUG: 0, INFO: 1, WARNING: 2, ERROR: 3 }
    const minLevel = levelPriority[logLevel]
    const filtered = logs.filter(log => levelPriority[log.level] >= minLevel)
    setFilteredLogs(filtered)
  }, [logs, logLevel])

  // Auto-scroll to bottom
  useEffect(() => {
    if (showFullLog) {
      logsEndRef.current?.scrollIntoView({ behavior: 'smooth' })
    }
  }, [logs.length, showFullLog])

  // Check for issues and create alerts
  useEffect(() => {
    if (systemMetrics.cpuUsage > 80) {
      createAlert('error', 'High CPU Usage', `CPU usage at ${systemMetrics.cpuUsage.toFixed(1)}%`)
    }
    if (systemMetrics.errorRate > 5) {
      createAlert('error', 'High Error Rate', `Error rate at ${systemMetrics.errorRate.toFixed(1)}%`)
    }
    if (onlineServices < services.length * 0.8) {
      createAlert('warning', 'Services Offline', `${services.length - onlineServices} services offline`)
    }
  }, [systemMetrics.cpuUsage, systemMetrics.errorRate, onlineServices, services.length])

  const handleSystemRestart = () => {
    if (!socket) return
    socket.emit('system_command', { action: 'restart' })
  }

  const handleRefreshStatus = () => {
    if (!socket) return
    socket.emit('system_command', { action: 'refresh_config' })
  }

  return (
    <div className="space-y-6">
      {/* System Health Hero */}
      <div className={`sw-panel ${healthStatus.bg} border-2`}>
        <div className="flex items-center justify-between">
          <div className="flex items-center space-x-4">
            <div className="text-center">
              <div className={`text-6xl font-bold ${healthStatus.color} sw-text-glow`}>
                {healthScore}
              </div>
              <div className="text-xs text-sw-blue-300 uppercase tracking-wide">Health Score</div>
            </div>
            <div className="border-l border-sw-blue-600/30 pl-4">
              <div className={`text-2xl font-bold ${healthStatus.color} mb-1`}>
                {healthStatus.status}
              </div>
              <div className="text-sm text-sw-blue-300">
                {onlineServices}/{services.length} Services Online
              </div>
              <div className="text-sm text-sw-blue-300">
                Uptime: {systemMetrics.uptime}
              </div>
            </div>
          </div>
          
          {/* Critical Alerts Badge */}
          {(criticalAlerts > 0 || warningAlerts > 0) && (
            <div className="flex items-center space-x-2">
              {criticalAlerts > 0 && (
                <div className="bg-sw-red/20 border border-sw-red/50 rounded-lg px-3 py-2">
                  <div className="flex items-center space-x-2">
                    <div className="w-2 h-2 rounded-full bg-sw-red animate-pulse"></div>
                    <span className="text-sw-red font-bold text-sm">{criticalAlerts} Critical</span>
                  </div>
                </div>
              )}
              {warningAlerts > 0 && (
                <div className="bg-sw-yellow/20 border border-sw-yellow/50 rounded-lg px-3 py-2">
                  <div className="flex items-center space-x-2">
                    <div className="w-2 h-2 rounded-full bg-sw-yellow animate-pulse"></div>
                    <span className="text-sw-yellow font-bold text-sm">{warningAlerts} Warning</span>
                  </div>
                </div>
              )}
            </div>
          )}
        </div>
      </div>

      {/* Quick Controls */}
      <div className="sw-panel">
        <div className="flex items-center justify-between">
          <SystemModeControl 
            currentMode={systemMode?.current_mode || 'IDLE'}
            onModeChange={(mode) => {
              const systemMode = mode.toUpperCase() as keyof typeof SystemModeEnum
              sendSystemCommand(SystemActionEnum.SET_MODE, { mode: SystemModeEnum[systemMode] })
            }}
            disabled={!socket}
          />
          
          <div className="flex items-center space-x-3">
            <button 
              onClick={handleRefreshStatus}
              className="sw-button bg-sw-blue-600 hover:bg-sw-blue-700"
              disabled={!socket}
            >
              Refresh Status
            </button>
            <button 
              onClick={handleSystemRestart}
              className="sw-button bg-sw-red hover:bg-red-600"
              disabled={!socket}
            >
              Restart System
            </button>
          </div>
        </div>
      </div>

      {/* Service Health Grid */}
      <div className="sw-panel">
        <h3 className="text-lg font-semibold text-sw-blue-100 mb-4 sw-text-glow">
          SERVICE STATUS
        </h3>
        
        <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-4">
          {services.map((service, index) => (
            <div 
              key={index} 
              className="bg-sw-dark-700/30 rounded-lg border border-sw-blue-600/20 p-3 hover:bg-sw-dark-700/50 transition-colors"
            >
              <div className="flex items-center justify-between mb-2">
                <div className={`w-3 h-3 rounded-full ${
                  service.status === 'online' ? 'bg-sw-green animate-pulse' : 'bg-sw-red'
                }`}></div>
                {service.errorCount > 0 && (
                  <span className="text-xs bg-sw-red/20 text-sw-red px-1 rounded">
                    {service.errorCount}
                  </span>
                )}
              </div>
              <div className="text-sm font-medium text-sw-blue-100 truncate">
                {getServiceDisplayName(service.name)}
              </div>
              <div className="text-xs text-sw-blue-300 capitalize">
                {service.status}
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Critical Metrics */}
      <div className="sw-panel">
        <h3 className="text-lg font-semibold text-sw-blue-100 mb-4 sw-text-glow">
          KEY METRICS
        </h3>
        
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          <MetricCard 
            label="CPU Usage" 
            value={systemMetrics.cpuUsage > 0 ? `${systemMetrics.cpuUsage.toFixed(1)}%` : "--"}
            status={
              systemMetrics.cpuUsage > 80 ? 'critical' :
              systemMetrics.cpuUsage > 50 ? 'warning' : 'good'
            }
          />
          <MetricCard 
            label="Memory" 
            value={systemMetrics.totalMemory > 0 ? `${systemMetrics.totalMemory} MB` : "-- MB"}
            status={
              systemMetrics.totalMemory > 1000 ? 'critical' :
              systemMetrics.totalMemory > 500 ? 'warning' : 'good'
            }
          />
          <MetricCard 
            label="Response Time" 
            value={systemMetrics.eventLatency > 0 ? `${systemMetrics.eventLatency}ms` : "--"}
            status={
              systemMetrics.eventLatency > 500 ? 'critical' :
              systemMetrics.eventLatency > 100 ? 'warning' : 'good'
            }
          />
          <MetricCard 
            label="Error Rate" 
            value={systemMetrics.errorRate >= 0 ? `${systemMetrics.errorRate.toFixed(1)}%` : "--"}
            status={
              systemMetrics.errorRate > 5 ? 'critical' :
              systemMetrics.errorRate > 1 ? 'warning' : 'good'
            }
          />
        </div>
      </div>

      {/* Recent Activity */}
      <div className="sw-panel">
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-lg font-semibold text-sw-blue-100 sw-text-glow">
            RECENT ACTIVITY
          </h3>
          <div className="flex items-center space-x-2">
            <select
              value={logLevel}
              onChange={(e) => setLogLevel(e.target.value as any)}
              className="px-2 py-1 bg-sw-dark-700 border border-sw-blue-600/30 rounded text-sw-blue-100 text-sm"
            >
              <option value="DEBUG">All</option>
              <option value="INFO">Info+</option>
              <option value="WARNING">Warning+</option>
              <option value="ERROR">Error Only</option>
            </select>
            <button
              onClick={() => setShowFullLog(!showFullLog)}
              className="text-xs sw-button py-1 px-2"
            >
              {showFullLog ? 'Show Less' : 'Show More'}
            </button>
          </div>
        </div>
        
        <div className="bg-sw-dark-700/50 rounded-lg border border-sw-blue-600/20 p-4">
          {filteredLogs.length === 0 ? (
            <div className="text-sw-blue-300/50 text-xs italic text-center py-4">
              No recent activity to display
            </div>
          ) : (
            <div className={`space-y-1 font-mono text-xs ${showFullLog ? 'max-h-96 overflow-y-auto' : ''}`}>
              {(showFullLog ? filteredLogs : filteredLogs.slice(-10)).map((log) => (
                <div key={log.id} className="flex items-start space-x-2 hover:bg-sw-blue-600/10 px-1 rounded">
                  <span className="text-sw-blue-400 flex-shrink-0 w-16">
                    {log.timestamp.split(' ')[1]}
                  </span>
                  <span className={`flex-shrink-0 w-12 font-semibold ${
                    log.level === 'ERROR' ? 'text-sw-red' :
                    log.level === 'WARNING' ? 'text-sw-yellow' :
                    log.level === 'INFO' ? 'text-sw-green' :
                    'text-sw-blue-300'
                  }`}>
                    {log.level}
                  </span>
                  <span className="text-sw-blue-300 flex-shrink-0 w-20 truncate">
                    {log.service}:
                  </span>
                  <span className="text-sw-blue-100 flex-1">
                    {log.message}
                  </span>
                </div>
              ))}
              <div ref={logsEndRef} />
            </div>
          )}
        </div>
      </div>

      {/* Active Alerts */}
      {alerts.filter(a => !a.dismissed).length > 0 && (
        <div className="sw-panel">
          <h3 className="text-lg font-semibold text-sw-blue-100 mb-4 sw-text-glow">
            ACTIVE ALERTS
          </h3>
          
          <div className="space-y-2">
            {alerts.filter(a => !a.dismissed).slice(0, 5).map((alert) => (
              <div
                key={alert.id}
                className={`flex items-center justify-between p-3 rounded-lg border ${
                  alert.type === 'error' 
                    ? 'bg-sw-red/10 border-sw-red/30' 
                    : 'bg-sw-yellow/10 border-sw-yellow/30'
                }`}
              >
                <div className="flex items-center space-x-3">
                  <div className={`w-2 h-2 rounded-full ${
                    alert.type === 'error' ? 'bg-sw-red animate-pulse' : 'bg-sw-yellow animate-pulse'
                  }`}></div>
                  <div>
                    <div className={`text-sm font-semibold ${
                      alert.type === 'error' ? 'text-sw-red' : 'text-sw-yellow'
                    }`}>
                      {alert.title}
                    </div>
                    <div className="text-xs text-sw-blue-300">{alert.message}</div>
                  </div>
                </div>
                <button
                  onClick={() => dismissAlert(alert.id)}
                  className="text-xs px-2 py-1 rounded bg-sw-dark-700 hover:bg-sw-dark-600 text-sw-blue-300"
                >
                  Dismiss
                </button>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

interface MetricCardProps {
  label: string
  value: string
  status: 'good' | 'warning' | 'critical'
}

function MetricCard({ label, value, status }: MetricCardProps) {
  const getStatusColor = () => {
    switch (status) {
      case 'good':
        return 'text-sw-green'
      case 'warning':
        return 'text-sw-yellow'
      case 'critical':
        return 'text-sw-red'
      default:
        return 'text-sw-blue-300'
    }
  }

  return (
    <div className="text-center p-4 bg-sw-dark-700/30 rounded-lg border border-sw-blue-600/20">
      <div className={`text-2xl font-bold mb-1 ${getStatusColor()}`}>{value}</div>
      <div className="text-xs text-sw-blue-300 uppercase tracking-wide">{label}</div>
    </div>
  )
}