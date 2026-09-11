import { ref, computed, onMounted, onBeforeUnmount } from 'vue'

// 全站响应式断点（与 docs/frontend-responsive-design-spec.md P0-1 对齐）：
// ≥1024 桌面完整布局 / 768-1023 平板（侧栏折叠）/ <768 手机（抽屉导航）
export function useBreakpoint() {
  const width = ref(window.innerWidth)
  const onResize = () => {
    width.value = window.innerWidth
  }
  onMounted(() => window.addEventListener('resize', onResize))
  onBeforeUnmount(() => window.removeEventListener('resize', onResize))

  const isTablet = computed(() => width.value < 1024)
  const isMobile = computed(() => width.value < 768)

  return { width, isTablet, isMobile }
}
