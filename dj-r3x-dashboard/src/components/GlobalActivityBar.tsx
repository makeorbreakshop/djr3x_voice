'use client'

import { useState } from 'react'
import { useSocketContext } from '@/contexts/SocketContext'
import { LogEntry } from '@/hooks/useSocket'

// Helper function to get log level color
const getLogLevelColor = (level: LogEntry['level']) => {
  switch (level) {
    case 'ERROR':
      return 'text-sw-red'
    case 'WARNING':
      return 'text-sw-yellow'
    case 'INFO':
      return 'text-sw-green'
    default:
      return 'text-sw-blue-300'
  }
}

export default function GlobalActivityBar() {
  const { logs } = useSocketContext()
  const [isExpanded, setIsExpanded] = useState(false)
  
  const latestLog = logs[logs.length - 1]

  return (
    <div className="fixed bottom-0 left-0 right-0 bg-sw-dark-700 border-t border-sw-blue-500/30 z-50">
      {/* Collapsed: Show latest log entry */}
      <div 
        className="px-4 py-2 cursor-pointer hover:bg-sw-blue-600/10 transition-colors flex items-center justify-between" 
        onClick={() => setIsExpanded(!isExpanded)}
      >
        <div className="flex items-center space-x-2 text-xs min-w-0 flex-1">
          {latestLog ? (
            <>
              <span className="text-sw-blue-400 flex-shrink-0">
                {latestLog.timestamp.split(' ')[1]}
              </span>
              <span className={`font-semibold flex-shrink-0 ${getLogLevelColor(latestLog.level)}`}>
                {latestLog.level}
              </span>
              <span className="text-sw-blue-300 flex-shrink-0">
                {latestLog.service}:
              </span>
              <span className="text-sw-blue-100 truncate">
                {latestLog.message}
              </span>
            </>
          ) : (
            <span className="text-sw-blue-400">No recent activity</span>
          )}
        </div>
        
        {/* Expand/Collapse Indicator */}
        <div className="flex items-center space-x-2 flex-shrink-0 ml-4">
          <span className="text-xs text-sw-blue-400">
            {logs.length} logs
          </span>
          <svg 
            className={`w-4 h-4 text-sw-blue-400 transition-transform ${
              isExpanded ? 'rotate-180' : ''
            }`}
            fill="none" 
            stroke="currentColor" 
            viewBox="0 0 24 24"
          >
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 15l7-7 7 7" />
          </svg>
        </div>
      </div>

      {/* Expanded: Show scrollable log list */}
      {isExpanded && (
        <div className="border-t border-sw-blue-500/20 bg-sw-dark-800 max-h-96 overflow-y-auto">
          {logs.length === 0 ? (
            <div className="text-sw-blue-300/50 text-xs italic text-center py-8">
              No logs to display
            </div>
          ) : (
            <div className="space-y-1 font-mono text-xs p-2">
              {logs.map((log) => (
                <div 
                  key={log.id} 
                  className="flex items-start space-x-2 hover:bg-sw-blue-600/10 px-2 py-1 rounded"
                >
                  <span className="text-sw-blue-400 flex-shrink-0 w-16">
                    {log.timestamp.split(' ')[1]}
                  </span>
                  <span className={`flex-shrink-0 w-12 font-semibold ${getLogLevelColor(log.level)}`}>
                    {log.level}
                  </span>
                  <span className="text-sw-blue-300 flex-shrink-0 w-20 truncate">
                    {log.service}:
                  </span>
                  <span className="text-sw-blue-100 flex-1 break-words">
                    {log.message}
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  )
}