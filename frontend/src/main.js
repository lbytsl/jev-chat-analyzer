import { createPinia } from 'pinia'
import { createApp, h } from 'vue'
import { RouterView } from 'vue-router'

import { router } from './router'
import './styles/main.css'
// 主题层必须排在基础样式之后：同一优先级下后者生效。
import './styles/glass.css'
import './styles/jev-guide.css'
import './styles/mobile.css'
import './styles/welcome.css'

createApp({ render: () => h(RouterView) }).use(createPinia()).use(router).mount('#app')
