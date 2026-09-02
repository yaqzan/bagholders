/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    "./src/**/*.{js,jsx,ts,tsx}",
  ],
  theme: {
    extend: {
      fontFamily: {
        sans: [
          'Inter var',
          'Inter',
          'ui-sans-serif',
          'system-ui',
          '-apple-system',
          'Segoe UI',
          'Roboto',
          'sans-serif',
        ],
        mono: [
          'JetBrains Mono var',
          'JetBrains Mono',
          'ui-monospace',
          'SFMono-Regular',
          'Menlo',
          'Consolas',
          'monospace',
        ],
      },
      colors: {
        /* New near-black ramp — warm neutral, replaces slate-950 */
        'trading-dark': {
          50:  '#F4F4F5',
          100: '#E4E4E7',
          200: '#C8C8CD',
          300: '#A1A1AA',
          400: '#71717A',
          500: '#52525B',
          600: '#3F3F46',
          700: '#27272A',
          800: '#1C1C20',
          900: '#111113',
          950: '#0A0A0B',
        },
        /* Desaturated sage — primary positive / bullish / brand */
        'trading-green': {
          50:  '#F1F6EF',
          100: '#DFEADB',
          200: '#C4D7BE',
          300: '#A7C4A0',   /* brand accent */
          400: '#8FB286',   /* primary positive tone (replaces bright emerald) */
          500: '#6E9A64',
          600: '#557849',
          700: '#425D38',
          800: '#324429',
          900: '#1F2B1A',
        },
        /* Dusty coral — primary negative / bearish */
        'trading-red': {
          50:  '#FBF1F1',
          100: '#F4DEDE',
          200: '#E8BFBF',
          300: '#DA9E9E',
          400: '#C98484',   /* primary negative tone (replaces bright red-400) */
          500: '#B26A6A',
          600: '#955353',
          700: '#754040',
          800: '#553030',
          900: '#341E1E',
        },
        /* Muted sky — informational only, rarely used */
        'trading-blue': {
          50:  '#EFF4F8',
          100: '#D9E4EC',
          200: '#B8CCDA',
          300: '#95B2C5',
          400: '#7DA4C4',
          500: '#5E85A8',
          600: '#466A8A',
          700: '#38546D',
          800: '#2A3E52',
          900: '#1E2C3A',
        },
        /* Muted gold — flagged / warning only */
        amber: {
          50:  '#F9F2E2',
          100: '#EFE0BC',
          200: '#E3CA8E',
          300: '#D6B362',
          400: '#C69C56',
          500: '#A7823E',
          600: '#84672F',
          700: '#645024',
        },
      },
      letterSpacing: {
        'tightest': '-0.04em',
      },
      animation: {
        'reveal': 'reveal 180ms cubic-bezier(0.16, 1, 0.3, 1) both',
        'snap': 'snap 140ms ease-out both',
      },
      keyframes: {
        reveal: {
          '0%': { opacity: '0', transform: 'translateY(4px)' },
          '100%': { opacity: '1', transform: 'translateY(0)' },
        },
        snap: {
          '0%': { opacity: '0' },
          '100%': { opacity: '1' },
        },
      },
    },
  },
  plugins: [],
};
