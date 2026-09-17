import { expect, afterEach } from 'vitest'
import { cleanup } from '@testing-library/react'
import * as matchers from '@testing-library/jest-dom/matchers'

// Extend Vitest's expect with jest-dom matchers
expect.extend(matchers)

// Clean up after each test case
// The semicolon is load-bearing: without it, automatic semicolon insertion does NOT
// apply (the next statement begins with `(`), so TypeScript parses this as
// `afterEach(cleanup)(global as any)` and fails the build with
// "This expression is not callable. Type 'void' has no call signatures."
afterEach(cleanup);

// Mock IntersectionObserver
(global as any).IntersectionObserver = class IntersectionObserver {
  root = null
  rootMargin = ''
  thresholds = []
  
  constructor() {}
  observe() {
    return null
  }
  disconnect() {
    return null
  }
  unobserve() {
    return null
  }
  takeRecords() {
    return []
  }
}

// Mock ResizeObserver
global.ResizeObserver = class ResizeObserver {
  constructor() {}
  observe() {
    return null
  }
  disconnect() {
    return null
  }
  unobserve() {
    return null
  }
}

// Mock matchMedia
Object.defineProperty(window, 'matchMedia', {
  writable: true,
  value: (query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => {},
  }),
})

// Mock URL.createObjectURL and revokeObjectURL for file downloads
global.URL.createObjectURL = () => 'mocked-url'
global.URL.revokeObjectURL = () => {}

// Mock scrollIntoView
Element.prototype.scrollIntoView = () => {}