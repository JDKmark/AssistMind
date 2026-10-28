/**
 * 时间展示格式化。
 *
 * 后端统一返回 ISO8601（UTC）字符串，页面只需要「YYYY-MM-DD HH:mm」用于列表与详情；
 * 不做时区换算（保持与后端记录一致，避免同一时刻在不同页面显示不一致）。
 */
export function fmtTime(iso) {
  if (!iso) return ''
  return String(iso).slice(0, 16).replace('T', ' ')
}
