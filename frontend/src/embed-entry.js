/**
 * 情报站内嵌入口：在原生「创作控制台」主内容区挂载 Vue 情报站 + 管线向导
 * 由 index.html 顶栏「情报站」按钮打开，自带「返回创作控制台」工具条。
 * 对外暴露 window.__mountIntel / __unmountIntel，供 index.html 反复挂载/卸载。
 *
 * 注意：根组件用 render()（而非 template 字符串），因为当前 Vite 使用的 Vue 是
 * 运行时构建（不带模板编译器），内联 template 字符串无法在运行时编译。
 */
import { createApp, h } from 'vue'
import { createRouter, createWebHistory, RouterView, RouterLink } from 'vue-router'
import IntelligenceView from './views/IntelligenceView.vue'
import PipelineView from './views/PipelineView.vue'

let app = null

function makeRoot() {
  return {
    methods: {
      close() {
        if (window.__closeIntel) window.__closeIntel()
      },
    },
    render() {
      return h('div', { class: 'intel-embed' }, [
        h('div', { class: 'intel-toolbar' }, [
          h('button', { class: 'intel-back', onClick: () => this.close() }, '← 返回创作控制台'),
          h('nav', { class: 'intel-tabs' }, [
            h(RouterLink, { to: '/intelligence', class: 'intel-tab' }, () => '情报看板'),
            h(RouterLink, { to: '/pipeline', class: 'intel-tab' }, () => '管线创作'),
          ]),
        ]),
        h(RouterView),
      ])
    },
  }
}

window.__mountIntel = function () {
  if (app) {
    app.unmount()
    app = null
  }
  const router = createRouter({
    history: createWebHistory(),
    routes: [
      { path: '/', redirect: '/intelligence' },
      { path: '/intelligence', name: 'intelligence', component: IntelligenceView },
      { path: '/pipeline', name: 'pipeline', component: PipelineView },
    ],
  })
  app = createApp(makeRoot())
  app.use(router)
  // 保持当前路径（/intelligence 或 /pipeline）；否则默认进入情报看板
  const startPath = ['/intelligence', '/pipeline'].includes(location.pathname)
    ? location.pathname
    : '/intelligence'
  router.push(startPath)
  app.mount('#intel-app')
}

window.__unmountIntel = function () {
  if (app) {
    app.unmount()
    app = null
  }
}
