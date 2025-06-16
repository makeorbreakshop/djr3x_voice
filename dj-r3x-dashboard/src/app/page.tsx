'use client'

import { useState } from 'react'
import { SocketProvider } from '@/contexts/SocketContext'
import Header from '@/components/Header'
import TabNavigation from '@/components/TabNavigation'
import GlobalActivityBar from '@/components/GlobalActivityBar'
import MonitorTab from '@/components/tabs/MonitorTab'
import VoiceTab from '@/components/tabs/VoiceTab'
import MusicTab from '@/components/tabs/MusicTab'
import DJTab from '@/components/tabs/DJTab'
import ShowTab from '@/components/tabs/ShowTab'
import SystemTab from '@/components/tabs/SystemTab'

export type TabType = 'monitor' | 'voice' | 'music' | 'dj' | 'show' | 'system'

export default function Home() {
  const [activeTab, setActiveTab] = useState<TabType>('monitor')

  const renderActiveTab = () => {
    switch (activeTab) {
      case 'monitor':
        return <MonitorTab />
      case 'voice':
        return <VoiceTab />
      case 'music':
        return <MusicTab />
      case 'dj':
        return <DJTab />
      case 'show':
        return <ShowTab />
      case 'system':
        return <SystemTab />
      default:
        return <MonitorTab />
    }
  }

  return (
    <SocketProvider>
      <div className="min-h-screen bg-sw-dark-900 pb-16">
        <header className="border-b border-sw-blue-600/30 bg-sw-dark-800/50 backdrop-blur-sm">
          <div className="container mx-auto px-4 py-2">
            <div className="flex items-center justify-between">
              <Header />
              <TabNavigation activeTab={activeTab} setActiveTab={setActiveTab} />
            </div>
          </div>
        </header>
        <div className="container mx-auto px-4 py-6">
          <div className="mt-6">
            {renderActiveTab()}
          </div>
        </div>
      </div>
      <GlobalActivityBar />
    </SocketProvider>
  )
}