import type { Config } from "tailwindcss";

const config: Config = {
    darkMode: ["class"],
    content: [
    "./src/pages/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/components/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/app/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
  	extend: {
  		fontFamily: {
  			display: ['var(--font-jakarta)', 'system-ui', 'sans-serif'],
  			body: ['var(--font-dm)', 'system-ui', 'sans-serif'],
  			mono: ['var(--font-jetbrains)', 'monospace'],
  			// Revamp (v2 pages): Manrope sans + DM Serif Display logo glyph
  			sans: ['var(--font-manrope)', 'system-ui', 'sans-serif'],
  			serif: ['var(--font-serif)', 'Georgia', 'serif'],
  		},
  		colors: {
  			background: 'hsl(var(--background))',
  			foreground: 'hsl(var(--foreground))',
  			card: {
  				DEFAULT: 'hsl(var(--card))',
  				foreground: 'hsl(var(--card-foreground))'
  			},
  			popover: {
  				DEFAULT: 'hsl(var(--popover))',
  				foreground: 'hsl(var(--popover-foreground))'
  			},
  			primary: {
  				DEFAULT: 'hsl(var(--primary))',
  				foreground: 'hsl(var(--primary-foreground))'
  			},
  			secondary: {
  				DEFAULT: 'hsl(var(--secondary))',
  				foreground: 'hsl(var(--secondary-foreground))'
  			},
  			muted: {
  				DEFAULT: 'hsl(var(--muted))',
  				foreground: 'hsl(var(--muted-foreground))'
  			},
  			accent: {
  				DEFAULT: 'hsl(var(--accent))',
  				foreground: 'hsl(var(--accent-foreground))'
  			},
  			destructive: {
  				DEFAULT: 'hsl(var(--destructive))',
  				foreground: 'hsl(var(--destructive-foreground))'
  			},
  			border: 'hsl(var(--border))',
  			input: 'hsl(var(--input))',
  			ring: 'hsl(var(--ring))',
  			chart: {
  				'1': 'hsl(var(--chart-1))',
  				'2': 'hsl(var(--chart-2))',
  				'3': 'hsl(var(--chart-3))',
  				'4': 'hsl(var(--chart-4))',
  				'5': 'hsl(var(--chart-5))'
  			},
  			/* Memoir semantic colors */
  			sidebar: {
  				DEFAULT: 'hsl(var(--sidebar))',
  				foreground: 'hsl(var(--sidebar-foreground))',
  				border: 'hsl(var(--sidebar-border))',
  				active: 'hsl(var(--sidebar-active))',
  			},
  			memoir: {
  				bg: '#F8F7F4',
  				surface: '#FFFFFF',
  				'sidebar': '#F0EEE9',
  				'border': '#E8E4DC',
  				'indigo': '#4F46E5',
  				'indigo-hover': '#4338CA',
  				'indigo-light': '#EEF2FF',
  				'amber': '#F59E0B',
  				'amber-light': '#FEF3C7',
  				'success': '#10B981',
  				'error': '#EF4444',
  				'text': '#1A1A2E',
  				'text-secondary': '#6B7280',
  				'text-muted': '#9CA3AF',
  			},
  			/* Revamp v2 palette — used by new pages (home v2, meetings v3, extension popup).
  			   Existing memoir-* tokens stay so unconverted pages render unchanged.
  			   Source mockups in ~/Downloads/memoir-*.jsx. */
  			brand: {
  				DEFAULT: '#5C5CE0',
  				light: '#EDEDFC',
  				dark: '#4848B8',
  			},
  			'brand-orange': {
  				DEFAULT: '#E8683A',
  				light: '#FFF3EE',
  			},
  			'brand-green': {
  				DEFAULT: '#00B87C',
  				light: '#E6F9F1',
  				dark: '#009E6A',
  			},
  			'brand-amber': {
  				DEFAULT: '#F5A623',
  				light: '#FFF8E8',
  			},
  			'brand-red': {
  				DEFAULT: '#E5484D',
  				light: '#FEECEE',
  			},
  			'brand-blue': {
  				DEFAULT: '#3B82F6',
  				light: '#EFF6FF',
  			},
  			ink: {
  				DEFAULT: '#1B1B1F',
  				secondary: '#7C7C8A',
  				light: '#A0A0AE',
  			},
  			surface: {
  				DEFAULT: '#FFFFFF',
  				soft: '#F8F8FC',
  				border: '#EBEBF0',
  				'border-hover': '#D4D4DC',
  			},
  		},
  		borderRadius: {
  			lg: 'var(--radius)',
  			md: 'calc(var(--radius) - 2px)',
  			sm: 'calc(var(--radius) - 4px)',
  			card: '12px',
  			button: '8px',
  			pill: '999px',
  		},
  		boxShadow: {
  			'card': '0 1px 3px rgba(0,0,0,0.06), 0 1px 2px rgba(0,0,0,0.04)',
  			'elevated': '0 4px 12px rgba(0,0,0,0.08), 0 2px 4px rgba(0,0,0,0.04)',
  			'modal': '0 20px 60px rgba(0,0,0,0.15)',
  		},
  		keyframes: {
  			'loading-bar': {
  				'0%': { width: '0%', marginLeft: '0%' },
  				'30%': { width: '40%', marginLeft: '0%' },
  				'60%': { width: '30%', marginLeft: '50%' },
  				'100%': { width: '0%', marginLeft: '100%' },
  			},
  			'pulse-ring': {
  				'0%, 100%': { transform: 'scale(0.9)', opacity: '1' },
  				'50%': { transform: 'scale(1.15)', opacity: '0.4' },
  			},
  			'fade-in': {
  				'0%': { opacity: '0', transform: 'translateY(6px)' },
  				'100%': { opacity: '1', transform: 'translateY(0)' },
  			},
  			'timer-tick': {
  				'0%, 100%': { opacity: '1' },
  				'50%': { opacity: '0.5' },
  			},
  		},
  		animation: {
  			'loading-bar': 'loading-bar 1.2s ease-in-out infinite',
  			'pulse-ring': 'pulse-ring 1.5s ease-in-out infinite',
  			'fade-in': 'fade-in 0.3s ease',
  			'timer-tick': 'timer-tick 2s ease infinite',
  		},
  		transitionTimingFunction: {
  			'spring': 'cubic-bezier(0.34, 1.56, 0.64, 1)',
  		},
  		width: {
  			'sidebar': '240px',
  			'sidebar-collapsed': '56px',
  		},
  		height: {
  			'topbar': '56px',
  		},
  	}
  },
  plugins: [require("tailwindcss-animate")],
};
export default config;
