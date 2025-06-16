'use client'

import { TabType } from '@/app/page'

interface TabNavigationProps {
  activeTab: TabType
  setActiveTab: (tab: TabType) => void
}

const tabs = [
  { id: 'monitor' as TabType, label: 'MONITOR' },
  { id: 'voice' as TabType, label: 'VOICE' },
  { id: 'music' as TabType, label: 'MUSIC' },
  { id: 'dj' as TabType, label: 'DJ MODE' },
  { id: 'show' as TabType, label: 'SHOW' },
  { id: 'system' as TabType, label: 'SYSTEM' },
]

export default function TabNavigation({ activeTab, setActiveTab }: TabNavigationProps) {
  return (
    <nav className="flex space-x-6">
      {tabs.map((tab) => (
        <button
          key={tab.id}
          onClick={() => setActiveTab(tab.id)}
          className={`sw-tab ${activeTab === tab.id ? 'active' : ''} group`}
        >
          <span className="text-sm font-semibold">{tab.label}</span>
        </button>
      ))}
    </nav>
  )
}