'use client'

import { useEffect, useRef, useState, useCallback } from 'react'

interface AudioSpectrumProps {
  width?: number
  height?: number
  className?: string
  isActive?: boolean
}

export default function AudioSpectrum({ 
  width = 400, 
  height = 128, 
  className = "",
  isActive = false 
}: AudioSpectrumProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const audioContextRef = useRef<AudioContext | null>(null)
  const analyserRef = useRef<AnalyserNode | null>(null)
  const sourceRef = useRef<MediaStreamAudioSourceNode | null>(null)
  const streamRef = useRef<MediaStream | null>(null)
  const animationRef = useRef<number>(0)
  
  const [isInitialized, setIsInitialized] = useState(false)
  const [permissionGranted, setPermissionGranted] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Initialize audio context and get microphone access
  const initializeAudio = useCallback(async () => {
    try {
      // Request microphone access
      const stream = await navigator.mediaDevices.getUserMedia({ 
        audio: {
          echoCancellation: false,
          noiseSuppression: false,
          autoGainControl: false
        } 
      })
      
      streamRef.current = stream
      setPermissionGranted(true)

      // Create audio context
      const audioContext = new (window.AudioContext || (window as any).webkitAudioContext)()
      audioContextRef.current = audioContext

      // Create analyser node
      const analyser = audioContext.createAnalyser()
      analyser.fftSize = 256 // 128 frequency bins
      analyser.smoothingTimeConstant = 0.8
      analyserRef.current = analyser

      // Connect microphone to analyser
      const source = audioContext.createMediaStreamSource(stream)
      sourceRef.current = source
      source.connect(analyser)

      setIsInitialized(true)
      setError(null)
    } catch (err) {
      setError(`Microphone access denied: ${err}`)
      setPermissionGranted(false)
    }
  }, [])

  // Animation loop for rendering spectrum
  const renderSpectrum = useCallback(() => {
    const canvas = canvasRef.current
    const analyser = analyserRef.current
    
    if (!canvas || !analyser) return

    const ctx = canvas.getContext('2d')
    if (!ctx) return

    // Get frequency data
    const bufferLength = analyser.frequencyBinCount
    const dataArray = new Uint8Array(bufferLength)
    analyser.getByteFrequencyData(dataArray)

    // Clear canvas
    ctx.fillStyle = 'rgba(10, 14, 22, 0.8)' // sw-dark-900 with transparency
    ctx.fillRect(0, 0, width, height)

    // Draw spectrum bars
    const barWidth = width / bufferLength
    let x = 0

    for (let i = 0; i < bufferLength; i++) {
      const barHeight = (dataArray[i] / 255) * height

      // Create gradient for bars
      const gradient = ctx.createLinearGradient(0, height, 0, height - barHeight)
      gradient.addColorStop(0, '#0087ff') // sw-blue-500
      gradient.addColorStop(0.6, '#006bcc') // sw-blue-600
      gradient.addColorStop(1, '#00aaff') // lighter blue

      ctx.fillStyle = gradient
      ctx.fillRect(x, height - barHeight, barWidth - 1, barHeight)

      x += barWidth
    }

    // Add glow effect
    ctx.shadowColor = '#0087ff'
    ctx.shadowBlur = 10
    
    // Continue animation
    if (isActive && isInitialized) {
      animationRef.current = requestAnimationFrame(renderSpectrum)
    }
  }, [width, height, isActive, isInitialized])

  // Start/stop spectrum rendering
  useEffect(() => {
    if (isActive && isInitialized && permissionGranted) {
      renderSpectrum()
    } else {
      if (animationRef.current) {
        cancelAnimationFrame(animationRef.current)
      }
    }

    return () => {
      if (animationRef.current) {
        cancelAnimationFrame(animationRef.current)
      }
    }
  }, [isActive, isInitialized, permissionGranted, renderSpectrum])

  // Initialize on component mount
  useEffect(() => {
    if (isActive && !isInitialized && !error) {
      initializeAudio()
    }

    return () => {
      // Cleanup
      if (animationRef.current) {
        cancelAnimationFrame(animationRef.current)
      }
      if (streamRef.current) {
        streamRef.current.getTracks().forEach(track => track.stop())
      }
      if (audioContextRef.current) {
        audioContextRef.current.close()
      }
    }
  }, [isActive, isInitialized, error, initializeAudio])

  return (
    <div className={`relative ${className}`}>
      <canvas
        ref={canvasRef}
        width={width}
        height={height}
        className="w-full h-full bg-sw-dark-700/50 rounded-lg border border-sw-blue-600/20"
      />
      
      {!permissionGranted && !error && (
        <div className="absolute inset-0 flex items-center justify-center text-center">
          <div>
            <div className="text-sw-blue-300/70 text-sm mb-2">
              Audio Spectrum Visualization
            </div>
            <button
              onClick={initializeAudio}
              className="px-3 py-1 text-xs bg-sw-blue-600 hover:bg-sw-blue-500 text-white rounded transition-colors"
            >
              Enable Microphone
            </button>
          </div>
        </div>
      )}

      {error && (
        <div className="absolute inset-0 flex items-center justify-center text-center">
          <div className="text-sw-red text-sm">
            {error}
          </div>
        </div>
      )}

      {!isActive && permissionGranted && (
        <div className="absolute inset-0 flex items-center justify-center">
          <div className="text-sw-blue-300/50 text-sm">
            Spectrum visualization inactive
          </div>
        </div>
      )}
    </div>
  )
}