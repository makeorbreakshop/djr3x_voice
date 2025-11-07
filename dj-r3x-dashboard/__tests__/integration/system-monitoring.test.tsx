import React from 'react'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { vi, describe, it, expect, beforeEach } from 'vitest'
import SystemTab from '../../src/components/tabs/SystemTab'

// Mock Socket.io
const mockSocket = {
  on: vi.fn(),
  off: vi.fn(),
  emit: vi.fn(),
}

vi.mock('../../src/hooks/useSocket', () => ({
  useSocket: () => mockSocket
}))

describe('System Monitoring Integration Tests', () => {
  let serviceStatusHandler: any
  let systemEventHandler: any
  let systemMetricsHandler: any
  let configStatusHandler: any

  beforeEach(() => {
    vi.clearAllMocks()
    
    // Setup handlers
    mockSocket.on.mockImplementation((event, handler) => {
      switch (event) {
        case 'service_status_update':
          serviceStatusHandler = handler
          break
        case 'cantina_event':
          systemEventHandler = handler
          break
        case 'system_metrics':
          systemMetricsHandler = handler
          break
        case 'config_status':
          configStatusHandler = handler
          break
        case 'system_error':
          systemEventHandler = handler
          break
      }
    })
  })

  describe('End-to-End Service Monitoring', () => {
    it('should handle complete service lifecycle', async () => {
      render(<SystemTab />)

      // 1. Service starts online
      serviceStatusHandler({
        service: 'DeepgramDirectMicService',
        status: 'RUNNING',
        uptime: '0h 1m',
        memory: '32 MB',
        cpu: '2.1%',
        error_count: 0,
        success_rate: 100
      })

      await waitFor(() => {
        expect(screen.getByText('DeepgramDirectMicService')).toBeInTheDocument()
        expect(screen.getByText('online')).toBeInTheDocument()
      })

      // 2. Service processes events
      systemEventHandler({
        level: 'INFO',
        service: 'DeepgramDirectMicService',
        message: 'Started voice transcription session'
      })

      await waitFor(() => {
        expect(screen.getByText('Started voice transcription session')).toBeInTheDocument()
      })

      // 3. Service encounters error
      systemEventHandler({
        level: 'ERROR',
        service: 'DeepgramDirectMicService',
        message: 'Transcription API rate limit exceeded'
      })

      serviceStatusHandler({
        service: 'DeepgramDirectMicService',
        status: 'RUNNING',
        uptime: '0h 5m',
        memory: '35 MB',
        cpu: '3.2%',
        error_count: 1,
        success_rate: 95.5
      })

      await waitFor(() => {
        expect(screen.getByText('Transcription API rate limit exceeded')).toBeInTheDocument()
        expect(screen.getByText('1 errors')).toBeInTheDocument()
      })

      // 4. Service recovers
      systemEventHandler({
        level: 'INFO',
        service: 'DeepgramDirectMicService',
        message: 'Transcription service recovered'
      })

      serviceStatusHandler({
        service: 'DeepgramDirectMicService',
        status: 'RUNNING',
        uptime: '0h 7m',
        memory: '33 MB',
        cpu: '2.8%',
        error_count: 1,
        success_rate: 98.2
      })

      await waitFor(() => {
        expect(screen.getByText('Transcription service recovered')).toBeInTheDocument()
        expect(screen.getByText('98.2%')).toBeInTheDocument()
      })
    })

    it('should handle multiple services with varying performance', async () => {
      render(<SystemTab />)

      // Setup multiple services
      const services = [
        {
          service: 'DeepgramDirectMicService',
          status: 'RUNNING',
          uptime: '2h 15m',
          memory: '45 MB',
          cpu: '5.2%',
          error_count: 0,
          success_rate: 98.5
        },
        {
          service: 'GPTService',
          status: 'RUNNING',
          uptime: '2h 10m',
          memory: '120 MB',
          cpu: '15.7%',
          error_count: 2,
          success_rate: 92.1
        },
        {
          service: 'ElevenLabsService',
          status: 'ERROR',
          uptime: '1h 45m',
          memory: '80 MB',
          cpu: '0.1%',
          error_count: 15,
          success_rate: 45.2
        }
      ]

      // Simulate all services reporting status
      services.forEach(service => {
        serviceStatusHandler(service)
      })

      await waitFor(() => {
        expect(screen.getByText('DeepgramDirectMicService')).toBeInTheDocument()
        expect(screen.getByText('GPTService')).toBeInTheDocument()
        expect(screen.getByText('ElevenLabsService')).toBeInTheDocument()
      })

      // Verify different service states are displayed correctly
      expect(screen.getByText('98.5%')).toBeInTheDocument() // Deepgram success rate
      expect(screen.getByText('92.1%')).toBeInTheDocument() // GPT success rate
      expect(screen.getByText('45.2%')).toBeInTheDocument() // ElevenLabs success rate
      expect(screen.getByText('15 errors')).toBeInTheDocument() // ElevenLabs errors
    })
  })

  describe('Performance Monitoring Integration', () => {
    it('should track system performance over time', async () => {
      render(<SystemTab />)

      // Simulate performance metrics updates
      const performanceScenarios = [
        { cpuUsage: 25.5, totalMemory: 320, eventLatency: 45, errorRate: 0.5 },
        { cpuUsage: 45.2, totalMemory: 450, eventLatency: 120, errorRate: 1.2 },
        { cpuUsage: 78.9, totalMemory: 680, eventLatency: 250, errorRate: 3.8 },
        { cpuUsage: 92.1, totalMemory: 890, eventLatency: 450, errorRate: 8.2 }
      ]

      for (let i = 0; i < performanceScenarios.length; i++) {
        const metrics = performanceScenarios[i]
        systemMetricsHandler(metrics)

        await waitFor(() => {
          expect(screen.getByText(`${metrics.cpuUsage.toFixed(1)}%`)).toBeInTheDocument()
        })

        // Check health score changes
        if (i === 0) {
          expect(screen.getByText('95')).toBeInTheDocument() // Good performance
        } else if (i === 3) {
          expect(screen.getByText('42')).toBeInTheDocument() // Poor performance
        }
      }
    })

    it('should generate appropriate alerts for performance issues', async () => {
      render(<SystemTab />)

      // High CPU usage should generate alert
      systemMetricsHandler({
        cpuUsage: 85.5,
        totalMemory: 400,
        eventLatency: 50,
        errorRate: 1.0
      })

      await waitFor(() => {
        expect(screen.getByText('High CPU Usage')).toBeInTheDocument()
        expect(screen.getByText(/CPU usage is at 85.5%/)).toBeInTheDocument()
      })

      // High memory usage should generate additional alert
      systemMetricsHandler({
        cpuUsage: 85.5,
        totalMemory: 1200,
        eventLatency: 50,
        errorRate: 1.0
      })

      await waitFor(() => {
        expect(screen.getByText('High Memory Usage')).toBeInTheDocument()
      })

      // Critical latency should generate critical alert
      systemMetricsHandler({
        cpuUsage: 85.5,
        totalMemory: 1200,
        eventLatency: 1500,
        errorRate: 1.0
      })

      await waitFor(() => {
        expect(screen.getByText('Critical Response Latency')).toBeInTheDocument()
        expect(screen.getByText('PERSISTENT')).toBeInTheDocument()
      })
    })
  })

  describe('Configuration Monitoring Integration', () => {
    it('should track API configuration status', async () => {
      render(<SystemTab />)

      // Initially all configs offline
      configStatusHandler({
        openai: false,
        elevenlabs: false,
        deepgram: false,
        arduino: false
      })

      await waitFor(() => {
        expect(screen.getAllByText('Not Configured')).toHaveLength(3)
        expect(screen.getByText('Not Connected')).toBeInTheDocument()
      })

      // Gradually configure APIs
      configStatusHandler({
        openai: true,
        elevenlabs: false,
        deepgram: false,
        arduino: false
      })

      await waitFor(() => {
        expect(screen.getByText('Configured')).toBeInTheDocument()
        expect(screen.getAllByText('Not Configured')).toHaveLength(2)
      })

      // All configured
      configStatusHandler({
        openai: true,
        elevenlabs: true,
        deepgram: true,
        arduino: true
      })

      await waitFor(() => {
        expect(screen.getAllByText('Configured')).toHaveLength(3)
        expect(screen.getByText('Connected')).toBeInTheDocument()
      })
    })
  })

  describe('Log Management Integration', () => {
    it('should handle high-volume log ingestion', async () => {
      render(<SystemTab />)

      // Simulate high-volume logs
      const services = ['DeepgramDirectMicService', 'GPTService', 'ElevenLabsService', 'MusicControllerService']
      const levels = ['DEBUG', 'INFO', 'WARNING', 'ERROR']

      // Generate 100 log entries
      for (let i = 0; i < 100; i++) {
        systemEventHandler({
          level: levels[i % levels.length],
          service: services[i % services.length],
          message: `Log message ${i + 1} - System processing normally`
        })
      }

      await waitFor(() => {
        expect(screen.getByText(/Showing \d+ of 100 logs/)).toBeInTheDocument()
      })

      // Test filtering works with high volume
      const searchInput = screen.getByPlaceholderText('Search logs...')
      fireEvent.change(searchInput, { target: { value: 'message 50' } })

      await waitFor(() => {
        expect(screen.getByText('Log message 50 - System processing normally')).toBeInTheDocument()
        expect(screen.getByText(/Showing 1 of 100 logs/)).toBeInTheDocument()
      })
    })

    it('should handle log export with large datasets', async () => {
      render(<SystemTab />)

      // Mock file operations
      const mockBlob = vi.fn()
      const mockClick = vi.fn()
      const mockCreateElement = vi.fn(() => ({
        href: '',
        download: '',
        click: mockClick
      }))

      global.Blob = mockBlob
      global.URL.createObjectURL = vi.fn(() => 'blob:test-url')
      document.createElement = mockCreateElement

      // Generate logs
      for (let i = 0; i < 50; i++) {
        systemEventHandler({
          level: 'INFO',
          service: 'TestService',
          message: `Test log message ${i + 1}`
        })
      }

      await waitFor(() => {
        expect(screen.getByText(/Showing \d+ of 50 logs/)).toBeInTheDocument()
      })

      // Export logs
      const exportButton = screen.getByText('Export Logs')
      fireEvent.click(exportButton)

      expect(mockBlob).toHaveBeenCalled()
      expect(mockClick).toHaveBeenCalled()
    })
  })

  describe('Service Control Integration', () => {
    it('should handle service restart operations', async () => {
      render(<SystemTab />)

      // Setup service
      serviceStatusHandler({
        service: 'GPTService',
        status: 'RUNNING',
        uptime: '1h 30m',
        memory: '100 MB',
        cpu: '12.5%',
        error_count: 0,
        success_rate: 95.5
      })

      await waitFor(() => {
        expect(screen.getByText('GPTService')).toBeInTheDocument()
      })

      // Restart service
      const restartButtons = screen.getAllByText('Restart')
      fireEvent.click(restartButtons[0])

      expect(mockSocket.emit).toHaveBeenCalledWith('service_command', {
        action: 'restart',
        service: 'GPTService'
      })

      // Simulate service restarting
      serviceStatusHandler({
        service: 'GPTService',
        status: 'STARTING',
        uptime: '0h 0m',
        memory: '50 MB',
        cpu: '8.2%',
        error_count: 0,
        success_rate: 0
      })

      // Service comes back online
      serviceStatusHandler({
        service: 'GPTService',
        status: 'RUNNING',
        uptime: '0h 1m',
        memory: '85 MB',
        cpu: '10.1%',
        error_count: 0,
        success_rate: 100
      })

      await waitFor(() => {
        expect(screen.getByText('100%')).toBeInTheDocument()
      })
    })
  })
})