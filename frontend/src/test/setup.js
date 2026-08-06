import { vi } from 'vitest'

class ResizeObserverMock {
  observe() {}
  unobserve() {}
  disconnect() {}
}

global.ResizeObserver = ResizeObserverMock
window.matchMedia = window.matchMedia || (() => ({
  matches: false,
  addEventListener: vi.fn(),
  removeEventListener: vi.fn(),
}))
