import type { Config } from 'tailwindcss'

const config: Config = {
  content: [
    './index.html',
    './src/**/*.{ts,tsx}',
  ],
  theme: {
    extend: {
      colors: {
        brand: {
          bg: '#0D0F14',
          card: '#141720',
          surface: '#1A1F2E',
          border: '#1E2330',
          green: '#00E676',
          'green-dim': 'rgba(0,230,118,0.12)',
          red: '#FF5252',
          'red-dim': 'rgba(255,82,82,0.12)',
          blue: '#2979FF',
          'blue-dim': 'rgba(41,121,255,0.12)',
          yellow: '#FFD740',
          'yellow-dim': 'rgba(255,215,64,0.12)',
          muted: '#5A6478',
          subtle: '#2A3045',
        },
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', 'sans-serif'],
        mono: ['"JetBrains Mono"', '"Fira Code"', 'monospace'],
      },
      borderRadius: {
        card: '12px',
        pill: '20px',
      },
      boxShadow: {
        card: '0 1px 3px rgba(0,0,0,0.4), 0 0 0 1px rgba(255,255,255,0.03)',
        'card-hover': '0 4px 16px rgba(0,0,0,0.5), 0 0 0 1px rgba(255,255,255,0.05)',
        'green-glow': '0 0 12px rgba(0,230,118,0.35)',
        'red-glow': '0 0 12px rgba(255,82,82,0.35)',
        'blue-glow': '0 0 12px rgba(41,121,255,0.35)',
      },
      animation: {
        'pulse-dot': 'pulse-dot 2s ease-in-out infinite',
        shimmer: 'shimmer 1.8s linear infinite',
        'fade-up': 'fade-up 0.2s ease-out',
        'slide-in': 'slide-in 0.15s ease-out',
        'flash-green': 'flash-green 1s ease-out forwards',
        'flash-red': 'flash-red 1s ease-out forwards',
      },
      keyframes: {
        'pulse-dot': {
          '0%, 100%': { opacity: '1', transform: 'scale(1)' },
          '50%': { opacity: '0.6', transform: 'scale(0.85)' },
        },
        shimmer: {
          '0%': { backgroundPosition: '-500px 0' },
          '100%': { backgroundPosition: '500px 0' },
        },
        'fade-up': {
          from: { opacity: '0', transform: 'translateY(6px)' },
          to: { opacity: '1', transform: 'translateY(0)' },
        },
        'slide-in': {
          from: { opacity: '0', transform: 'translateX(-6px)' },
          to: { opacity: '1', transform: 'translateX(0)' },
        },
        'flash-green': {
          '0%': { backgroundColor: 'rgba(0,230,118,0.2)' },
          '100%': { backgroundColor: 'transparent' },
        },
        'flash-red': {
          '0%': { backgroundColor: 'rgba(255,82,82,0.2)' },
          '100%': { backgroundColor: 'transparent' },
        },
      },
    },
  },
  plugins: [],
}

export default config
