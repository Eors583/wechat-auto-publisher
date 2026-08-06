import { describe, expect, it } from 'vitest'

import { formatDateTime, plainText, statusLabel } from './format'

describe('formatDateTime', () => {
  it('treats database timestamps without a zone as UTC and displays Shanghai time', () => {
    expect(formatDateTime('2026-08-05 06:21:54')).toBe('2026-08-05 14:21:54')
  })

  it('preserves an explicit timezone before converting', () => {
    expect(formatDateTime('2026-08-05T14:21:54+08:00')).toBe('2026-08-05 14:21:54')
  })
})
describe('plainText', () => {
  it('removes HTML and converts escaped line breaks from AI candidates', () => {
    const value = '<p>第一段</p>\\n\\n## 开放反馈：<strong>第二段</strong>'
    expect(plainText(value)).toBe('第一段\n\n## 开放反馈：第二段')
  })
})

describe('statusLabel', () => {
  it('uses clear Chinese workflow labels', () => {
    expect(statusLabel('candidate_ready')).toBe('候选稿待选择')
    expect(statusLabel('source_kept')).toBe('已保留原文')
  })
})
