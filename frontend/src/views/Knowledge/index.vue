<template>
  <div class="knowledge-page">
    <el-card shadow="never" class="panel-card">
      <template #header>
        <div class="card-header">
          <span class="card-title">知识库文档（共 {{ total }} 篇）</span>
          <el-button
            type="primary"
            :loading="rebuilding"
            @click="handleRebuild"
          >
            重建索引
          </el-button>
        </div>
      </template>

      <el-alert
        v-if="error"
        type="error"
        :closable="false"
        show-icon
        class="list-alert"
        :title="error"
      />

      <el-table
        v-loading="loading"
        :data="docs"
        border
        stripe
        style="width: 100%"
      >
        <el-table-column prop="doc_id" label="文档 ID" min-width="180" show-overflow-tooltip />
        <el-table-column prop="title" label="标题" min-width="200" show-overflow-tooltip />
        <el-table-column prop="source" label="来源" min-width="140" show-overflow-tooltip />
        <el-table-column label="分类" width="110">
          <template #default="{ row }">
            <el-tag v-if="row && row.category" size="small" type="info">
              {{ row.category }}
            </el-tag>
            <span v-else>-</span>
          </template>
        </el-table-column>
        <el-table-column prop="chunk_count" label="Chunk 数" width="100" />
        <el-table-column label="操作" width="110">
          <template #default="{ row }">
            <el-popconfirm
              :title="`确认删除文档「${row ? row.title : ''}」？删除后不可恢复`"
              @confirm="handleDelete(row)"
            >
              <template #reference>
                <el-button
                  type="danger"
                  link
                  :disabled="deletingDocId !== ''"
                  :loading="deletingDocId === (row ? row.doc_id : '')"
                >
                  删除
                </el-button>
              </template>
            </el-popconfirm>
          </template>
        </el-table-column>
      </el-table>

      <el-empty
        v-if="!loading && !error && docs.length === 0"
        description="知识库暂无文档"
        :image-size="80"
      />
    </el-card>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { listDocs, deleteDoc, rebuildIndex } from '@/api/knowledge'

const loading = ref(false)
const rebuilding = ref(false)
const deletingDocId = ref('')
const docs = ref([])
const total = ref(0)
const error = ref('')

async function loadDocs() {
  loading.value = true
  try {
    const data = await listDocs()
    docs.value = data.docs || []
    total.value = data.total || 0
    error.value = data.error || ''
  } catch (e) {
    // 错误已由 request 拦截器统一提示
  } finally {
    loading.value = false
  }
}

async function handleDelete(row) {
  if (!row || !row.doc_id) return
  deletingDocId.value = row.doc_id
  try {
    await deleteDoc(row.doc_id)
    ElMessage.success(`已删除文档 ${row.doc_id}`)
    await loadDocs()
  } catch (e) {
    // 错误已由 request 拦截器统一提示
  } finally {
    deletingDocId.value = ''
  }
}

async function handleRebuild() {
  rebuilding.value = true
  try {
    const data = await rebuildIndex()
    ElMessage.success(`索引重建完成，共 ${data.chunks || 0} 个 chunk`)
  } catch (e) {
    // 错误已由 request 拦截器统一提示
  } finally {
    rebuilding.value = false
  }
}

onMounted(loadDocs)
</script>

<style scoped>
.knowledge-page {
  padding: 16px;
}
.panel-card {
  margin-bottom: 16px;
}
.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.card-title {
  font-weight: 600;
}
.list-alert {
  margin-bottom: 12px;
}
</style>
