'use client'

import { TabType } from '@/app/page'

interface TabNavigationProps {
  activeTab: TabType
  setActiveTab: (tab: TabType) => void
}

const tabs = [
  { id: 'monitor' as TabType, label: 'MONITOR', description: 'System Overview' },
  { id: 'voice' as TabType, label: 'VOICE', description: 'Audio Processing' },
  { id: 'music' as TabType, label: 'MUSIC', description: 'Playback Control' },
  { id: 'dj' as TabType, label: 'DJ MODE', description: 'Auto DJ' },
  { id: 'system' as TabType, label: 'SYSTEM', description: 'Debug & Logs' },
]

export default function TabNavigation({ activeTab, setActiveTab }: TabNavigationProps) {
  return (
    <nav className="border-b border-sw-blue-600/30">
      <div className="flex space-x-8">
        {tabs.map((tab) => (
          <button
            key={tab.id}
            onClick={() => setActiveTab(tab.id)}
            className={`sw-tab ${activeTab === tab.id ? 'active' : ''} group`}
          >
            <div className="flex flex-col items-center">
              <span className="text-sm font-semibold">{tab.label}</span>
              <span className="text-xs opacity-60 group-hover:opacity-80 transition-opacity">
                {tab.description}
              </span>
            </div>
          </button>
        ))}
      </div>
    </nav>
  )
}