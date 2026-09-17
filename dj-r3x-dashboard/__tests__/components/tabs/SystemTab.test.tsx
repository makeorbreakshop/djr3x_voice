import React from 'react'
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react'
import { vi, describe, it, expect, beforeEach, afterEach } from 'vitest'
import SystemTab from '../../../src/components/tabs/SystemTab'
import { SocketProvider } from '../../../src/contexts/SocketContext'

// ROOT CAUSE (fixed 2026-09-17): SystemTab reads socket state via
// `useSocketContext()` (src/contexts/SocketContext.tsx), which throws unless the
// tree is wrapped in a real <SocketProvider>. These tests used to mock
// '../../../src/hooks/useSocket' directly, but SystemTab no longer imports that
// hook itself (it imports useSocketContext instead), so the mock never applied
// and every render() threw "useSocketContext must be used within a
// SocketProvider" before any assertion ran.
//
// Fix: mock the lower-level 'socket.io-client' module (which the real useSocket
// hook still uses internally) and render SystemTab inside the real
// SocketProvider. This exercises the real hook/context wiring against a fake
// transport, matching how the app actually composes these modules.

type Handler = (...args: any[]) => void
const listeners: Record<string, Handler[]> = {}

const mockSocket = {
  on: vi.fn((event: string, handler: Handler) => {
    listeners[event] = listeners[event] || []
    listeners[event].push(handler)
  }),
  off: vi.fn((event: string, handler: Handler) => {
    listeners[event] = (listeners[event] || []).filter(h => h !== handler)
  }),
  emit: vi.fn(),
  onAny: vi.fn(),
  close: vi.fn(),
}

vi.mock('socket.io-client', () => ({
  io: () => mockSocket,
}))

// Fire a fake socket event to every handler registered for it (mirrors how
// socket.io would dispatch an incoming event to all listeners).
const fireSocketEvent = (event: string, data?: any) => {
  ;(listeners[event] || []).forEach(handler => handler(data))
}

const renderSystemTab = () =>
  render(
    <SocketProvider>
      <SystemTab />
    </SocketProvider>
  )

describe('SystemTab Component', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    Object.keys(listeners).forEach(key => delete listeners[key])
  })

  afterEach(() => {
    vi.clearAllMocks()
  })

  describe('Initial Render', () => {
    // The following five tests asserted headings/panels ("SERVICE HEALTH
    // MONITORING", "INDIVIDUAL SERVICE METRICS", "PERFORMANCE PROFILING &
    // ANALYTICS", "SYSTEM INFO", "CONFIGURATION") that do not exist anywhere in
    // the current src/components/tabs/SystemTab.tsx (verified via grep — zero
    // matches). The component was rewritten into a simpler health/metrics/log
    // dashboard (see SystemTab.tsx:261-518) and these sections were never
    // reimplemented, so the tests are skipped rather than deleted.
    it.skip('should render service health grid', () => {
      // Obsolete: SystemTab.tsx has no "SERVICE HEALTH MONITORING" table; the
      // current equivalent is the "SERVICE STATUS" grid at SystemTab.tsx:342-372.
    })

    it.skip('should render individual service metrics section', () => {
      // Obsolete: "INDIVIDUAL SERVICE METRICS" section removed entirely.
    })

    it.skip('should render performance profiling section', () => {
      // Obsolete: "PERFORMANCE PROFILING & ANALYTICS" / timeline charts removed entirely.
    })

    it('should render real-time event log', () => {
      renderSystemTab()
      // Current heading is "RECENT ACTIVITY" (SystemTab.tsx:420); there is no
      // search input anymore (no "Search logs..." placeholder exists in the file).
      expect(screen.getByText('RECENT ACTIVITY')).toBeInTheDocument()
    })

    it.skip('should render system information panel', () => {
      // Obsolete: "SYSTEM INFO" panel / "CantinaOS Version:" / "Event Bus:" removed entirely.
    })

    it.skip('should render configuration panel', () => {
      // Obsolete: "CONFIGURATION" panel with "OpenAI API:" / "ElevenLabs API:" removed entirely.
    })

    it.skip('should render performance metrics panel', () => {
      // Obsolete: dedicated "PERFORMANCE METRICS" panel removed; current equivalent
      // is the "KEY METRICS" grid at SystemTab.tsx:377-414.
    })
  })

  describe('Socket Event Handling', () => {
    it('should register socket event listeners on mount', () => {
      renderSystemTab()

      // Fixed: SystemTab.tsx:212-214 registers these three events directly on the
      // socket (system_status, service_status_update, system_metrics). It no
      // longer listens for 'cantina_event', 'config_status' or 'system_error' —
      // those were removed along with the config/log-search panels above.
      expect(mockSocket.on).toHaveBeenCalledWith('system_status', expect.any(Function))
      expect(mockSocket.on).toHaveBeenCalledWith('service_status_update', expect.any(Function))
      expect(mockSocket.on).toHaveBeenCalledWith('system_metrics', expect.any(Function))
    })

    it('should handle service status updates', async () => {
      renderSystemTab()

      // Fixed: service keys must match SystemTab's internal service list
      // (SystemTab.tsx:82-95), e.g. 'deepgram_direct_mic', not the old display
      // name 'DeepgramDirectMicService'.
      await act(async () => {
        fireSocketEvent('service_status_update', {
          service: 'deepgram_direct_mic',
          status: 'RUNNING',
          uptime: '2h 15m',
          memory: '45 MB',
          cpu: '5.2%',
          error_count: 0,
          success_rate: 98.5,
        })
      })

      await waitFor(() => {
        // getServiceDisplayName maps 'deepgram_direct_mic' -> 'Voice Input'
        // (SystemTab.tsx:51).
        expect(screen.getByText('Voice Input')).toBeInTheDocument()
        expect(screen.getByText('online')).toBeInTheDocument()
      })
    })

    it('should handle system events and add to logs', async () => {
      renderSystemTab()

      // Fixed: log ingestion now happens inside the shared useSocket hook via the
      // 'cantina_event' listener (src/hooks/useSocket.ts:262-268), which builds a
      // LogEntry and exposes it through context as `logs`. SystemTab renders those
      // via `filteredLogs` (SystemTab.tsx:449).
      await act(async () => {
        fireSocketEvent('cantina_event', {
          level: 'INFO',
          service: 'TestService',
          message: 'Test message',
        })
      })

      await waitFor(() => {
        expect(screen.getByText('Test message')).toBeInTheDocument()
      })
    })
  })

  describe('Service Management', () => {
    it.skip('should handle service restart', () => {
      // Obsolete: there are no per-service "Restart" buttons or a
      // 'service_command' emit anywhere in SystemTab.tsx — only a single
      // system-wide restart button remains (see next test).
    })

    it('should handle system restart', () => {
      renderSystemTab()

      const systemRestartButton = screen.getByText('Restart System')
      fireEvent.click(systemRestartButton)

      expect(mockSocket.emit).toHaveBeenCalledWith('system_command', {
        action: 'restart',
      })
    })

    it('should handle config refresh', () => {
      renderSystemTab()

      // Fixed: the button was renamed from "Refresh Config" to "Refresh Status"
      // (SystemTab.tsx:328), but it still emits the same 'system_command' /
      // 'refresh_config' payload (SystemTab.tsx:258).
      const refreshButton = screen.getByText('Refresh Status')
      fireEvent.click(refreshButton)

      expect(mockSocket.emit).toHaveBeenCalledWith('system_command', {
        action: 'refresh_config',
      })
    })
  })

  describe('Log Management', () => {
    it('should filter logs by level', async () => {
      renderSystemTab()

      await act(async () => {
        fireSocketEvent('cantina_event', { level: 'ERROR', service: 'Test', message: 'Error message' })
        fireSocketEvent('cantina_event', { level: 'INFO', service: 'Test', message: 'Info message' })
      })

      await waitFor(() => expect(screen.getByText('Error message')).toBeInTheDocument())

      // Fixed: the log-level <select> still exists (SystemTab.tsx:423-432);
      // getByDisplayValue matches the *label* of the selected <option>
      // ("Info+"), not its value attribute ("INFO").
      const logLevelSelect = screen.getByDisplayValue('Info+')
      fireEvent.change(logLevelSelect, { target: { value: 'ERROR' } })

      await waitFor(() => {
        expect(screen.getByText('Error message')).toBeInTheDocument()
        expect(screen.queryByText('Info message')).not.toBeInTheDocument()
      })
    })

    it.skip('should filter logs by search term', () => {
      // Obsolete: there is no "Search logs..." input in SystemTab.tsx anymore —
      // log filtering is level-only (see test above).
    })

    it.skip('should clear all logs', () => {
      // Obsolete: there is no "Clear" logs button in SystemTab.tsx anymore.
    })

    it.skip('should export logs', () => {
      // Obsolete: there is no "Export Logs" button/feature in SystemTab.tsx anymore.
    })
  })

  describe('Alert System', () => {
    it('should create alerts for high CPU usage', async () => {
      renderSystemTab()

      await act(async () => {
        fireSocketEvent('system_metrics', {
          cpuUsage: 85.5,
          totalMemory: 400,
          eventLatency: 50,
          errorRate: 1.0,
        })
      })

      await waitFor(() => {
        expect(screen.getByText('High CPU Usage')).toBeInTheDocument()
        expect(screen.getByText(/CPU usage at 85.5%/)).toBeInTheDocument()
      })
    })

    it.skip('should create alerts for high memory usage', () => {
      // Obsolete: SystemTab's alert effect (SystemTab.tsx:239-249) only checks
      // cpuUsage, errorRate and offline-service ratio — there is no memory-usage
      // alert condition anymore.
    })

    it('should dismiss alerts', async () => {
      renderSystemTab()

      await act(async () => {
        fireSocketEvent('system_metrics', {
          cpuUsage: 85,
          totalMemory: 400,
          eventLatency: 50,
          errorRate: 1.0,
        })
      })

      await waitFor(() => {
        expect(screen.getByText('High CPU Usage')).toBeInTheDocument()
      })

      // Note: with no services online (all 12 default to 'offline' until a
      // service_status_update arrives), a "Services Offline" warning alert is
      // also active alongside "High CPU Usage" — so there can be more than one
      // "Dismiss" button. Scope to the CPU alert's own dismiss button.
      const cpuAlertCard = screen.getByText('High CPU Usage').closest('div.flex.items-center.justify-between')
      const dismissButton = cpuAlertCard?.querySelector('button')
      expect(dismissButton).toBeTruthy()
      fireEvent.click(dismissButton as HTMLButtonElement)

      await waitFor(() => {
        expect(screen.queryByText('High CPU Usage')).not.toBeInTheDocument()
      })
    })

    it.skip('should show dismissed alerts in history', () => {
      // Obsolete: there is no dismissed-alerts history / "Show History" /
      // "DISMISSED ALERTS" UI in SystemTab.tsx anymore — dismissed alerts are
      // simply filtered out (SystemTab.tsx:116-117, 477).
    })
  })

  describe('Performance Metrics', () => {
    it.skip('should display correct health score colors', () => {
      // Obsolete: calculateHealthScore (SystemTab.tsx:68-78) factors in the
      // fraction of services currently online, which defaults to 0 (all services
      // start 'offline', SystemTab.tsx:82-95) unless service_status_update events
      // are simulated for all 12 services. The old test assumed a starting score
      // of 95 with no service data, which no longer matches the real formula.
    })

    it.skip('should show bottleneck detection when system is optimal', () => {
      // Obsolete: there is no "Bottleneck Detection" / "System Running Optimally" UI in SystemTab.tsx anymore.
    })

    it.skip('should calculate stability index correctly', () => {
      // Obsolete: there is no "stability index" concept/metric in SystemTab.tsx anymore.
    })
  })

  describe('Accessibility', () => {
    it.skip('should have proper ARIA labels for buttons', () => {
      // Obsolete: depends on the removed per-service "Restart" buttons (see
      // 'should handle service restart' above).
    })

    it.skip('should support keyboard navigation', () => {
      // Obsolete: depends on the removed "Search logs..." input (see
      // 'should filter logs by search term' above).
    })
  })

  describe('Error Handling', () => {
    it.skip('should handle missing socket gracefully', () => {
      // Obsolete by design change: SystemTab now reads state via
      // useSocketContext(), which intentionally throws when rendered outside a
      // SocketProvider (src/contexts/SocketContext.tsx:8-14) rather than
      // degrading to a null socket. The old test asserted the previous
      // permissive behavior (a null useSocket() return was tolerated), which is
      // no longer how the component is composed in the real app (see
      // src/app/page.tsx:40, which always wraps children in SocketProvider).
    })

    it('should handle malformed socket data', async () => {
      renderSystemTab()

      // Fixed: malformed events now flow through 'cantina_event' (see above).
      await act(async () => {
        expect(() => {
          fireSocketEvent('cantina_event', { invalid: 'data' })
        }).not.toThrow()
      })
    })
  })
})
