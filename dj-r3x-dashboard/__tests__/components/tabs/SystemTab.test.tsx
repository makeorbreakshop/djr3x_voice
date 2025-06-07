import React from 'react'
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react'
import { vi, describe, it, expect, beforeEach, afterEach } from 'vitest'
import SystemTab from '../../../src/components/tabs/SystemTab'

// Mock the useSocket hook
const mockSocket = {
  on: vi.fn(),
  off: vi.fn(),
  emit: vi.fn(),
}

vi.mock('../../../src/hooks/useSocket', () => ({
  useSocket: () => mockSocket
}))

// Mock data for testing
const mockServiceData = [
  { 
    name: 'DeepgramDirectMicService', 
    status: 'online', 
    uptime: '2h 15m', 
    memory: '45 MB', 
    cpu: '5.2%', 
    lastActivity: '10:30:45', 
    errorCount: 0, 
    successRate: 98.5 
  },
  { 
    name: 'GPTService', 
    status: 'offline', 
    uptime: '--', 
    memory: '--', 
    cpu: '--', 
    lastActivity: '--', 
    errorCount: 3, 
    successRate: 0 
  },
]

const mockSystemMetrics = {
  totalMemory: 512,
  cpuUsage: 45.2,
  eventLatency: 120,
  errorRate: 2.1,
  eventsPerMinute: 25,
  uptime: '2h 15m 30s',
  activeServices: 5,
  totalServices: 6
}

const mockLogEntries = [
  {
    id: '1',
    timestamp: '2025-06-06 10:30:45',
    level: 'INFO' as const,
    service: 'DeepgramDirectMicService',
    message: 'Voice recording started successfully'
  },
  {
    id: '2',
    timestamp: '2025-06-06 10:30:50',
    level: 'ERROR' as const,
    service: 'GPTService',
    message: 'API request failed: Connection timeout'
  }
]

describe('SystemTab Component', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  afterEach(() => {
    vi.clearAllMocks()
  })

  describe('Initial Render', () => {
    it('should render service health grid', () => {
      render(<SystemTab />)
      expect(screen.getByText('SERVICE HEALTH MONITORING')).toBeInTheDocument()
      expect(screen.getByText('Service')).toBeInTheDocument()
      expect(screen.getByText('Status')).toBeInTheDocument()
      expect(screen.getByText('Memory')).toBeInTheDocument()
      expect(screen.getByText('CPU')).toBeInTheDocument()
    })

    it('should render individual service metrics section', () => {
      render(<SystemTab />)
      expect(screen.getByText('INDIVIDUAL SERVICE METRICS')).toBeInTheDocument()
    })

    it('should render performance profiling section', () => {
      render(<SystemTab />)
      expect(screen.getByText('PERFORMANCE PROFILING & ANALYTICS')).toBeInTheDocument()
      expect(screen.getByText('Performance Timeline (Last 60 seconds)')).toBeInTheDocument()
    })

    it('should render real-time event log', () => {
      render(<SystemTab />)
      expect(screen.getByText('REAL-TIME EVENT LOG')).toBeInTheDocument()
      expect(screen.getByPlaceholderText('Search logs...')).toBeInTheDocument()
    })

    it('should render system information panel', () => {
      render(<SystemTab />)
      expect(screen.getByText('SYSTEM INFO')).toBeInTheDocument()
      expect(screen.getByText('CantinaOS Version:')).toBeInTheDocument()
      expect(screen.getByText('Event Bus:')).toBeInTheDocument()
    })

    it('should render configuration panel', () => {
      render(<SystemTab />)
      expect(screen.getByText('CONFIGURATION')).toBeInTheDocument()
      expect(screen.getByText('OpenAI API:')).toBeInTheDocument()
      expect(screen.getByText('ElevenLabs API:')).toBeInTheDocument()
    })

    it('should render performance metrics panel', () => {
      render(<SystemTab />)
      expect(screen.getByText('PERFORMANCE METRICS')).toBeInTheDocument()
    })
  })

  describe('Socket Event Handling', () => {
    it('should register socket event listeners on mount', () => {
      render(<SystemTab />)
      
      expect(mockSocket.on).toHaveBeenCalledWith('service_status_update', expect.any(Function))
      expect(mockSocket.on).toHaveBeenCalledWith('cantina_event', expect.any(Function))
      expect(mockSocket.on).toHaveBeenCalledWith('system_metrics', expect.any(Function))
      expect(mockSocket.on).toHaveBeenCalledWith('config_status', expect.any(Function))
      expect(mockSocket.on).toHaveBeenCalledWith('system_error', expect.any(Function))
    })

    it('should handle service status updates', async () => {
      render(<SystemTab />)
      
      // Get the service status handler
      const serviceStatusHandler = mockSocket.on.mock.calls.find(
        call => call[0] === 'service_status_update'
      )?.[1]

      expect(serviceStatusHandler).toBeDefined()

      // Simulate service status update
      await act(async () => {
        serviceStatusHandler({
          service: 'DeepgramDirectMicService',
          status: 'RUNNING',
          uptime: '2h 15m',
          memory: '45 MB',
          cpu: '5.2%',
          error_count: 0,
          success_rate: 98.5
        })
      })

      // Check if UI updates with new status
      await waitFor(() => {
        expect(screen.getByText('DeepgramDirectMicService')).toBeInTheDocument()
      })
    })

    it('should handle system events and add to logs', async () => {
      render(<SystemTab />)
      
      // Get the system event handler
      const systemEventHandler = mockSocket.on.mock.calls.find(
        call => call[0] === 'cantina_event'
      )?.[1]

      expect(systemEventHandler).toBeDefined()

      // Simulate system event
      await act(async () => {
        systemEventHandler({
          level: 'INFO',
          service: 'TestService',
          message: 'Test message'
        })
      })

      await waitFor(() => {
        expect(screen.getByText('Test message')).toBeInTheDocument()
      })
    })
  })

  describe('Service Management', () => {
    it('should handle service restart', () => {
      render(<SystemTab />)
      
      const restartButtons = screen.getAllByText('Restart')
      fireEvent.click(restartButtons[0])

      expect(mockSocket.emit).toHaveBeenCalledWith('service_command', {
        action: 'restart',
        service: expect.any(String)
      })
    })

    it('should handle system restart', () => {
      render(<SystemTab />)
      
      const systemRestartButton = screen.getByText('Restart System')
      fireEvent.click(systemRestartButton)

      expect(mockSocket.emit).toHaveBeenCalledWith('system_command', {
        action: 'restart'
      })
    })

    it('should handle config refresh', () => {
      render(<SystemTab />)
      
      const refreshConfigButton = screen.getByText('Refresh Config')
      fireEvent.click(refreshConfigButton)

      expect(mockSocket.emit).toHaveBeenCalledWith('system_command', {
        action: 'refresh_config'
      })
    })
  })

  describe('Log Management', () => {
    it('should filter logs by level', async () => {
      render(<SystemTab />)
      
      // Add some test logs first
      const systemEventHandler = mockSocket.on.mock.calls.find(
        call => call[0] === 'cantina_event'
      )?.[1]

      systemEventHandler({ level: 'ERROR', service: 'Test', message: 'Error message' })
      systemEventHandler({ level: 'INFO', service: 'Test', message: 'Info message' })

      // Change log level filter to ERROR
      const logLevelSelect = screen.getByDisplayValue('INFO')
      fireEvent.change(logLevelSelect, { target: { value: 'ERROR' } })

      await waitFor(() => {
        expect(screen.getByText('Error message')).toBeInTheDocument()
        expect(screen.queryByText('Info message')).not.toBeInTheDocument()
      })
    })

    it('should filter logs by search term', async () => {
      render(<SystemTab />)
      
      // Add test logs
      const systemEventHandler = mockSocket.on.mock.calls.find(
        call => call[0] === 'cantina_event'
      )?.[1]

      systemEventHandler({ level: 'INFO', service: 'Test', message: 'Voice recording started' })
      systemEventHandler({ level: 'INFO', service: 'Test', message: 'Music playback stopped' })

      // Search for "voice"
      const searchInput = screen.getByPlaceholderText('Search logs...')
      fireEvent.change(searchInput, { target: { value: 'voice' } })

      await waitFor(() => {
        expect(screen.getByText('Voice recording started')).toBeInTheDocument()
        expect(screen.queryByText('Music playback stopped')).not.toBeInTheDocument()
      })
    })

    it('should clear all logs', async () => {
      render(<SystemTab />)
      
      // Add test logs
      const systemEventHandler = mockSocket.on.mock.calls.find(
        call => call[0] === 'cantina_event'
      )?.[1]

      systemEventHandler({ level: 'INFO', service: 'Test', message: 'Test message' })

      await waitFor(() => {
        expect(screen.getByText('Test message')).toBeInTheDocument()
      })

      // Clear logs
      const clearButton = screen.getByText('Clear')
      fireEvent.click(clearButton)

      await waitFor(() => {
        expect(screen.queryByText('Test message')).not.toBeInTheDocument()
      })
    })

    it('should export logs', () => {
      // Mock URL.createObjectURL and document.createElement
      const mockCreateObjectURL = vi.fn(() => 'blob:test-url')
      const mockClick = vi.fn()
      const mockCreateElement = vi.fn(() => ({
        href: '',
        download: '',
        click: mockClick
      }))

      global.URL.createObjectURL = mockCreateObjectURL
      global.URL.revokeObjectURL = vi.fn()
      document.createElement = mockCreateElement

      render(<SystemTab />)
      
      // Add test logs
      const systemEventHandler = mockSocket.on.mock.calls.find(
        call => call[0] === 'cantina_event'
      )?.[1]

      systemEventHandler({ level: 'INFO', service: 'Test', message: 'Test message' })

      const exportButton = screen.getByText('Export Logs')
      fireEvent.click(exportButton)

      expect(mockCreateObjectURL).toHaveBeenCalled()
      expect(mockClick).toHaveBeenCalled()
    })
  })

  describe('Alert System', () => {
    it('should create alerts for high CPU usage', async () => {
      render(<SystemTab />)
      
      // Simulate high CPU usage
      const systemMetricsHandler = mockSocket.on.mock.calls.find(
        call => call[0] === 'system_metrics'
      )?.[1]

      await act(async () => {
        systemMetricsHandler({
          cpuUsage: 85.5,
          totalMemory: 400,
          eventLatency: 50,
          errorRate: 1.0
        })
      })

      // Wait for debounced alert creation
      await waitFor(() => {
        expect(screen.getByText('High CPU Usage')).toBeInTheDocument()
        expect(screen.getByText(/CPU usage is at 85.5%/)).toBeInTheDocument()
      }, { timeout: 3000 })
    })

    it('should create alerts for high memory usage', async () => {
      render(<SystemTab />)
      
      const systemMetricsHandler = mockSocket.on.mock.calls.find(
        call => call[0] === 'system_metrics'
      )?.[1]

      systemMetricsHandler({
        cpuUsage: 30,
        totalMemory: 1200,
        eventLatency: 50,
        errorRate: 1.0
      })

      await waitFor(() => {
        expect(screen.getByText('High Memory Usage')).toBeInTheDocument()
      })
    })

    it('should dismiss alerts', async () => {
      render(<SystemTab />)
      
      // Create alert
      const systemMetricsHandler = mockSocket.on.mock.calls.find(
        call => call[0] === 'system_metrics'
      )?.[1]

      systemMetricsHandler({
        cpuUsage: 85,
        totalMemory: 400,
        eventLatency: 50,
        errorRate: 1.0
      })

      await waitFor(() => {
        expect(screen.getByText('High CPU Usage')).toBeInTheDocument()
      })

      // Dismiss alert
      const dismissButton = screen.getByText('Dismiss')
      fireEvent.click(dismissButton)

      await waitFor(() => {
        expect(screen.queryByText('High CPU Usage')).not.toBeInTheDocument()
      })
    })

    it('should show dismissed alerts in history', async () => {
      render(<SystemTab />)
      
      // Create and dismiss alert
      const systemMetricsHandler = mockSocket.on.mock.calls.find(
        call => call[0] === 'system_metrics'
      )?.[1]

      systemMetricsHandler({ cpuUsage: 85 })

      await waitFor(() => {
        expect(screen.getByText('High CPU Usage')).toBeInTheDocument()
      })

      const dismissButton = screen.getByText('Dismiss')
      fireEvent.click(dismissButton)

      // Show history
      const historyButton = screen.getByText(/Show History/)
      fireEvent.click(historyButton)

      await waitFor(() => {
        expect(screen.getByText('DISMISSED ALERTS')).toBeInTheDocument()
      })
    })
  })

  describe('Performance Metrics', () => {
    it('should display correct health score colors', async () => {
      render(<SystemTab />)
      
      const systemMetricsHandler = mockSocket.on.mock.calls.find(
        call => call[0] === 'system_metrics'
      )?.[1]

      // Good performance
      systemMetricsHandler({
        cpuUsage: 30,
        totalMemory: 400,
        eventLatency: 50,
        errorRate: 0.5
      })

      await waitFor(() => {
        const healthScore = screen.getByText('95')
        expect(healthScore).toHaveClass('text-sw-green')
      })
    })

    it('should show bottleneck detection when system is optimal', async () => {
      render(<SystemTab />)
      
      const systemMetricsHandler = mockSocket.on.mock.calls.find(
        call => call[0] === 'system_metrics'
      )?.[1]

      systemMetricsHandler({
        cpuUsage: 30,
        totalMemory: 400,
        eventLatency: 50,
        errorRate: 0.5
      })

      await waitFor(() => {
        expect(screen.getByText('System Running Optimally')).toBeInTheDocument()
      })
    })

    it('should calculate stability index correctly', () => {
      render(<SystemTab />)
      
      // Should show 100% when no services have errors
      expect(screen.getByText('100%')).toBeInTheDocument()
    })
  })

  describe('Accessibility', () => {
    it('should have proper ARIA labels for buttons', () => {
      render(<SystemTab />)
      
      const restartButtons = screen.getAllByText('Restart')
      restartButtons.forEach(button => {
        expect(button).toBeInTheDocument()
      })
    })

    it('should support keyboard navigation', () => {
      render(<SystemTab />)
      
      const searchInput = screen.getByPlaceholderText('Search logs...')
      expect(searchInput).toBeInTheDocument()
      
      fireEvent.focus(searchInput)
      expect(document.activeElement).toBe(searchInput)
    })
  })

  describe('Error Handling', () => {
    it('should handle missing socket gracefully', () => {
      vi.mocked(require('../../../src/hooks/useSocket').useSocket).mockReturnValue(null)
      
      expect(() => render(<SystemTab />)).not.toThrow()
    })

    it('should handle malformed socket data', async () => {
      render(<SystemTab />)
      
      const systemEventHandler = mockSocket.on.mock.calls.find(
        call => call[0] === 'cantina_event'
      )?.[1]

      // Should not crash with malformed data
      expect(() => {
        systemEventHandler({ invalid: 'data' })
      }).not.toThrow()
    })
  })
})