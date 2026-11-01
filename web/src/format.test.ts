import { describe, expect, it } from 'vitest'
import { late } from './format'

describe('late', () => {
  it('shows late minutes with a plus sign', () => {
    expect(late(9)).toBe('+9 min')
  })

  it('says early in words instead of a negative number', () => {
    expect(late(-2)).toBe('2 min early')
  })

  it('calls zero on time', () => {
    expect(late(0)).toBe('on time')
  })

  it('shows a dash when there is no history', () => {
    expect(late(null)).toBe('-')
  })
})
