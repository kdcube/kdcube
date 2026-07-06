/**
 * Rail / titlebar icons — the same stroke glyphs the website scene host uses
 * for its components, so both hosts read identically.
 */

import React from 'react'

function StrokeSvg({ children }: { children: React.ReactNode }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      {children}
    </svg>
  )
}

export function PinBoardIcon() {
  return (
    <StrokeSvg>
      <line x1="12" y1="17" x2="12" y2="22" />
      <path d="M9 10.8V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v6.8a2 2 0 0 0 .6 1.4L18 14H6l2.4-1.8A2 2 0 0 0 9 10.8z" />
    </StrokeSvg>
  )
}

export function MemoriesIcon() {
  return (
    <StrokeSvg>
      <path d="M12 3a3 3 0 0 0-3 3 3 3 0 0 0-2 5 3 3 0 0 0 2 5 3 3 0 0 0 6 0 3 3 0 0 0 2-5 3 3 0 0 0-2-5 3 3 0 0 0-3-3z" />
      <path d="M12 6v12M9 9h6M8.5 14h7" />
    </StrokeSvg>
  )
}

export function ChatIcon() {
  return (
    <StrokeSvg>
      <path d="M21 11.5a8.4 8.4 0 0 1-9 8 9 9 0 0 1-4-1L3 20l1.5-4.5A8.4 8.4 0 0 1 12 3a8.4 8.4 0 0 1 9 8.5z" />
    </StrokeSvg>
  )
}

export function StatsIcon() {
  return (
    <StrokeSvg>
      <path d="M3 3v18h18M8 16v-5M13 16V8M18 16v-9" />
    </StrokeSvg>
  )
}

export function UsageIcon() {
  return (
    <StrokeSvg>
      <path d="M12 1v22M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6" />
    </StrokeSvg>
  )
}

export function TasksIcon() {
  return (
    <StrokeSvg>
      <path d="M8 6h13M8 12h13M8 18h13" />
      <path d="m3 6 1 1 2-2M3 12l1 1 2-2M3 18l1 1 2-2" />
    </StrokeSvg>
  )
}

export function NewsIcon() {
  return (
    <StrokeSvg>
      <path d="M4 4h13v16H5a2 2 0 0 1-2-2V6M17 8h3v10a2 2 0 0 1-2 2M7 8h6M7 12h6M7 16h4" />
    </StrokeSvg>
  )
}

export function JournalIcon() {
  return (
    <StrokeSvg>
      <path d="M14 3v4a1 1 0 0 0 1 1h4" />
      <path d="M17 21H7a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h7l5 5v11a2 2 0 0 1-2 2z" />
      <path d="M9 13h6M9 17h6" />
    </StrokeSvg>
  )
}

const ICONS: Record<string, () => React.JSX.Element> = {
  pinboard: PinBoardIcon,
  memories: MemoriesIcon,
  chat: ChatIcon,
  stats: StatsIcon,
  usage: UsageIcon,
  usage_card: UsageIcon,
  tasks: TasksIcon,
  task_list: TasksIcon,
  news: NewsIcon,
  journal: JournalIcon,
}

export function componentIcon(alias: string): React.JSX.Element {
  const Icon = ICONS[alias] ?? ChatIcon
  return <Icon />
}
