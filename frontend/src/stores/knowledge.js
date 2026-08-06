import { defineStore } from 'pinia'
import { ref } from 'vue'

export const useKnowledgeStore = defineStore('knowledge', () => {
  const docs = ref([])
  const total = ref(0)

  return { docs, total }
})
