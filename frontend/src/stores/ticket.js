import { defineStore } from 'pinia'
import { ref } from 'vue'

export const useTicketStore = defineStore('ticket', () => {
  const tickets = ref([])
  const total = ref(0)

  return { tickets, total }
})
