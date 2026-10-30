<template>
  <div class="tool-section">
    <div v-for="(tc, i) in toolCalls" :key="i" class="tool-item">
      <template v-if="isStaff">
        <el-tag size="small" type="warning">工具调用</el-tag>
        <span class="tool-name">调用 {{ toolLabel(tc.tool_name) }}</span>
        <span v-if="toolArgsText(tc)" class="tool-args">{{ toolArgsText(tc) }}</span>
      </template>

      <!-- 工具结果卡片（订单/物流/商品/售后）：白卡 + 左侧类型色条 -->
      <div v-if="cardOf(tc)" class="tool-result-card" :class="`card-type-${cardOf(tc).type}`">
        <!-- 订单卡片 -->
        <template v-if="cardOf(tc).type === 'order'">
          <el-descriptions :column="1" border size="small" class="card-desc">
            <el-descriptions-item label="订单号">
              {{ cardOf(tc).data.order_sn }}
            </el-descriptions-item>
            <el-descriptions-item label="状态">
              {{ cardOf(tc).data.status }}
            </el-descriptions-item>
            <el-descriptions-item label="实付金额">
              ¥{{ cardOf(tc).data.pay_amount }}
            </el-descriptions-item>
            <el-descriptions-item label="物流单号">
              {{ cardOf(tc).data.logistics_no || '未发货' }}
            </el-descriptions-item>
            <el-descriptions-item label="商品明细">
              <div v-for="(it, j) in cardOf(tc).data.items" :key="j">
                {{ it.name }}（{{ it.spec }}）×{{ it.quantity }} ¥{{ it.price }}
              </div>
            </el-descriptions-item>
          </el-descriptions>
        </template>

        <!-- 物流轨迹卡片 -->
        <template v-else-if="cardOf(tc).type === 'logistics'">
          <el-timeline class="logistics-timeline">
            <el-timeline-item
              v-for="(t, j) in cardOf(tc).data"
              :key="j"
              :timestamp="t.ts"
              size="small"
            >
              {{ t.content }}
            </el-timeline-item>
          </el-timeline>
        </template>

        <!-- 商品卡片 -->
        <template v-else-if="cardOf(tc).type === 'product'">
          <el-descriptions :column="1" border size="small" class="card-desc">
            <el-descriptions-item label="商品">{{ cardOf(tc).data.name }}</el-descriptions-item>
            <el-descriptions-item label="规格">{{ cardOf(tc).data.spec }}</el-descriptions-item>
            <el-descriptions-item label="价格">¥{{ cardOf(tc).data.price }}</el-descriptions-item>
            <el-descriptions-item label="库存">{{ cardOf(tc).data.stock }}</el-descriptions-item>
            <el-descriptions-item
              v-if="cardOf(tc).data.services && cardOf(tc).data.services.length"
              label="服务"
            >
              <el-tag
                v-for="(s, k) in cardOf(tc).data.services"
                :key="k"
                size="small"
                type="success"
                effect="plain"
                class="service-tag"
              >
                {{ s }}
              </el-tag>
            </el-descriptions-item>
          </el-descriptions>
        </template>

        <!-- 售后结果卡片 -->
        <template v-else-if="cardOf(tc).type === 'refund'">
          <el-descriptions :column="1" border size="small" class="card-desc">
            <el-descriptions-item label="售后单号">{{
              cardOf(tc).data.refund_id
            }}</el-descriptions-item>
            <el-descriptions-item label="状态">{{ cardOf(tc).data.status }}</el-descriptions-item>
            <el-descriptions-item label="说明">{{ cardOf(tc).data.message }}</el-descriptions-item>
          </el-descriptions>
        </template>
      </div>
    </div>
  </div>
</template>

<script setup>
/**
 * 工具调用过程与结果卡片。
 *
 * 从 views/Chat/index.vue 抽出：过程行（工具名/参数 JSON）是内部机制，仅 staff 可见；
 * 结果卡片（订单/物流/商品/售后）是业务结果，所有角色可见。
 * 解析逻辑在 composables/useToolCards.js，本组件只负责渲染。
 */
import { toolLabel, toolArgsText, cardOf } from '@/composables/useToolCards'

defineProps({
  toolCalls: { type: Array, required: true },
  isStaff: { type: Boolean, default: false },
})
</script>

<style scoped>
/* 工具调用 */
.tool-section {
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin: 6px 0;
}
.tool-item {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
  font-size: 13px;
}
.tool-name {
  font-weight: 600;
  color: var(--am-text);
}
.tool-args {
  color: var(--am-text-3);
  font-size: 12px;
  font-family: var(--am-font-mono);
  word-break: break-all;
}

/* 工具结果卡片：白卡 + 左侧类型色条（订单蓝 / 物流青 / 商品靛 / 退款玫红） */
.tool-result-card {
  position: relative;
  margin-top: 6px;
  padding: 10px 12px 10px 14px;
  background: #fff;
  border: 1px solid var(--am-line);
  border-radius: 8px;
  width: 100%;
  overflow: hidden;
}
.tool-result-card::before {
  content: '';
  position: absolute;
  left: 0;
  top: 8px;
  bottom: 8px;
  width: 3px;
  border-radius: 2px;
  background: var(--am-blue-600);
}
.tool-result-card.card-type-order::before {
  background: var(--am-blue-600);
}
.tool-result-card.card-type-logistics::before {
  background: var(--am-teal-600);
}
.tool-result-card.card-type-product::before {
  background: var(--am-indigo-600);
}
.tool-result-card.card-type-refund::before {
  background: var(--am-rose-600);
}
.card-desc {
  width: 100%;
}
.card-desc :deep(.el-descriptions__label) {
  color: var(--am-text-3);
  font-weight: 500;
  background: var(--am-paper);
}
.logistics-timeline {
  padding-left: 4px;
}
.service-tag {
  margin-right: 4px;
}
</style>
