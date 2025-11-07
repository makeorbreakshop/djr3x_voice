'use client'

import { useEffect, useRef, useState } from 'react'

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
  const animationFrameId = useRef<number | null>(null)
  const audioContext = useRef<AudioContext | null>(null)
  const analyser = useRef<AnalyserNode | null>(null)
  const source = useRef<MediaStreamAudioSourceNode | null>(null)
  const stream = useRef<MediaStream | null>(null)
  const [isBrowserReady, setIsBrowserReady] = useState(false)
  
  // Ensure we only initialize audio in browser environment
  useEffect(() => {
    setIsBrowserReady(typeof window !== 'undefined')
  }, [])

  useEffect(() => {
    const setupAudio = async () => {
      try {
        if (isActive) {
          // 1. Get user media
          stream.current = await navigator.mediaDevices.getUserMedia({
            audio: {
              echoCancellation: false,
              noiseSuppression: false,
              autoGainControl: false,
            },
          });

          // 2. Create AudioContext and nodes
          const context = new (window.AudioContext || (window as any).webkitAudioContext)();
          audioContext.current = context;
          
          analyser.current = context.createAnalyser();
          analyser.current.fftSize = 256;
          analyser.current.smoothingTimeConstant = 0.8;

          source.current = context.createMediaStreamSource(stream.current);
          source.current.connect(analyser.current);

          // 3. Start rendering
          renderSpectrum();
        }
      } catch (error) {
        console.error("Error initializing audio for spectrum:", error);
      }
    };

    const renderSpectrum = () => {
      const canvas = canvasRef.current;
      const analyserNode = analyser.current;
      if (!canvas || !analyserNode) return;

      const ctx = canvas.getContext('2d');
      if (!ctx) return;

      const bufferLength = analyserNode.frequencyBinCount;
      const dataArray = new Uint8Array(bufferLength);
      analyserNode.getByteFrequencyData(dataArray);

      ctx.fillStyle = 'rgba(10, 14, 22, 0.8)'; // sw-dark-900 with transparency
      ctx.fillRect(0, 0, width, height);

      const barWidth = width / bufferLength;
      let x = 0;

      for (let i = 0; i < bufferLength; i++) {
        const barHeight = (dataArray[i] / 255) * height;
        const gradient = ctx.createLinearGradient(0, height, 0, height - barHeight);
        gradient.addColorStop(0, '#0087ff');
        gradient.addColorStop(0.6, '#006bcc');
        gradient.addColorStop(1, '#00aaff');
        ctx.fillStyle = gradient;
        ctx.fillRect(x, height - barHeight, barWidth - 1, barHeight);
        x += barWidth;
      }

      animationFrameId.current = requestAnimationFrame(renderSpectrum);
    };

    const cleanup = () => {
      // Stop the animation frame
      if (animationFrameId.current) {
        cancelAnimationFrame(animationFrameId.current);
      }
      // Stop the media stream tracks
      stream.current?.getTracks().forEach(track => track.stop());
      // Disconnect the source
      source.current?.disconnect();
      // Close the audio context
      if (audioContext.current && audioContext.current.state !== 'closed') {
        audioContext.current.close();
      }
      audioContext.current = null;
    };

    if (isBrowserReady && isActive) {
      setupAudio();
    } else {
      cleanup();
    }

    // This is the cleanup function that will be called when the component unmounts OR when isActive becomes false.
    return cleanup;
  }, [isBrowserReady, isActive, width, height]); // Rerun effect if browser ready, isActive, width, or height changes

  return (
    <div className={`relative ${className}`}>
      <canvas
        ref={canvasRef}
        width={width}
        height={height}
        className="w-full h-full bg-sw-dark-700/50 rounded-lg border border-sw-blue-600/20"
      />
      {!isActive && (
         <div className="absolute inset-0 flex items-center justify-center">
           <div className="text-sw-blue-300/50 text-sm">
             Voice activity display inactive.
           </div>
         </div>
       )}
    </div>
  );
}