import { defineStore } from 'pinia'
import { ref } from 'vue'

export const useChatStore = defineStore('chat', () => {
  const messages = ref([])
  const loading = ref(false)

  function addMessage(msg) {
    messages.value.push(msg)
  }

  function clear() {
    messages.value = []
  }

  return { messages, loading, addMessage, clear }
})
