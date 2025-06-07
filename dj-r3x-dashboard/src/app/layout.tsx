import type { Metadata } from 'next'
import './globals.css'

export const metadata: Metadata = {
  title: 'DJ R3X Monitoring Dashboard',
  description: 'Real-time monitoring and control interface for DJ R3X Voice Assistant',
}

export default function RootLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  )
}