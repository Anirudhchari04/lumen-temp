/** @type {import('tailwindcss').Config} */
export default {
  // Drive `dark:` variants off the `html.dark` class (toggled by the app's theme
  // switch) instead of the OS `prefers-color-scheme`. Without this, dark: utilities
  // followed the OS setting and went out of sync with the in-app toggle — e.g. an
  // input's `dark:text-white` rendered white text in the app's LIGHT mode whenever
  // the OS was in dark mode, making typed text invisible.
  darkMode: 'class',
  content: ['./index.html', './src/**/*.{js,jsx}'],
  theme: {
    extend: {
      colors: {
        beige: {
          50:  '#faf8f4',
          100: '#f3efe6',
          200: '#e8e0d0',
          300: '#d9cfbc',
          400: '#c7baa1',
        },
        ink: {
          DEFAULT: '#2c2820',
          soft:    '#6b6152',
          muted:   '#a09080',
        },
        amber: {
          DEFAULT: '#c8762a',
          light:   '#fdf0e0',
          border:  '#f0c070',
        },
        math:  { bg: '#f0ebe0', dot: '#c8762a' },
        cs:    { bg: '#e8eaf6', dot: '#5c6bc0' },
        cal:   { bg: '#e8f4ee', dot: '#4caf50' },
      },
      borderRadius: {
        card: '16px',
        inner: '10px',
        pill: '20px',
      },
      fontFamily: {
        sans: ['system-ui', '-apple-system', 'Segoe UI', 'Roboto', 'sans-serif'],
      },
      fontSize: {
        '2xs': '11px',
      },
      keyframes: {
        pulseDot: {
          '0%,100%': { opacity: 0.3 },
          '50%':     { opacity: 1 },
        },
        slideUp: {
          '0%':   { transform: 'translateY(100%)' },
          '100%': { transform: 'translateY(0)' },
        },
      },
      animation: {
        'pulse-dot': 'pulseDot 1.2s ease-in-out infinite',
        'slide-up':  'slideUp 0.25s ease-out',
      },
    },
  },
  plugins: [],
}
