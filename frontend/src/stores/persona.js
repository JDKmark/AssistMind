import { defineStore } from 'pinia'
import { ref, watch } from 'vue'

const STORAGE_KEY = 'assistmind:persona'

// localStorage 不可用（隐私模式/被禁用）时降级为内存态，不影响聊天主链路
function loadStoredPersona() {
  try {
    return localStorage.getItem(STORAGE_KEY) || ''
  } catch {
    return ''
  }
}

// 客服人格（角色语气）选择状态：localStorage 持久化（刷新保持）；'' = 默认语气
export const usePersonaStore = defineStore('persona', () => {
  const personaId = ref(loadStoredPersona())
  const personas = ref([]) // [{id, name, description}]
  const loaded = ref(false)

  // 组件用 v-model 直写 personaId（不走 setPersona），须用 sync watcher 同步捕获所有写路径
  watch(
    personaId,
    (id) => {
      try {
        if (id) localStorage.setItem(STORAGE_KEY, id)
        else localStorage.removeItem(STORAGE_KEY)
      } catch (e) {
        console.warn('[Persona] 人格选择持久化失败', e)
      }
    },
    { flush: 'sync' }
  )

  function setPersona(id) {
    personaId.value = id || ''
  }

  function setPersonas(list) {
    personas.value = Array.isArray(list) ? list : []
    loaded.value = true
    // 持久化的选择可能已下架：回落默认，避免 UI 选中已不存在的人格
    if (personaId.value && !personas.value.some((p) => p.id === personaId.value)) {
      personaId.value = ''
    }
  }

  return { personaId, personas, loaded, setPersona, setPersonas }
})
