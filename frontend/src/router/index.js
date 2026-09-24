import { createRouter, createWebHistory } from 'vue-router'

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', component: () => import('@/App.vue') },
    { path: '/jev-guide', component: () => import('@/view/JevGuideView.vue') },
  ],
})
