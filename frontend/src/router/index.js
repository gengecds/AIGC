import { createRouter, createWebHistory } from 'vue-router'
import PipelineView from '../views/PipelineView.vue'
import IntelligenceView from '../views/IntelligenceView.vue'

const routes = [
  { path: '/', redirect: '/pipeline' },
  { path: '/pipeline', name: 'pipeline', component: PipelineView },
  { path: '/intelligence', name: 'intelligence', component: IntelligenceView },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

export default router
