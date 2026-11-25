const paths = {
  chevron: 'M3.85 6.25 8 10.4 12.15 6.25',
  check: 'm3.5 8 3 3 6-6',
  clear: 'm5 5 6 6M11 5l-6 6',
  external: 'M9 3.5h3.5V7M12.5 3.5 7 9m.5-4.5h-4v8h8v-4',
}

export default function SelectionIcon({ kind, className }) {
  const angular = kind === 'chevron'

  return (
    <svg className={className} viewBox="0 0 16 16" fill="none" aria-hidden="true" focusable="false">
      <path d={paths[kind]} stroke="currentColor" strokeWidth={angular ? 1.55 : 1.6}
        strokeLinecap={angular ? 'square' : 'round'} strokeLinejoin={angular ? 'miter' : 'round'} />
    </svg>
  )
}
