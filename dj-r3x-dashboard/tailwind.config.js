/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    './src/pages/**/*.{js,ts,jsx,tsx,mdx}',
    './src/components/**/*.{js,ts,jsx,tsx,mdx}',
    './src/app/**/*.{js,ts,jsx,tsx,mdx}',
  ],
  theme: {
    extend: {
      colors: {
        // Star Wars inspired colors
        'sw-blue': {
          50: '#e6f3ff',
          100: '#cce7ff',
          200: '#99cfff',
          300: '#66b7ff',
          400: '#339fff',
          500: '#0087ff',
          600: '#006bcc',
          700: '#004f99',
          800: '#003366',
          900: '#001733'
        },
        'sw-yellow': '#ffd700',
        'sw-green': '#00ff41',
        'sw-red': '#ff073a',
        'sw-dark': {
          50: '#f6f7f9',
          100: '#eceef2',
          200: '#d4d9e2',
          300: '#aeb8c8',
          400: '#8292a9',
          500: '#61748e',
          600: '#4d5d75',
          700: '#3f4c5f',
          800: '#364050',
          900: '#0a0e16',
          950: '#020306'
        }
      },
      fontFamily: {
        'sw-mono': ['SF Mono', 'Monaco', 'Inconsolata', 'Roboto Mono', 'monospace'],
      },
      backgroundImage: {
        'gradient-radial': 'radial-gradient(var(--tw-gradient-stops))',
        'sw-terminal': 'linear-gradient(135deg, rgba(0,135,255,0.1) 0%, rgba(0,0,0,0.8) 100%)',
      },
      animation: {
        'glow': 'glow 2s ease-in-out infinite alternate',
        'flicker': 'flicker 0.15s infinite linear',
      },
      keyframes: {
        glow: {
          '0%': { 
            'box-shadow': '0 0 5px rgba(0,135,255,0.5), 0 0 10px rgba(0,135,255,0.3), 0 0 15px rgba(0,135,255,0.2)' 
          },
          '100%': { 
            'box-shadow': '0 0 10px rgba(0,135,255,0.8), 0 0 20px rgba(0,135,255,0.5), 0 0 30px rgba(0,135,255,0.3)' 
          },
        },
        flicker: {
          '0%, 19.999%, 22%, 62.999%, 64%, 64.999%, 70%, 100%': {
            opacity: '0.99',
          },
          '20%, 21.999%, 63%, 63.999%, 65%, 69.999%': {
            opacity: '0.4',
          }
        }
      }
    },
  },
  plugins: [],
}