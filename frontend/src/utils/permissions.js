/** 权限工具函数。 */

/**
 * 检查当前角色是否有权限访问某路由。
 * @param {string} role - 当前用户角色
 * @param {string[]} allowedRoles - 允许的角色列表（空表示所有角色可访问）
 */
export function hasPermission(role, allowedRoles) {
  if (!allowedRoles || allowedRoles.length === 0) return true
  return allowedRoles.includes(role)
}
