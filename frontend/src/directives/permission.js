import { useAuthStore } from '@/stores/auth'

/** v-permission 指令：按角色控制元素显示。
 * 用法：v-permission="['admin']"
 */
export const permissionDirective = {
  mounted(el, binding) {
    const auth = useAuthStore()
    const allowed = binding.value || []
    if (allowed.length > 0 && !allowed.includes(auth.role)) {
      el.parentNode?.removeChild(el)
    }
  },
}
