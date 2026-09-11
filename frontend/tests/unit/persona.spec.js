import { describe, it, expect, beforeEach } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { usePersonaStore } from '@/stores/persona'

// persona store 持久化契约：刷新保持（localStorage）、清除、已下架人格回落默认
describe('persona store 持久化', () => {
  beforeEach(() => {
    localStorage.clear()
    setActivePinia(createPinia())
  })

  it('test_persona_init_empty_without_storage', () => {
    const store = usePersonaStore()
    expect(store.personaId).toBe('')
  })

  it('test_persona_set_persists_and_survives_refresh', () => {
    const store = usePersonaStore()
    store.setPersona('gentle')
    expect(localStorage.getItem('assistmind:persona')).toBe('gentle')
    // 新 pinia 实例模拟刷新后重新初始化
    setActivePinia(createPinia())
    const fresh = usePersonaStore()
    expect(fresh.personaId).toBe('gentle')
  })

  it('test_persona_clear_removes_storage', () => {
    const store = usePersonaStore()
    store.setPersona('gentle')
    store.setPersona('')
    expect(localStorage.getItem('assistmind:persona')).toBeNull()
    expect(store.personaId).toBe('')
  })

  it('test_persona_vmodel_direct_write_persists', () => {
    // 组件用 v-model 直写 personaId，不走 setPersona，也必须持久化
    const store = usePersonaStore()
    store.personaId = 'professional'
    expect(localStorage.getItem('assistmind:persona')).toBe('professional')
  })

  it('test_persona_stale_selection_falls_back_on_list_load', () => {
    const store = usePersonaStore()
    store.setPersona('removed-persona')
    store.setPersonas([{ id: 'gentle', name: '温柔客服' }])
    expect(store.personaId).toBe('')
  })

  it('test_persona_valid_selection_kept_on_list_load', () => {
    const store = usePersonaStore()
    store.setPersona('gentle')
    store.setPersonas([{ id: 'gentle', name: '温柔客服' }])
    expect(store.personaId).toBe('gentle')
    expect(store.loaded).toBe(true)
  })

  it('test_persona_set_personas_rejects_non_array', () => {
    const store = usePersonaStore()
    store.setPersonas(null)
    expect(store.personas).toEqual([])
    expect(store.loaded).toBe(true)
  })
})
