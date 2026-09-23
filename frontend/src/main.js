import { createPinia } from 'pinia'
import { createApp } from 'vue'

import App from './App.vue'
import './styles/main.css'
// 主题层必须排在基础样式之后：同一优先级下后者生效。
import './styles/glass.css'

createApp(App).use(createPinia()).mount('#app')
