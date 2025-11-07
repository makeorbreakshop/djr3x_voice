'use client'

import React, { createContext, useContext, ReactNode } from 'react'
import { useSocket } from '@/hooks/useSocket'

const SocketContext = createContext<ReturnType<typeof useSocket> | null>(null)

export const useSocketContext = () => {
  const context = useContext(SocketContext)
  if (!context) {
    throw new Error('useSocketContext must be used within a SocketProvider')
  }
  return context
}

interface SocketProviderProps {
  children: ReactNode
}

export const SocketProvider: React.FC<SocketProviderProps> = ({ children }) => {
  const socket = useSocket()

  return (
    <SocketContext.Provider value={socket}>
      {children}
    </SocketContext.Provider>
  )
}