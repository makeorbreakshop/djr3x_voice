'use client'

import dynamic from 'next/dynamic'

const AudioSpectrum = dynamic(
  () => import('./AudioSpectrum'),
  { ssr: false }
)

export default AudioSpectrum 