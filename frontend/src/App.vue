<template>
  <div id="app">
    <nav class="top-nav">
      <router-link to="/pipeline" class="nav-link" active-class="nav-active">漫剧创作</router-link>
      <router-link to="/intelligence" class="nav-link" active-class="nav-active">情报看板</router-link>
    </nav>
    <router-view />
  </div>
</template>

<script setup>
</script>

<style>
body { margin: 0; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #f5f5f5; }
.top-nav { display: flex; gap: 0.5rem; padding: 0.8rem 1.5rem; background: #fff; border-bottom: 1px solid #e6e6e6; }
.nav-link { text-decoration: none; color: #666; font-weight: 600; padding: 0.4rem 0.9rem; border-radius: 6px; transition: all 0.15s; }
.nav-link:hover { background: #f0f0f0; color: #333; }
.nav-active { background: #4a90d9; color: #fff; }
.nav-active:hover { background: #4a90d9; color: #fff; }
</style>
